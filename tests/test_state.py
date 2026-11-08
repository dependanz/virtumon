"""State must retain ownership across failed writes and reject corrupt journals."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from virtumon.state import StateError, StateStore, file_lock


MONITOR_ID = "a0000000-0000-4000-8000-000000000001"
SECOND_ID = "b0000000-0000-4000-8000-000000000002"
SESSION_ID = "c0000000-0000-4000-8000-000000000003"


def valid_record(**changes):
    result = dict(name="work", id=MONITOR_ID, backend="sudovda", width=1920,
                  height=1080, refresh_hz=60, identity=None,
                  session_id=SESSION_ID, generation=0)
    result.update(changes)
    return result


class OwnershipStateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="virtumon-state-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.store = StateStore(self.directory)

    def write_document(self, document):
        self.store.path.write_text(json.dumps(document), encoding="utf-8")

    def assert_rejected_record(self, item):
        self.write_document({"schema": 1, "monitors": [item]})
        with self.assertRaises(StateError):
            self.store.load()

    def test_missing_state_is_empty_without_creating_a_file(self):
        self.assertEqual(self.store.load(), [])
        self.assertFalse(self.store.path.exists())

    def test_write_ahead_record_and_complete_identity_round_trip(self):
        pending = valid_record()
        self.store.save([pending])
        self.assertEqual(self.store.load(), [pending])
        complete = valid_record(identity={"adapter_low": 0xFFFFFFFF, "adapter_high": -2,
                                          "target_id": 0xFFFFFFFE})
        complete["last_observed"] = {"device": "DISPLAY42", "x": -1920, "y": 400}
        self.store.save([complete])
        self.assertEqual(self.store.load(), [complete])
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_incomplete_record_may_omit_session_and_generation(self):
        pending = valid_record()
        del pending["session_id"]
        del pending["generation"]
        self.write_document({"schema": 1, "monitors": [pending]})
        self.assertEqual(self.store.load(), [pending])

    def test_truncated_json_and_invalid_utf8_fail_closed(self):
        for raw in (b'{"schema":1,"monitors":[', b'\xff\xfeinvalid', b'{not-json}'):
            with self.subTest(raw=raw):
                self.store.path.write_bytes(raw)
                with self.assertRaises(StateError):
                    self.store.load()
                self.assertEqual(self.store.path.read_bytes(), raw)

    def test_invalid_document_shapes_and_schema_types_are_rejected(self):
        documents = [None, [], "not an object", {},
                     {"schema": 2, "monitors": []},
                     {"schema": True, "monitors": []},
                     {"schema": 1, "monitors": {}},
                     {"schema": 1, "monitors": [None]}]
        for document in documents:
            with self.subTest(document=document):
                self.write_document(document)
                with self.assertRaises(StateError):
                    self.store.load()

    def test_ownership_uuid_must_be_canonical_uuid4(self):
        invalid = [None, 42, "not-a-uuid", "00000000-0000-0000-0000-000000000000",
                   "a0000000-0000-5000-8000-000000000001", MONITOR_ID.upper(),
                   "{" + MONITOR_ID + "}"]
        for monitor_id in invalid:
            with self.subTest(monitor_id=monitor_id):
                self.assert_rejected_record(valid_record(id=monitor_id))

    def test_record_name_must_be_a_valid_nonreserved_cli_name(self):
        for name in (None, 1, "", " ", "primary", "PRIMARY", "work room", "-work", "w" * 65):
            with self.subTest(name=name):
                self.assert_rejected_record(valid_record(name=name))

    def test_duplicate_names_or_uuid_ownership_are_rejected(self):
        for second in (valid_record(name="WORK", id=SECOND_ID), valid_record(name="chat")):
            with self.subTest(second=second):
                self.write_document({"schema": 1, "monitors": [valid_record(), second]})
                with self.assertRaises(StateError):
                    self.store.load()

    def test_missing_fields_bad_backend_and_bad_dimensions_are_rejected(self):
        for field in ("name", "id", "backend", "width", "height", "refresh_hz"):
            item = valid_record()
            del item[field]
            with self.subTest(missing=field):
                self.assert_rejected_record(item)
        self.assert_rejected_record(valid_record(backend="unknown-driver"))
        for field in ("width", "height", "refresh_hz"):
            for value in (0, -1, True, 1.5, "1920"):
                with self.subTest(field=field, value=value):
                    self.assert_rejected_record(valid_record(**{field: value}))

    def test_native_identity_rejects_partial_bool_and_out_of_range_fields(self):
        good = {"adapter_low": 7, "adapter_high": -1, "target_id": 4}
        invalid = [[], {}, {**good, "extra": 1}]
        for field, values in {
            "adapter_low": (True, -1, 1 << 32),
            "adapter_high": (False, -(1 << 31) - 1, 1 << 31),
            "target_id": (True, -1, 1 << 32, 1.0),
        }.items():
            invalid.extend({**good, field: value} for value in values)
        for identity in invalid:
            with self.subTest(identity=identity):
                self.assert_rejected_record(valid_record(identity=identity))

    def test_present_session_and_generation_fields_cannot_masquerade_as_current(self):
        for session in (None, 1, False, "not-a-session-uuid"):
            with self.subTest(session=session):
                self.assert_rejected_record(valid_record(session_id=session))
        for generation in (None, -1, True, False, 0.0, "0"):
            with self.subTest(generation=generation):
                self.assert_rejected_record(valid_record(generation=generation))

    def test_invalid_driver_paths_are_rejected_but_missing_or_none_remains_valid(self):
        for path in ("", 1, False, [], {}, "bad\0path", "x" * 4097):
            with self.subTest(driver_path=repr(path)[:80]):
                self.assert_rejected_record(valid_record(driver_path=path))
        for item in (valid_record(), valid_record(driver_path=None),
                     valid_record(driver_path=r"\\?\sudovda-test")):
            with self.subTest(allowed_driver_path=item.get("driver_path")):
                self.write_document({"schema": 1, "monitors": [item]})
                self.assertEqual(self.store.load(), [item])

    def test_oversized_file_is_rejected_and_not_overwritten(self):
        raw = b" " * 1_048_577
        self.store.path.write_bytes(raw)
        with self.assertRaisesRegex(StateError, "large"):
            self.store.load()
        self.assertEqual(self.store.path.stat().st_size, len(raw))

    def test_failed_atomic_replace_keeps_previous_ownership_and_cleans_temporary(self):
        original = [valid_record()]
        self.store.save(original)
        previous = self.store.path.read_bytes()
        with patch("virtumon.state.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(StateError):
                self.store.save([])
        self.assertEqual(self.store.path.read_bytes(), previous)
        self.assertEqual(self.store.load(), original)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_windows_sharing_violation_retries_same_atomic_replace_and_succeeds(self):
        self.store.save([valid_record()])
        previous = self.store.path.read_bytes()
        replacement = [valid_record(name="chat", id=SECOND_ID)]
        real_replace = os.replace
        attempts = []
        sharing_violation = OSError("a Windows reader temporarily holds the old journal")
        sharing_violation.winerror = 32

        def transient_sharing_failure(source, destination):
            attempts.append((source, destination))
            if len(attempts) == 1:
                self.assertEqual(self.store.path.read_bytes(), previous)
                raise sharing_violation
            return real_replace(source, destination)

        with patch("virtumon.state.os.replace", side_effect=transient_sharing_failure):
            with patch("virtumon.state.time.monotonic", return_value=100.0):
                with patch("virtumon.state.time.sleep"):
                    self.store.save(replacement)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(self.store.load(), replacement)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_failed_serialization_keeps_previous_ownership(self):
        self.store.save([valid_record()])
        previous = self.store.path.read_bytes()
        for bad_value in (float("nan"), object()):
            item = copy.deepcopy(valid_record())
            item["error"] = bad_value
            with self.subTest(value=type(bad_value).__name__):
                with self.assertRaises(StateError):
                    self.store.save([item])
                self.assertEqual(self.store.path.read_bytes(), previous)
                self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_failed_fsync_never_replaces_previous_journal(self):
        self.store.save([valid_record()])
        previous = self.store.path.read_bytes()
        with patch("virtumon.state.os.fsync", side_effect=OSError("cannot flush journal")):
            with self.assertRaises(StateError):
                self.store.save([])
        self.assertEqual(self.store.path.read_bytes(), previous)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_cross_process_lock_refuses_a_second_writer_and_releases_after_exit(self):
        lock_path = self.directory / "agent.lock"
        child = textwrap.dedent("""\
            from pathlib import Path
            import sys
            from virtumon.state import StateError, file_lock
            try:
                with file_lock(Path(sys.argv[1]), timeout=0):
                    print("acquired")
            except StateError:
                print("locked")
            """)
        with file_lock(lock_path):
            blocked = subprocess.run([sys.executable, "-c", child, str(lock_path)],
                                     capture_output=True, text=True, timeout=15, check=False)
            self.assertEqual(blocked.returncode, 0, blocked.stderr)
            self.assertEqual(blocked.stdout.strip(), "locked")
        released = subprocess.run([sys.executable, "-c", child, str(lock_path)],
                                  capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertEqual(released.stdout.strip(), "acquired")


if __name__ == "__main__":
    unittest.main()
