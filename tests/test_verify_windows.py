"""Run the actual read-only acceptance script under available Windows shells.

These tests never request -Exercise or require a virtual display driver.
The multi-command fixture reproduces a resolver returning several applications;
all four verification commands still run through the real installed Python.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid


@unittest.skipUnless(sys.platform == "win32", "requires Windows PowerShell/PowerShell and display discovery")
class WindowsVerificationScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = Path(__file__).resolve().parents[1] / "tools" / "verify_windows.ps1"
        cls.shells = {name: path for name in ("powershell", "pwsh") if (path := shutil.which(name))}
        if not cls.shells:
            raise unittest.SkipTest("No Windows PowerShell host is available")

    def run_script(self, shell, python_argument, *, multiple_applications=False):
        with tempfile.TemporaryDirectory(prefix="virtumon-script-test-") as temporary:
            environment = os.environ.copy()
            environment.update(
                VIRTUMON_STATE_DIR=str(Path(temporary) / "state"),
                VIRTUMON_TEST_SCRIPT=str(self.script),
                VIRTUMON_TEST_PYTHON=sys.executable,
                VIRTUMON_TEST_PYTHON_ARGUMENT=python_argument,
                PYTHONIOENCODING="utf-8",
            )
            fixture = ""
            if multiple_applications:
                fixture = r"""
                function Get-Command {
                    [CmdletBinding()]
                    param([string]$Name, [object]$CommandType)
                    if ($Name -ne 'virtumon-test-python') {
                        throw "Unexpected resolver request: $Name"
                    }
                    [pscustomobject]@{
                        Path = $env:VIRTUMON_TEST_PYTHON
                        Source = $env:VIRTUMON_TEST_PYTHON
                    }
                    [pscustomobject]@{
                        Path = 'C:\Virtumon non-winning Python\python.exe'
                        Source = 'C:\Virtumon non-winning Python\python.exe'
                    }
                }
                """
            command = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n" + fixture + r"""
                & $env:VIRTUMON_TEST_SCRIPT -Python $env:VIRTUMON_TEST_PYTHON_ARGUMENT
                exit $LASTEXITCODE
            """
            return subprocess.run(
                [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=temporary, env=environment, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=90, check=False,
            )

    def assert_read_only_checks_completed(self, result):
        output = result.stdout + result.stderr
        self.assertIn(result.returncode, (0, 1), output)
        interpreters = [line.removeprefix("Interpreter: ") for line in result.stdout.splitlines()
                        if line.startswith("Interpreter: ")]
        self.assertEqual(len(interpreters), 1, output)
        self.assertEqual(os.path.normcase(os.path.normpath(interpreters[0])),
                         os.path.normcase(os.path.normpath(sys.executable)), output)
        self.assertNotIn("Verification failed:", output)
        self.assertNotIn("non-winning Python", output)
        self.assertNotIn("Temporary monitor name:", output)
        self.assertNotIn("Cleaning up only:", output)
        for arguments in ("--version", "doctor", "list --json", "list --managed --json"):
            self.assertIn("> python -m virtumon " + arguments, result.stdout, output)
        # A missing driver is a legitimate readiness failure, and must survive
        # the wrapper. This script must not turn doctor exit 1 into a false PASS.
        if "Virtual monitor creation: unavailable" in result.stdout:
            self.assertEqual(result.returncode, 1, output)
            self.assertNotIn("PASS:", result.stdout)
        else:
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("PASS: Read-only checks completed.", result.stdout)

    def test_multiple_discovered_applications_use_only_first_interpreter(self):
        for name, shell in self.shells.items():
            with self.subTest(shell=name):
                result = self.run_script(shell, "virtumon-test-python", multiple_applications=True)
                self.assert_read_only_checks_completed(result)

    def test_explicit_interpreter_path_runs_all_read_only_checks(self):
        for name, shell in self.shells.items():
            with self.subTest(shell=name):
                result = self.run_script(shell, sys.executable)
                self.assert_read_only_checks_completed(result)

    def test_missing_interpreter_reports_failure_before_launching(self):
        for name, shell in self.shells.items():
            with self.subTest(shell=name):
                result = self.run_script(shell, "virtumon-missing-python-" + uuid.uuid4().hex)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn("Verification failed:", output)
                self.assertNotIn("Interpreter:", result.stdout)
                self.assertNotIn("> python -m virtumon", result.stdout)


if __name__ == "__main__":
    unittest.main()
