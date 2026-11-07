"""Check the published SudoVDA ABI using fake Win32 functions, never real mutations."""

from __future__ import annotations

import ctypes
import struct
import sys
import unittest
import uuid

from virtumon.backends import sudovda as native


MONITOR_UUID = "00112233-4455-6677-8899-aabbccddeeff"


class FakeWin32:
    def __init__(self, *, paths=None, protocol=(0, 2, 1, 1)):
        self.paths = [r"\\?\root#sudomaker#sudovda"] if paths is None else paths
        self.error = 0
        self.protocol_bytes = bytes(protocol)
        self.calls = []
        self.closed = []
        self.destroyed = []
        self.opened = []
        self.failures = {}
        self.short_responses = {}
        self.detail_header_sizes = []
        self.identity = (0xF1234567, -7, 42)

    def get_last_error(self):
        return self.error

    def set_last_error(self, code):
        self.error = code

    def format_error(self, code):
        return f"fake Windows error {code}"

    def SetupDiGetClassDevsW(self, guid, _enumerator, _parent, flags):
        self.interface_guid = uuid.UUID(bytes_le=ctypes.string_at(guid, 16))
        self.interface_flags = flags
        return 100

    def SetupDiEnumDeviceInterfaces(self, _set, _device, _guid, index, output):
        if index >= len(self.paths):
            self.error = 259
            return 0
        data = ctypes.cast(output, ctypes.POINTER(native._DeviceInterfaceData)).contents
        self.interface_data_size = data.cbSize
        data.Reserved = index
        return 1

    def SetupDiGetDeviceInterfaceDetailW(self, _set, interface, detail, size, required, _device):
        index = ctypes.cast(interface, ctypes.POINTER(native._DeviceInterfaceData)).contents.Reserved
        encoded = (self.paths[index] + "\0").encode("utf-16-le")
        ctypes.cast(required, ctypes.POINTER(ctypes.c_uint32)).contents.value = len(encoded) + 4
        if detail is None:
            self.error = 122
            return 0
        self.detail_header_sizes.append(struct.unpack("<I", ctypes.string_at(detail, 4))[0])
        if size < len(encoded) + 4:
            self.error = 122
            return 0
        ctypes.memmove(ctypes.addressof(detail) + 4, encoded, len(encoded))
        return 1

    def SetupDiDestroyDeviceInfoList(self, handle):
        self.destroyed.append(handle)
        return 1

    def CreateFileW(self, path, access, share, _security, creation, flags, _template):
        self.opened.append((path, access, share, creation, flags))
        return 200

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1

    def DeviceIoControl(self, handle, code, input_buffer, input_size, output_buffer, output_size, returned, overlapped):
        payload = ctypes.string_at(input_buffer, input_size) if input_size else b""
        self.calls.append((handle, code, payload, output_size, overlapped))
        if code in self.failures:
            self.error = self.failures[code]
            return 0
        response = {
            native.IOCTL_GET_PROTOCOL_VERSION: self.protocol_bytes,
            native.IOCTL_GET_WATCHDOG: struct.pack("<II", 3, 2),
            native.IOCTL_ADD_VIRTUAL_DISPLAY: struct.pack("<IiI", *self.identity),
        }.get(code, b"")
        response = self.short_responses.get(code, response)
        if response and output_size:
            ctypes.memmove(output_buffer, response, min(output_size, len(response)))
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32)).contents.value = len(response)
        return 1


class SudoVDAAbiTests(unittest.TestCase):
    def test_wire_structure_sizes_and_offsets_match_c_header(self):
        expected_sizes = {
            native._GUID: 16, native._LUID: 8, native._AddParams: 56,
            native._RemoveParams: 16, native._AddOutput: 12,
            native._WatchdogOutput: 8, native._ProtocolOutput: 4,
        }
        for kind, size in expected_sizes.items():
            with self.subTest(structure=kind.__name__):
                self.assertEqual(ctypes.sizeof(kind), size)
        self.assertEqual(native._AddParams.MonitorGuid.offset, 12)
        self.assertEqual(native._AddParams.DeviceName.offset, 28)
        self.assertEqual(native._AddParams.SerialNumber.offset, 42)
        self.assertEqual(native._AddOutput.TargetId.offset, 8)

    def test_open_enumerates_only_sudovda_and_uses_synchronous_shared_handle(self):
        api = FakeWin32(paths=[r"\\?\root#sudovda#monitör"])
        with native.SudoVDADriver(_api=api) as driver:
            self.assertEqual(str(api.interface_guid), native.DRIVER_INTERFACE_GUID)
            self.assertEqual(api.interface_flags, 0x12)
            self.assertEqual(driver.device_path, api.paths[0])
            self.assertEqual(api.opened, [(api.paths[0], 0xC0000000, 3, 3, 0x80)])
            self.assertEqual(api.detail_header_sizes, [8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6])
            self.assertEqual(api.interface_data_size, 32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
            self.assertEqual(driver.protocol(), (0, 2, 1, True))
        self.assertEqual(api.destroyed, [100])
        self.assertEqual(api.closed, [200])
        self.assertIsNone(driver.device_path)
        driver.close()
        self.assertEqual(api.closed, [200])

    def test_add_wire_payload_has_exact_guid_mode_and_stable_serial(self):
        api = FakeWin32()
        with native.SudoVDADriver(_api=api) as driver:
            result = driver.add(MONITOR_UUID, 1920, 1080, 59940, "work")
            self.assertEqual(result, native.NativeIdentity(*api.identity))
            expected = (
                struct.pack("<III", 1920, 1080, 59940)
                + uuid.UUID(MONITOR_UUID).bytes_le
                + b"work".ljust(14, b"\0")
                + b"0011223344556\0"
            )
            self.assertEqual(api.calls[-1], (200, 0x222000, expected, 12, None))
            # A repeated UUID issues only ADD, never an implicit remove/resize.
            driver.add(MONITOR_UUID, 1280, 720, 60000, "work")
            self.assertFalse(any(call[1] == 0x222004 for call in api.calls))

    def test_edid_strings_are_printable_ascii_and_null_terminated(self):
        api = FakeWin32()
        with native.SudoVDADriver(_api=api) as driver:
            driver.add(MONITOR_UUID, 1920, 1080, 60000, "work\n\u2603-long-name-over-thirteen")
        payload = api.calls[-1][2]
        self.assertEqual(payload[28:42], b"work__-long-n\0")
        self.assertEqual(payload[42:56], b"0011223344556\0")

    def test_remove_targets_only_guid_and_absence_is_distinct_from_failure(self):
        api = FakeWin32()
        with native.SudoVDADriver(_api=api) as driver:
            self.assertTrue(driver.remove(MONITOR_UUID))
            self.assertEqual(api.calls[-1], (200, 0x222004, uuid.UUID(MONITOR_UUID).bytes_le, 0, None))
            api.failures[0x222004] = 1168
            self.assertFalse(driver.remove(MONITOR_UUID))
            api.failures[0x222004] = 5
            with self.assertRaises(native.DriverError) as caught:
                driver.remove(MONITOR_UUID)
            self.assertEqual(caught.exception.winerror, 5)

    def test_watchdog_and_ping_use_distinct_ioctl_and_buffers(self):
        api = FakeWin32()
        with native.SudoVDADriver(_api=api) as driver:
            self.assertEqual(driver.watchdog(), (3, 2))
            self.assertEqual(api.calls[-1], (200, 0x22200C, b"", 8, None))
            driver.ping()
            self.assertEqual(api.calls[-1], (200, 0x222220, b"", 0, None))
        # Handle closure must not accidentally remove another application's displays.
        self.assertNotIn(0x222004, [call[1] for call in api.calls])

    def test_incompatible_protocol_closes_handle(self):
        for protocol in ((0, 1, 99, 0), (1, 2, 1, 0)):
            with self.subTest(protocol=protocol):
                api = FakeWin32(protocol=protocol)
                driver = native.SudoVDADriver(_api=api)
                with self.assertRaises(native.DriverProtocolError):
                    driver.open()
                self.assertEqual(api.closed, [200])
                self.assertIsNone(driver.device_path)
        with native.SudoVDADriver(_api=FakeWin32(protocol=(0, 3, 0, 0))) as driver:
            self.assertEqual(driver.protocol(), (0, 3, 0, False))

    def test_short_protocol_or_identity_response_fails_closed(self):
        api = FakeWin32()
        api.short_responses[0x2223FC] = b"\0\2"
        with self.assertRaises(native.DriverProtocolError):
            native.SudoVDADriver(_api=api).open()
        self.assertEqual(api.closed, [200])
        api = FakeWin32()
        with native.SudoVDADriver(_api=api) as driver:
            api.short_responses[0x222000] = b"\0" * 8
            with self.assertRaises(native.DriverProtocolError):
                driver.add(MONITOR_UUID, 1920, 1080, 60000, "work")

    def test_missing_driver_releases_enumeration_handle(self):
        api = FakeWin32(paths=[])
        with self.assertRaises(native.DriverUnavailableError):
            native.SudoVDADriver(_api=api).open()
        self.assertEqual(api.destroyed, [100])
        self.assertEqual(api.opened, [])

    def test_multiple_driver_interfaces_are_rejected_before_open_or_ioctl(self):
        api = FakeWin32(paths=[r"\\?\root#sudovda#one", r"\\?\root#sudovda#two"])
        driver = native.SudoVDADriver(_api=api)
        with self.assertRaisesRegex(native.DriverUnavailableError, "exactly one"):
            driver.open()
        self.assertEqual(api.destroyed, [100])
        self.assertEqual(api.opened, [])
        self.assertEqual(api.calls, [])
        self.assertEqual(api.closed, [])
        self.assertIsNone(driver.device_path)

    def test_invalid_parameters_do_not_send_add(self):
        api = FakeWin32()
        with native.SudoVDADriver(_api=api) as driver:
            for width, height, refresh in ((0, 1080, 60000), (1920, -1, 60000),
                                           (1920, 1080, 60), (2**32, 1080, 60000),
                                           (True, 1080, 60000)):
                with self.subTest(values=(width, height, refresh)), self.assertRaises(ValueError):
                    driver.add(MONITOR_UUID, width, height, refresh, "work")
            with self.assertRaises(ValueError):
                driver.add("not-a-uuid", 1920, 1080, 60000, "work")
        self.assertNotIn(0x222000, [call[1] for call in api.calls])

    @unittest.skipIf(sys.platform == "win32", "non-Windows import/availability check")
    def test_non_windows_open_reports_unavailable_without_loading_libraries(self):
        with self.assertRaisesRegex(native.DriverUnavailableError, "Windows only"):
            native.SudoVDADriver().open()

    @unittest.skipUnless(sys.platform == "win32", "read-only native Windows probe")
    def test_windows_native_probe_does_not_create_or_remove_displays(self):
        driver = native.SudoVDADriver()
        try:
            driver.open()
        except native.DriverUnavailableError as exc:
            self.skipTest(str(exc))  # CI normally has no SudoVDA installed.
        try:
            self.assertEqual(driver.protocol()[0], 0)
            timeout, countdown = driver.watchdog()
            self.assertGreaterEqual(timeout, 0)
            self.assertGreaterEqual(countdown, 0)
        finally:
            driver.close()


if __name__ == "__main__":
    unittest.main()
