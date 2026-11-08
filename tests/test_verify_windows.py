"""Run acceptance-script control flow under available Windows shells.

Read-only cases use the real installed CLI. Exercise cases replace only the
Invoke-Virtumon function body with a fake CLI; they never call a display driver.
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


FAKE_INVOKE_BODY = r"""{
    param([string[]]$Arguments)
    if (-not (Get-Variable -Name VirtumonFixture -Scope Script -ErrorAction SilentlyContinue)) {
        $script:VirtumonFixture = @{ Phase = 'before'; Name = ''; Retained = $false; LookupFailed = $false }
    }
    $state = $script:VirtumonFixture
    $scenario = $env:VIRTUMON_TEST_FIXTURE_CASE
    if ($scenario -notin @('rolled-back', 'orphan', 'retained', 'lookup-failure')) {
        throw "Unknown fake CLI scenario: $scenario"
    }
    Write-Host "[fake-cli] $($Arguments -join ' ')"
    $code = 0
    $stdout = ''
    $stderr = ''
    if ($Arguments[0] -eq '--version') {
        $stdout = 'virtumon test fixture'
    }
    elseif ($Arguments[0] -eq 'doctor') {
        $stdout = 'Test fixture: no native display driver is called.'
    }
    elseif ($Arguments[0] -eq 'create') {
        $state.Phase = 'after'
        $state.Name = $Arguments[1]
        $state.Retained = $scenario -in @('retained', 'lookup-failure')
        $code = 7  # Distinct first failure must survive cleanup and later errors.
        $stderr = "virtumon: simulated creation failure`n"
    }
    elseif ($Arguments[0] -eq 'list' -and $Arguments -contains '--managed') {
        if ($scenario -eq 'lookup-failure' -and $state.Phase -eq 'after' -and -not $state.LookupFailed) {
            $state.LookupFailed = $true
            $code = 9
            $stderr = "virtumon: simulated cleanup lookup failure`n"
        }
        elseif ($state.Retained) {
            $rows = @([ordered]@{ name = $state.Name; id = 'b42916a1-6bc7-4e5b-8144-ff9b59987d30'; status = 'error' })
            $stdout = ConvertTo-Json -InputObject $rows -Compress
        }
        else {
            $stdout = '[]'
        }
    }
    elseif ($Arguments[0] -eq 'list') {
        $rows = @([ordered]@{ device = '\\.\DISPLAY1'; x = 0; y = 0; width = 2560; height = 1600; primary = $true })
        if ($state.Phase -eq 'after' -and ($scenario -eq 'orphan' -or $state.Retained)) {
            $rows += [ordered]@{ device = '\\.\DISPLAY9'; x = 2560; y = 0; width = 1280; height = 720; primary = $false }
        }
        $stdout = ConvertTo-Json -InputObject $rows -Compress
    }
    elseif ($Arguments[0] -eq 'remove') {
        if ($Arguments[1] -ne $state.Name -or -not $state.Retained) {
            throw 'The script attempted to remove an unexpected fake monitor.'
        }
        $state.Retained = $false
        $stdout = ConvertTo-Json -InputObject @{ removed = @($state.Name); errors = @() } -Compress
    }
    else {
        throw "Unexpected fake CLI request: $($Arguments -join ' ')"
    }
    return [pscustomobject]@{ Arguments = $Arguments; ExitCode = $code; Stdout = $stdout; Stderr = $stderr }
}"""


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

    def run_exercise_fixture(self, shell, scenario):
        """Keep production cleanup flow, replacing its entire native-call boundary."""
        with tempfile.TemporaryDirectory(prefix="virtumon-cleanup-test-") as temporary:
            environment = os.environ.copy()
            environment.update(
                VIRTUMON_STATE_DIR=str(Path(temporary) / "state"),
                VIRTUMON_TEST_SCRIPT=str(self.script),
                VIRTUMON_TEST_PYTHON=sys.executable,
                VIRTUMON_TEST_FIXTURE_SCRIPT=str(Path(temporary) / "fixture.ps1"),
                VIRTUMON_TEST_FIXTURE_CASE=scenario,
                PYTHONIOENCODING="utf-8",
            )
            command = r"""
                $ErrorActionPreference = 'Stop'
                [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
                $tokens = $null
                $parseErrors = $null
                $ast = [Management.Automation.Language.Parser]::ParseFile(
                    $env:VIRTUMON_TEST_SCRIPT, [ref]$tokens, [ref]$parseErrors)
                if ($parseErrors.Count -ne 0) { throw 'The production script did not parse.' }
                $definitions = @($ast.FindAll({
                    param($node)
                    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                        $node.Name -eq 'Invoke-Virtumon'
                }, $true))
                if ($definitions.Count -ne 1) { throw 'Native-call boundary was not found uniquely.' }
                $extent = $definitions[0].Body.Extent
                $source = [IO.File]::ReadAllText($env:VIRTUMON_TEST_SCRIPT)
            """ + "\n$replacement = @'\n" + FAKE_INVOKE_BODY + "\n'@\n" + r"""
                $rewritten = $source.Remove($extent.StartOffset, $extent.EndOffset - $extent.StartOffset)
                $rewritten = $rewritten.Insert($extent.StartOffset, $replacement)
                [IO.File]::WriteAllText($env:VIRTUMON_TEST_FIXTURE_SCRIPT, $rewritten, [Text.UTF8Encoding]::new($true))
                & $env:VIRTUMON_TEST_FIXTURE_SCRIPT -Python $env:VIRTUMON_TEST_PYTHON -Exercise
                exit $LASTEXITCODE
            """
            return subprocess.run(
                [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=temporary, env=environment, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60, check=False,
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

    def test_failed_create_already_rolled_back_skips_redundant_removal(self):
        for name, shell in self.shells.items():
            with self.subTest(shell=name):
                result = self.run_exercise_fixture(shell, "rolled-back")
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 7, output)
                self.assertIn("Verification failed:", output)
                self.assertIn("already absent from the managed list", output)
                self.assertNotIn("[fake-cli] remove ", output)
                self.assertNotIn("Cleanup failed", output)
                self.assertIn("Cleanup verified;", output)
                self.assertNotIn("PASS:", output)

    def test_failed_create_with_unmanaged_display_does_not_verify_cleanup(self):
        for name, shell in self.shells.items():
            with self.subTest(shell=name):
                result = self.run_exercise_fixture(shell, "orphan")
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 7, output)
                self.assertNotIn("[fake-cli] remove ", output)
                self.assertIn("Post-cleanup check failed: Additional Windows displays remain", output)
                self.assertIn(r"\\.\DISPLAY9", output)
                self.assertIn("Their ownership is unverified; they were not modified.", output)
                self.assertNotIn("Cleanup verified;", output)
                self.assertNotIn("PASS:", output)

    def test_failed_create_with_retained_record_removes_only_that_name(self):
        for name, shell in self.shells.items():
            with self.subTest(shell=name):
                result = self.run_exercise_fixture(shell, "retained")
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 7, output)
                self.assertEqual(output.count("[fake-cli] remove virtumon-check-"), 1, output)
                self.assertNotIn("Cleanup failed", output)
                self.assertIn("Cleanup verified;", output)
                self.assertNotIn("PASS:", output)

    def test_failed_cleanup_lookup_still_attempts_targeted_removal(self):
        for name, shell in self.shells.items():
            with self.subTest(shell=name):
                result = self.run_exercise_fixture(shell, "lookup-failure")
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 7, output)
                self.assertIn("Could not inspect cleanup state", output)
                self.assertEqual(output.count("[fake-cli] remove virtumon-check-"), 1, output)
                self.assertIn("Cleanup verified;", output)
                self.assertNotIn("PASS:", output)


if __name__ == "__main__":
    unittest.main()
