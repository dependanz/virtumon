"""Smoke-test an installed wheel: python -m unittest discover -s tests -v.

Run from a fresh working directory so the checkout cannot supply the package.
Windows tests enumerate the real desktop; an empty CI desktop is valid.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import unittest


class InstalledCliSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.version = importlib.metadata.version("virtumon")
        script_name = "virtumon.exe" if sys.platform == "win32" else "virtumon"
        console = Path(sysconfig.get_path("scripts")) / script_name
        if not console.is_file():
            raise RuntimeError(f"Install the Virtumon wheel before testing: missing {console}")
        cls.entrypoints = {
            "console": [str(console)],
            "module": [sys.executable, "-m", "virtumon"],
        }
        cls.working_directory = tempfile.TemporaryDirectory(prefix="virtumon-smoke-")
        cls.addClassCleanup(cls.working_directory.cleanup)

    def run_cli(self, entrypoint: str, *args: str, no_color: bool = False):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("NO_COLOR", None)
        env.update(COLUMNS="80", LINES="24", TERM="xterm", PYTHONIOENCODING="utf-8")
        if no_color:
            env["NO_COLOR"] = "1"
        return subprocess.run(
            [*self.entrypoints[entrypoint], *args],
            cwd=self.working_directory.name,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )

    def assert_success(self, result) -> None:
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(result.stderr, "")

    def test_help(self) -> None:
        for entrypoint in self.entrypoints:
            with self.subTest(entrypoint=entrypoint):
                result = self.run_cli(entrypoint, "--help")
                self.assert_success(result)
                for token in ("usage:", "virtumon", "--version", "list", "doctor"):
                    self.assertIn(token, result.stdout)
                listing = self.run_cli(entrypoint, "list", "--help")
                self.assert_success(listing)
                self.assertIn("--json", listing.stdout)

    def test_version_matches_installed_metadata(self) -> None:
        for entrypoint in self.entrypoints:
            with self.subTest(entrypoint=entrypoint):
                result = self.run_cli(entrypoint, "--version")
                self.assert_success(result)
                self.assertEqual(result.stdout.strip(), f"virtumon {self.version}")

    def test_banner_is_plain_when_redirected_or_no_color(self) -> None:
        for entrypoint in self.entrypoints:
            for no_color in (False, True):
                with self.subTest(entrypoint=entrypoint, no_color=no_color):
                    result = self.run_cli(entrypoint, no_color=no_color)
                    self.assert_success(result)
                    self.assertNotIn("\x1b", result.stdout)
                    self.assertIn(self.version, result.stdout)
                    self.assertIn("Your desktop, expanded.", result.stdout)
                    self.assertIn("Early development", result.stdout)
                    self.assertIn("virtumon list", result.stdout)
                    self.assertIn("virtumon doctor", result.stdout)

    def test_doctor(self) -> None:
        for entrypoint in self.entrypoints:
            with self.subTest(entrypoint=entrypoint):
                result = self.run_cli(entrypoint, "doctor")
                expected_code = 0 if sys.platform == "win32" else 2
                self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertIn(self.version, result.stdout)
                for label in ("Platform:", "Python:", "Display discovery:", "Virtual monitor creation:"):
                    self.assertIn(label, result.stdout)
                self.assertIn("not implemented", result.stdout.lower())
                if sys.platform == "win32":
                    self.assertIn("Display discovery: OK", result.stdout)
                else:
                    self.assertIn("unsupported", result.stdout.lower())

    def test_invalid_command(self) -> None:
        for entrypoint in self.entrypoints:
            with self.subTest(entrypoint=entrypoint):
                result = self.run_cli(entrypoint, "not-a-virtumon-command")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("invalid choice", result.stderr.lower())
                self.assertIn("not-a-virtumon-command", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    @unittest.skipUnless(sys.platform == "win32", "requires real Windows display APIs")
    def test_windows_list_json_enumerates_real_desktop(self) -> None:
        required_fields = {"device", "x", "y", "width", "height", "primary"}
        for entrypoint in self.entrypoints:
            with self.subTest(entrypoint=entrypoint):
                result = self.run_cli(entrypoint, "list", "--json")
                self.assert_success(result)
                displays = json.loads(result.stdout)
                self.assertIsInstance(displays, list)
                for display in displays:
                    self.assertIsInstance(display, dict)
                    self.assertTrue(required_fields.issubset(display), display)
                    self.assertIsInstance(display["device"], str)
                    self.assertTrue(display["device"])
                    for field in ("x", "y", "width", "height"):
                        self.assertIs(type(display[field]), int, (field, display))
                    self.assertGreater(display["width"], 0)
                    self.assertGreater(display["height"], 0)
                    self.assertIs(type(display["primary"]), bool)

    @unittest.skipIf(sys.platform == "win32", "Windows display discovery is supported")
    def test_non_windows_listing_fails_clearly(self) -> None:
        for entrypoint in self.entrypoints:
            for args in (("list",), ("list", "--json")):
                with self.subTest(entrypoint=entrypoint, args=args):
                    result = self.run_cli(entrypoint, *args)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("Windows only", result.stderr)
                    self.assertIn("support is planned", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
