"""Ownership and recovery contracts, with a fake driver and fake Windows desktop."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import itertools
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from virtumon.backends.sudovda import NativeIdentity
from virtumon.managed import Health, MonitorManager, OperationError, offline_rows
from virtumon.state import StateError, StateStore


SESSION = "10000000-0000-4000-8000-000000000001"
OLD_SESSION = "20000000-0000-4000-8000-000000000002"
OWNED_ID = "30000000-0000-4000-8000-000000000003"
SECOND_ID = "40000000-0000-4000-8000-000000000004"
OTHER_APP_ID = "50000000-0000-4000-8000-000000000005"


@dataclass(frozen=True)
class Screen:
    adapter_low: int
    adapter_high: int
    target_id: int
    device: str
    x: int
    y: int
    width: int
    height: int
    refresh_hz: float = 60.0
    primary: bool = False


def key(display):
    return display.adapter_low, display.adapter_high, display.target_id


def request(name="work", **changes):
    result = dict(name=name, width=1920, height=1080, refresh_hz=60, side=None, anchor=None)
    result.update(changes)
    return result


def record(name="work", monitor_id=OWNED_ID, identity=None, **changes):
    result = dict(name=name, id=monitor_id, backend="sudovda", width=1920, height=1080,
                  refresh_hz=60, identity=asdict(identity) if identity else None,
                  session_id=SESSION, generation=0)
    result.update(changes)
    return result


class FakeLayout:
    def __init__(self, screens):
        self.screens = {key(screen): screen for screen in screens}
        self.configure_calls = []
        self.presence_checks = []
        self.configure_error = None
        self.before_configure = None
        self.after_configure = None
        self.return_override = None
        self.guards = []

    def list_native_displays(self):
        return list(self.screens.values())

    def configure_display(self, identity, width, height, refresh, x, y, *, guard=None):
        self.configure_calls.append((identity, width, height, refresh, x, y))
        self.guards.append(guard)
        if self.before_configure:
            self.before_configure()
        if guard is not None and not guard():
            raise OperationError("Display ownership became stale before layout mutation.")
        if self.configure_error:
            raise self.configure_error
        observed = Screen(identity.adapter_low, identity.adapter_high, identity.target_id,
                          f"DISPLAY-{identity.target_id}", x, y, width, height, refresh)
        self.screens[key(identity)] = observed
        if self.after_configure:
            self.after_configure(observed)
        return self.return_override(observed) if self.return_override else observed

    def is_display_present(self, identity):
        self.presence_checks.append(key(identity))
        return key(identity) in self.screens


class FakeDriverFactory:
    """Only per-GUID operations exist; there is deliberately no global clear."""

    def __init__(self, layout):
        self.layout = layout
        self.device_path = r"\\?\sudovda-test"
        self.native = {}
        self.add_calls = []
        self.remove_calls = []
        self.open_count = 0
        self.before_add = None
        self.after_add = None
        self.add_error = None
        self.remove_error = None
        self.remove_errors = {}
        self.keep_os_display_after_remove = False
        self.next_target = 40

    def __call__(self):
        factory = self

        class Driver:
            def __init__(self):
                self.device_path = factory.device_path

            def __enter__(self):
                factory.open_count += 1
                return self

            def __exit__(self, *_args):
                pass

            def add(self, monitor_id, width, height, refresh_millihz, name):
                factory.add_calls.append((monitor_id, width, height, refresh_millihz, name))
                if factory.before_add:
                    factory.before_add(monitor_id)
                identity = NativeIdentity(77, -2, factory.next_target)
                factory.next_target += 1
                factory.native[monitor_id] = identity
                factory.layout.screens[key(identity)] = Screen(
                    *key(identity), f"DISPLAY-{identity.target_id}", 0, 0, 640, 480,
                )
                if factory.add_error:
                    # The display can exist even when the caller loses the reply.
                    raise factory.add_error
                if factory.after_add:
                    factory.after_add(monitor_id, identity)
                return identity

            def remove(self, monitor_id):
                factory.remove_calls.append(monitor_id)
                error = factory.remove_errors.get(monitor_id, factory.remove_error)
                if error:
                    raise error
                identity = factory.native.pop(monitor_id, None)
                if identity is None:
                    return False
                if not factory.keep_os_display_after_remove:
                    factory.layout.screens.pop(key(identity), None)
                return True

        return Driver()


class ManagedMonitorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="virtumon-managed-")
        self.addCleanup(directory.cleanup)
        self.store = StateStore(Path(directory.name))
        self.physical = Screen(1, 0, 1, "PHYSICAL", 0, 0, 2560, 1600, primary=True)
        self.unrelated = Screen(2, 0, 1, "OTHER-APP", 2560, 400, 1280, 720)
        self.layout = FakeLayout([self.physical, self.unrelated])
        self.driver = FakeDriverFactory(self.layout)
        self.health = Health()
        self.health.timeout = 0  # Tests explicitly simulate outages, not wall-clock scheduling.
        self.health.succeed()
        self.manager = MonitorManager(self.store, self.driver, self.layout, self.health, SESSION)

    def seed(self, name="work", monitor_id=OWNED_ID, *, target=8, **changes):
        identity = NativeIdentity(77, -2, target)
        item = record(name, monitor_id, identity, **changes)
        self.store.save([*self.store.load(), item])
        self.driver.native[monitor_id] = identity
        self.layout.screens[key(identity)] = Screen(
            *key(identity), f"DISPLAY-{target}", 3840 + target * 1920, 400, 1920, 1080,
        )
        return item, identity

    def assert_unrelated_unchanged(self):
        self.assertEqual(self.layout.screens[key(self.physical)], self.physical)
        self.assertEqual(self.layout.screens[key(self.unrelated)], self.unrelated)

    def test_create_journals_uuid_before_native_mutation_and_then_verifies(self):
        journal_observations = []

        def inspect_journal(monitor_id):
            saved = self.store.load()
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["id"], monitor_id)
            self.assertEqual(saved[0]["name"], "work")
            self.assertIsNone(saved[0]["identity"])
            self.assertEqual(saved[0]["driver_path"], self.driver.device_path)
            self.assertEqual(uuid.UUID(monitor_id).version, 4)
            journal_observations.append(monitor_id)

        self.driver.before_add = inspect_journal
        row = self.manager.create(request())
        self.assertEqual(row["status"], "active")
        self.assertEqual((row["x"], row["y"]), (3840, 400))
        self.assertEqual((row["width"], row["height"], row["refresh_hz"]), (1920, 1080, 60))
        self.assertEqual(journal_observations, [row["id"]])
        self.assertEqual(self.driver.add_calls[0][1:], (1920, 1080, 60000, "work"))
        saved = self.store.load()[0]
        self.assertEqual(saved["identity"], asdict(self.driver.native[row["id"]]))
        self.assertEqual(saved["last_observed"]["device"], row["device"])
        self.assertEqual(len(self.layout.configure_calls), 1)
        self.assertEqual(key(self.layout.configure_calls[0][0]), key(self.driver.native[row["id"]]))
        self.assert_unrelated_unchanged()

    def test_journal_write_failure_prevents_native_creation(self):
        with patch.object(self.store, "save", side_effect=StateError("disk full")):
            with self.assertRaises(StateError):
                self.manager.create(request())
        self.assertEqual(self.driver.add_calls, [])
        self.assertEqual(self.driver.remove_calls, [])
        self.assertEqual(self.layout.configure_calls, [])
        self.assert_unrelated_unchanged()

    def test_lost_add_reply_is_cleaned_up_using_write_ahead_uuid(self):
        self.driver.add_error = RuntimeError("driver reply lost after creation")
        with self.assertRaises(OperationError):
            self.manager.create(request())
        monitor_id = self.driver.add_calls[0][0]
        self.assertEqual(self.driver.remove_calls, [monitor_id])
        self.assertNotIn(monitor_id, self.driver.native)
        self.assertEqual(self.store.load(), [])
        self.assert_unrelated_unchanged()

    def test_identity_save_failure_rolls_back_exact_created_guid(self):
        original_save = self.store.save
        failed = False

        def fail_identity_save(records):
            nonlocal failed
            if not failed and any(item.get("identity") is not None for item in records):
                failed = True
                raise StateError("cannot record native identity")
            original_save(records)

        with patch.object(self.store, "save", side_effect=fail_identity_save):
            with self.assertRaises(OperationError):
                self.manager.create(request())
        self.assertEqual(self.driver.remove_calls, [self.driver.add_calls[0][0]])
        self.assertEqual(self.store.load(), [])
        self.assertEqual(self.layout.configure_calls, [])
        self.assert_unrelated_unchanged()

    def test_layout_failure_rolls_back_only_new_monitor(self):
        self.layout.configure_error = RuntimeError("requested mode unavailable")
        with self.assertRaisesRegex(OperationError, "requested mode unavailable"):
            self.manager.create(request())
        self.assertEqual(self.driver.remove_calls, [self.driver.add_calls[0][0]])
        self.assertEqual(self.store.load(), [])
        self.assert_unrelated_unchanged()

    def test_rollback_refuses_a_different_driver_and_retains_original_ownership(self):
        original_path = self.driver.device_path
        self.layout.configure_error = RuntimeError("layout failed")
        self.driver.after_add = lambda _monitor_id, _identity: setattr(
            self.driver, "device_path", r"\\?\different-sudovda-device",
        )
        with self.assertRaisesRegex(OperationError, "cleanup failed"):
            self.manager.create(request())
        saved = self.store.load()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["id"], self.driver.add_calls[0][0])
        self.assertEqual(saved[0]["driver_path"], original_path)
        self.assertIn("different SudoVDA device", saved[0]["error"])
        self.assertEqual(self.driver.remove_calls, [])
        self.assertIn(saved[0]["id"], self.driver.native)
        self.assert_unrelated_unchanged()

    def test_failed_rollback_retains_guid_and_error_for_later_removal(self):
        self.layout.configure_error = RuntimeError("layout failed")
        self.driver.remove_error = RuntimeError("driver disconnected")
        with self.assertRaisesRegex(OperationError, "cleanup failed"):
            self.manager.create(request())
        saved = self.store.load()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["id"], self.driver.add_calls[0][0])
        self.assertIn("layout failed", saved[0]["error"])
        self.assertIn("driver disconnected", saved[0]["error"])
        self.assertEqual(self.manager.list_managed()[0]["status"], "error")
        self.driver.remove_error = None
        result = self.manager.remove(["work"])
        self.assertEqual(result, {"removed": ["work"], "errors": []})
        self.assertEqual(self.store.load(), [])
        self.assert_unrelated_unchanged()

    def test_unconfirmed_windows_departure_retains_ownership(self):
        self.layout.configure_error = RuntimeError("layout failed")
        self.driver.keep_os_display_after_remove = True
        ticks = itertools.count(0, 100)
        with patch("virtumon.managed.time.monotonic", side_effect=lambda: next(ticks)):
            with patch("virtumon.managed.time.sleep"):
                with self.assertRaisesRegex(OperationError, "Windows still reports"):
                    self.manager.create(request())
        saved = self.store.load()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["id"], self.driver.add_calls[0][0])
        self.assertIn("cleanup failed", saved[0]["error"])

    def test_final_snapshot_must_contain_created_target(self):
        self.layout.after_configure = lambda screen: self.layout.screens.pop(key(screen))
        with self.assertRaisesRegex(OperationError, "disappeared"):
            self.manager.create(request())
        self.assertEqual(self.driver.remove_calls, [self.driver.add_calls[0][0]])
        self.assertEqual(self.store.load(), [])

    def test_final_snapshot_mode_and_position_override_configure_return_value(self):
        for change in ({"width": 1280}, {"height": 720}, {"x": -500},
                       {"y": -300}, {"refresh_hz": 30.0}):
            with self.subTest(change=change):
                self.layout.after_configure = lambda screen, delta=change: self.layout.screens.__setitem__(
                    key(screen), replace(screen, **delta),
                )
                with self.assertRaises(OperationError):
                    self.manager.create(request())
                self.assertEqual(self.store.load(), [])
                self.assertEqual(self.driver.remove_calls[-1], self.driver.add_calls[-1][0])
                self.assert_unrelated_unchanged()

    def test_configure_returning_different_adapter_target_is_rejected(self):
        self.layout.return_override = lambda screen: replace(screen, adapter_low=999)
        with self.assertRaisesRegex(OperationError, "different display identity"):
            self.manager.create(request())
        self.assertEqual(self.store.load(), [])
        self.assertEqual(self.driver.remove_calls, [self.driver.add_calls[0][0]])

    def test_unrelated_display_change_is_detected_without_configuring_it(self):
        self.layout.after_configure = lambda _screen: self.layout.screens.__setitem__(
            key(self.unrelated), replace(self.unrelated, x=-1280),
        )
        with self.assertRaisesRegex(OperationError, "another display"):
            self.manager.create(request())
        self.assertEqual(self.store.load(), [])
        self.assertEqual(self.driver.remove_calls, [self.driver.add_calls[0][0]])
        self.assertTrue(all(key(call[0]) not in (key(self.physical), key(self.unrelated))
                            for call in self.layout.configure_calls))

    def test_heartbeat_outage_during_create_rolls_back(self):
        self.layout.after_configure = lambda _screen: self.health.fail("ping failed")
        with self.assertRaisesRegex(OperationError, "heartbeat"):
            self.manager.create(request())
        self.assertEqual(self.store.load(), [])
        self.assertEqual(self.driver.remove_calls, [self.driver.add_calls[0][0]])

    def test_watchdog_gap_during_add_never_configures_reused_target(self):
        clock = [100.0]
        self.health.timeout = 3
        self.health.last_success = 0
        replacement = []

        def expire_and_reuse_target(monitor_id, identity):
            # The driver's whole-second timer may expire just after two
            # seconds even though its configured timeout is three seconds.
            clock[0] = 102.0
            self.driver.native.pop(monitor_id)
            other = Screen(*key(identity), "REUSED-BY-OTHER-APP", -1280, 100, 1280, 720)
            self.layout.screens[key(identity)] = other
            replacement.append(other)
            self.health.succeed()  # Recovery must not make the old mapping current again.

        self.driver.after_add = expire_and_reuse_target
        with patch("virtumon.managed.time.monotonic", side_effect=lambda: clock[0]):
            self.health.succeed()
            with self.assertRaisesRegex(OperationError, "heartbeat"):
                self.manager.create(request())
        self.assertEqual(self.layout.configure_calls, [])
        self.assertEqual(self.driver.remove_calls, [self.driver.add_calls[0][0]])
        self.assertEqual(self.layout.screens[key(replacement[0])], replacement[0])
        self.assertEqual(self.store.load(), [])
        self.assert_unrelated_unchanged()

    def test_configuration_receives_guard_that_rechecks_generation(self):
        def fail_then_recover():
            self.health.fail("heartbeat interrupted before apply")
            self.health.succeed()

        self.layout.before_configure = fail_then_recover
        with self.assertRaisesRegex(OperationError, "stale"):
            self.manager.create(request())
        self.assertEqual(len(self.layout.guards), 1)
        self.assertTrue(callable(self.layout.guards[0]))
        self.assertFalse(self.layout.guards[0]())
        self.assertEqual(self.driver.remove_calls, [self.driver.add_calls[0][0]])
        self.assertEqual(self.store.load(), [])
        self.assert_unrelated_unchanged()

    def test_default_position_uses_rightmost_displays_actual_vertical_position(self):
        edge = replace(self.unrelated, x=4000, y=-900)
        self.layout.screens[key(edge)] = edge
        row = self.manager.create(request())
        self.assertEqual((row["x"], row["y"]), (5280, -900))

    def test_overlapping_explicit_placement_is_rejected_before_driver_mutation(self):
        with self.assertRaisesRegex(OperationError, "overlap"):
            self.manager.create(request(side="right", anchor="primary"))
        self.assertEqual(self.driver.add_calls, [])
        self.assertEqual(self.layout.configure_calls, [])
        self.assertEqual(self.store.load(), [])
        self.assert_unrelated_unchanged()

    def test_duplicate_name_is_case_insensitive_and_preserves_original_owner(self):
        original, _identity = self.seed()
        with self.assertRaisesRegex(OperationError, "already exists"):
            self.manager.create(request("WORK"))
        self.assertEqual(self.store.load(), [original])
        self.assertEqual(self.driver.add_calls, [])

    def test_multi_remove_preflights_every_name_before_any_native_mutation(self):
        self.seed()
        original = self.store.path.read_bytes()
        with self.assertRaisesRegex(OperationError, "Unknown"):
            self.manager.remove(["work", "typo"])
        self.assertEqual(self.driver.remove_calls, [])
        self.assertEqual(self.driver.open_count, 0)
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assert_unrelated_unchanged()

    def test_remove_validates_argument_types_before_any_mutation(self):
        self.seed()
        for names, all_monitors in ((["work", None], False), (["work", 1], False),
                                    ("work", False), ([""], False), (None, "yes")):
            with self.subTest(names=names, all_monitors=all_monitors):
                with self.assertRaises((ValueError, OperationError)):
                    self.manager.remove(names, all_monitors=all_monitors)
        self.assertEqual(self.driver.remove_calls, [])
        self.assertEqual([item["id"] for item in self.store.load()], [OWNED_ID])

    def test_named_removal_uses_exact_guid_and_deduplicates_names(self):
        self.seed()
        second, second_identity = self.seed("chat", SECOND_ID, target=9)
        result = self.manager.remove(["WORK", "work"])
        self.assertEqual(result, {"removed": ["work"], "errors": []})
        self.assertEqual(self.driver.remove_calls, [OWNED_ID])
        self.assertEqual(self.store.load(), [second])
        self.assertIn(key(second_identity), self.layout.screens)
        self.assert_unrelated_unchanged()

    def test_remove_refuses_a_different_driver_without_clearing_record(self):
        original, identity = self.seed(driver_path=self.driver.device_path)
        previous_bytes = self.store.path.read_bytes()
        self.driver.device_path = r"\\?\different-sudovda-device"
        result = self.manager.remove(["work"])
        self.assertEqual(result["removed"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["name"], "work")
        self.assertIn("different SudoVDA device", result["errors"][0]["error"])
        self.assertEqual(self.driver.remove_calls, [])
        self.assertEqual(self.store.load(), [original])
        self.assertEqual(self.store.path.read_bytes(), previous_bytes)
        self.assertIn(key(identity), self.layout.screens)
        self.assert_unrelated_unchanged()

    def test_all_removes_only_journaled_guids_and_keeps_other_app_monitor(self):
        self.seed()
        self.seed("chat", SECOND_ID, target=9)
        other_identity = NativeIdentity(*key(self.unrelated))
        self.driver.native[OTHER_APP_ID] = other_identity
        result = self.manager.remove(all_monitors=True)
        self.assertEqual(result, {"removed": ["work", "chat"], "errors": []})
        self.assertEqual(self.driver.remove_calls, [OWNED_ID, SECOND_ID])
        self.assertEqual(self.driver.native, {OTHER_APP_ID: other_identity})
        self.assertEqual(self.store.load(), [])
        self.assert_unrelated_unchanged()

    def test_all_with_names_is_rejected_without_removing_anything(self):
        self.seed()
        with self.assertRaises(OperationError):
            self.manager.remove(["work"], all_monitors=True)
        self.assertEqual(self.driver.remove_calls, [])
        self.assertEqual(len(self.store.load()), 1)

    def test_remove_failure_retains_that_record_while_reporting_partial_result(self):
        original, _identity = self.seed()
        self.seed("chat", SECOND_ID, target=9)
        self.driver.remove_errors[OWNED_ID] = RuntimeError("driver refused removal")
        result = self.manager.remove(["work", "chat"])
        self.assertEqual(result["removed"], ["chat"])
        self.assertEqual([error["name"] for error in result["errors"]], ["work"])
        self.assertEqual(self.store.load(), [original])
        self.assert_unrelated_unchanged()

    def test_old_session_reused_target_is_stale_and_never_polled_during_remove(self):
        stale_identity = NativeIdentity(*key(self.unrelated))
        self.store.save([record(identity=stale_identity, session_id=OLD_SESSION)])
        # The GUID remains removable even if the driver's current target has
        # changed and the saved target now belongs to an unrelated display.
        current_identity = NativeIdentity(77, -2, 99)
        self.driver.native[OWNED_ID] = current_identity
        self.layout.screens[key(current_identity)] = Screen(
            *key(current_identity), "CURRENT-OWNED", 3840, 400, 1920, 1080,
        )
        row = self.manager.list_managed()[0]
        self.assertEqual(row["status"], "stale")
        self.assertIsNone(row["device"])
        with patch.object(self.layout, "is_display_present", side_effect=AssertionError("stale target was polled")) as probe:
            result = self.manager.remove(["work"])
            probe.assert_not_called()
        self.assertEqual(result, {"removed": ["work"], "errors": []})
        self.assertEqual(self.driver.remove_calls, [OWNED_ID])
        self.assertNotIn(key(current_identity), self.layout.screens)
        self.assertEqual(self.layout.presence_checks, [])
        self.assert_unrelated_unchanged()

    def test_old_generation_stays_stale_after_heartbeat_recovers(self):
        self.store.save([record(identity=NativeIdentity(*key(self.unrelated)))])
        self.health.fail("heartbeat lost")
        first_generation = self.health.snapshot()["generation"]
        self.health.fail("heartbeat still lost")
        self.assertEqual(self.health.snapshot()["generation"], first_generation)
        self.health.succeed()
        row = self.manager.list_managed()[0]
        self.assertEqual(row["status"], "stale")
        self.assertIsNone(row["device"])
        self.assertEqual(self.driver.add_calls, [])
        self.assertEqual(self.driver.remove_calls, [])

    def test_same_target_number_on_different_adapter_is_not_an_owned_display(self):
        identity = NativeIdentity(999, 0, self.physical.target_id)
        self.store.save([record(identity=identity)])
        row = self.manager.list_managed()[0]
        self.assertEqual(row["status"], "missing")
        self.assertIsNone(row["device"])

    def test_offline_rows_never_promote_saved_native_identity_to_active(self):
        self.seed()
        rows = offline_rows(self.store)
        self.assertEqual(rows[0]["status"], "stale")
        self.assertIsNone(rows[0]["device"])
        self.assertIn("not running", rows[0]["error"])
        self.assertEqual(self.driver.open_count, 0)

    def test_corruption_after_agent_start_blocks_create_and_remove(self):
        self.store.path.write_text('{"schema":1,"monitors":[', encoding="utf-8")
        with self.assertRaises(StateError):
            self.manager.create(request())
        with self.assertRaises(StateError):
            self.manager.remove(all_monitors=True)
        self.assertEqual(self.driver.add_calls, [])
        self.assertEqual(self.driver.remove_calls, [])
        self.assertEqual(self.layout.configure_calls, [])
        self.assert_unrelated_unchanged()


class HealthBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.clock = 100.0
        timer = patch("virtumon.managed.time.monotonic", side_effect=lambda: self.clock)
        timer.start()
        self.addCleanup(timer.stop)
        self.health = Health()
        self.health.set_timeout(3)
        self.health.succeed()

    def test_snapshot_invalidates_at_earliest_watchdog_tick_boundary(self):
        self.clock = 101.999
        self.assertEqual(self.health.snapshot()["generation"], 0)
        self.assertIsNone(self.health.snapshot()["error"])
        self.clock = 102.0
        expired = self.health.snapshot()
        self.assertEqual(expired["generation"], 1)
        self.assertIsNotNone(expired["error"])
        self.clock = 102.25
        self.assertEqual(self.health.snapshot()["generation"], 1)
        self.health.succeed()
        self.assertEqual(self.health.snapshot()["generation"], 1)
        self.assertIsNone(self.health.snapshot()["error"])

    def test_success_after_gap_invalidates_without_an_intervening_snapshot(self):
        self.clock = 102.0
        self.health.succeed()
        observed = self.health.snapshot()
        self.assertEqual(observed["generation"], 1)
        self.assertIsNone(observed["error"])
        self.assertEqual(observed["last_success"], 102.0)

    def test_subthreshold_heartbeat_does_not_invalidate_generation(self):
        self.clock = 101.999
        self.health.succeed()
        self.clock = 103.998
        self.health.succeed()
        self.assertEqual(self.health.snapshot()["generation"], 0)
        self.assertIsNone(self.health.snapshot()["error"])

    def test_disabled_watchdog_does_not_expire_mappings_after_a_long_pause(self):
        self.health.set_timeout(0)
        self.clock = 1_000_000.0
        self.assertEqual(self.health.snapshot()["generation"], 0)
        self.assertIsNone(self.health.snapshot()["error"])
        self.health.succeed()
        self.assertEqual(self.health.snapshot()["generation"], 0)


if __name__ == "__main__":
    unittest.main()
