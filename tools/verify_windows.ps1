#Requires -Version 5.1
<#
.SYNOPSIS
Read-only Windows checks, with an optional temporary-monitor exercise.
.DESCRIPTION
Uses the installed Virtumon module. -Exercise creates one unique monitor and
attempts to remove only that name in finally. It never removes all monitors,
installs a driver, changes certificate trust, or stops the background agent.
.EXAMPLE
.\tools\verify_windows.ps1 -Python .\.venv\Scripts\python.exe
.EXAMPLE
.\tools\verify_windows.ps1 -Python .\.venv\Scripts\python.exe -Exercise
#>
[CmdletBinding()]
param(
    [string]$Python = 'python',
    [switch]$Exercise
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:VerificationExitCode = 0
$script:PythonExecutable = $null
$testName = 'virtumon-check-' + [Guid]::NewGuid().ToString('N')
$createAttempted = $false
$created = $null
$baselineDisplays = @()
$baselineManaged = @()

function Set-FailureStatus {
    param([int]$Code = 1)
    if ($script:VerificationExitCode -eq 0) {
        $script:VerificationExitCode = $Code
    }
}

function Assert-Check {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

function Invoke-Virtumon {
    param([string[]]$Arguments)

    # These are only script-generated tokens, not user-provided command text.
    # ProcessStartInfo runs Python directly, without a command shell.
    $tokens = @('-m', 'virtumon') + $Arguments
    foreach ($token in $tokens) {
        if ($token -notmatch '^[A-Za-z0-9_.:-]+$') {
            throw "Unexpected command argument: $token"
        }
    }

    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $script:PythonExecutable
    $start.Arguments = $tokens -join ' '
    $start.WorkingDirectory = [IO.Path]::GetTempPath()
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.Encoding]::UTF8
    $start.StandardErrorEncoding = [Text.Encoding]::UTF8
    $start.EnvironmentVariables.Remove('PYTHONPATH')
    $start.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $start.EnvironmentVariables['NO_COLOR'] = '1'

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    try {
        [void]$process.Start()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(45000)) {
            $process.Kill()
            $process.WaitForExit()
            throw "Timed out running: python -m virtumon $($Arguments -join ' ')"
        }
        if (-not $stdoutTask.Wait(5000) -or -not $stderrTask.Wait(5000)) {
            throw 'The CLI exited but left an output stream open.'
        }
        return [pscustomobject]@{
            Arguments = $Arguments
            ExitCode = $process.ExitCode
            Stdout = $stdoutTask.GetAwaiter().GetResult()
            Stderr = $stderrTask.GetAwaiter().GetResult()
        }
    }
    finally {
        $process.Dispose()
    }
}

function Show-Result {
    param($Result)
    Write-Host "`n> python -m virtumon $($Result.Arguments -join ' ')"
    if ($Result.Stdout.Length -gt 0) {
        Write-Host $Result.Stdout.TrimEnd()
    }
    if ($Result.Stderr.Length -gt 0) {
        [Console]::Error.Write($Result.Stderr)
    }
    Write-Host "Exit status: $($Result.ExitCode)"
    if ($Result.ExitCode -ne 0) {
        Set-FailureStatus -Code $Result.ExitCode
    }
}

function Get-VirtumonJson {
    param(
        [string[]]$Arguments,
        [ValidateSet('Array', 'Object')][string]$Shape = 'Array',
        [switch]$Quiet
    )
    $result = Invoke-Virtumon -Arguments $Arguments
    if (-not $Quiet -or $result.ExitCode -ne 0) {
        Show-Result $result
    }
    if ($result.ExitCode -ne 0) {
        Set-FailureStatus -Code $result.ExitCode
        throw "Command failed: virtumon $($Arguments -join ' ')"
    }
    $text = $result.Stdout.Trim()
    $prefix = if ($Shape -eq 'Array') { '[' } else { '{' }
    Assert-Check ($text.StartsWith($prefix)) "Expected a JSON $Shape from virtumon."
    $value = ConvertFrom-Json -InputObject $text
    if ($Shape -eq 'Array') {
        # Keep [] as an empty array under both Windows PowerShell 5.1 and PS7.
        return ,@($value | Where-Object { $null -ne $_ })
    }
    return $value
}

function Assert-DisplaySchema {
    param([object[]]$Displays)
    foreach ($display in $Displays) {
        foreach ($field in @('device', 'x', 'y', 'width', 'height', 'primary')) {
            Assert-Check ($display.PSObject.Properties.Name -contains $field) "Display is missing '$field'."
        }
        Assert-Check ($display.device -is [string] -and $display.device.Length -gt 0) 'Display has no device name.'
        foreach ($field in @('x', 'y', 'width', 'height')) {
            $number = $display.$field
            Assert-Check ($number -is [int] -or $number -is [long]) "Display '$field' must be an integer."
        }
        Assert-Check ($display.width -gt 0 -and $display.height -gt 0) 'Display dimensions must be positive.'
        Assert-Check ($display.primary -is [bool]) 'Display primary flag must be Boolean.'
    }
}

function Assert-ExistingMonitorsPreserved {
    param([object[]]$Displays, [object[]]$Managed)
    foreach ($original in $baselineManaged) {
        $matchingRecords = @($Managed | Where-Object { $_.id -eq $original.id -and $_.name -eq $original.name })
        Assert-Check ($matchingRecords.Count -eq 1) "Pre-existing managed monitor disappeared: $($original.name)"
    }
    foreach ($original in $baselineDisplays) {
        $matchingDisplays = @($Displays | Where-Object { $_.device -eq $original.device })
        Assert-Check ($matchingDisplays.Count -eq 1) "Pre-existing Windows display disappeared: $($original.device)"
        foreach ($field in @('x', 'y', 'width', 'height', 'primary')) {
            Assert-Check ($matchingDisplays[0].$field -eq $original.$field) "Pre-existing display changed '$field': $($original.device)"
        }
    }
}

try {
    Assert-Check ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) 'Run this acceptance script on Windows.'
    # Application discovery can return several Python installations. Resolve a
    # single command before reading its path; an array becomes one invalid
    # space-joined ProcessStartInfo.FileName. Respect the first PATH match.
    $pythonCommand = Get-Command -Name $Python -CommandType Application -ErrorAction Stop |
        Select-Object -First 1
    $script:PythonExecutable = [string]$pythonCommand.Path
    Assert-Check (-not [string]::IsNullOrWhiteSpace($script:PythonExecutable)) 'Python did not resolve to an executable path.'
    Assert-Check ([IO.File]::Exists($script:PythonExecutable)) "Python executable does not exist: $script:PythonExecutable"
    Write-Host "Interpreter: $script:PythonExecutable"
    Show-Result (Invoke-Virtumon -Arguments @('--version'))
    Show-Result (Invoke-Virtumon -Arguments @('doctor'))
    $baselineDisplays = Get-VirtumonJson -Arguments @('list', '--json')
    Assert-DisplaySchema -Displays $baselineDisplays
    $baselineManaged = Get-VirtumonJson -Arguments @('list', '--managed', '--json')

    if ($Exercise) {
        Assert-Check ($script:VerificationExitCode -eq 0) 'Read-only checks failed; the exercise was not started.'
        Assert-Check (@($baselineManaged | Where-Object { $_.name -eq $testName }).Count -eq 0) 'Generated test name already exists.'
        Write-Host "`nTemporary monitor name: $testName"
        Write-Host 'Cleanup will target only this name.'
        # Set this before invoking create: a failed command may have partially succeeded.
        $createAttempted = $true
        $created = Get-VirtumonJson -Arguments @(
            'create', $testName, '--resolution', '1280x720', '--refresh', '60', '--json'
        ) -Shape Object
        Assert-Check ($created.name -eq $testName) 'Creation returned a different name.'
        [void][Guid]::Parse($created.id)
        Assert-Check ($created.backend -eq 'sudovda') 'Creation returned an unexpected backend.'
        Assert-Check ($created.status -eq 'active') 'Created monitor is not active.'

        Write-Host 'Waiting five seconds to exercise the background heartbeat...'
        Start-Sleep -Seconds 5
        $managed = Get-VirtumonJson -Arguments @('list', '--managed', '--json')
        $displays = Get-VirtumonJson -Arguments @('list', '--json')
        Assert-DisplaySchema -Displays $displays
        $matchingRecords = @($managed | Where-Object { $_.name -eq $testName -and $_.id -eq $created.id })
        Assert-Check ($matchingRecords.Count -eq 1) 'Created name/UUID is missing from the managed list.'
        $monitor = $matchingRecords[0]
        Assert-Check ($monitor.status -eq 'active') 'Monitor did not remain active past the watchdog interval.'
        Assert-Check ($monitor.device -is [string] -and $monitor.device.Length -gt 0) 'Managed monitor has no Windows device.'
        Assert-Check ($monitor.width -eq 1280 -and $monitor.height -eq 720 -and $monitor.refresh_hz -eq 60) 'Managed mode differs from the requested mode.'
        $osMatches = @($displays | Where-Object { $_.device -eq $monitor.device })
        Assert-Check ($osMatches.Count -eq 1) 'Managed monitor has no unique match in the Windows display list.'
        foreach ($field in @('x', 'y', 'width', 'height', 'primary')) {
            Assert-Check ($osMatches[0].$field -eq $monitor.$field) "Managed and Windows geometry differ at '$field'."
        }
        $created = $monitor
        Assert-ExistingMonitorsPreserved -Displays $displays -Managed $managed
        Write-Host 'Create/list/heartbeat checks passed.'
    }
}
catch {
    Set-FailureStatus
    [Console]::Error.WriteLine("Verification failed: $($_.Exception.Message)")
}
finally {
    if ($createAttempted) {
        $cleanupNeeded = $true
        try {
            $cleanupManaged = Get-VirtumonJson -Arguments @('list', '--managed', '--json') -Quiet
            $cleanupNeeded = @($cleanupManaged | Where-Object { $_.name -eq $testName }).Count -gt 0
        }
        catch {
            Set-FailureStatus
            [Console]::Error.WriteLine("Could not inspect cleanup state for ${testName}: $($_.Exception.Message). Attempting removal of only this name.")
        }

        if ($cleanupNeeded) {
            try {
                Write-Host "`nCleaning up only: $testName"
                $removed = Get-VirtumonJson -Arguments @('remove', $testName, '--json') -Shape Object
                Assert-Check (@($removed.errors).Count -eq 0) 'Cleanup reported a removal error.'
                Assert-Check (@($removed.removed) -contains $testName) 'Cleanup did not confirm removal of the test name.'
            }
            catch {
                Set-FailureStatus
                [Console]::Error.WriteLine("Cleanup failed for ${testName}: $($_.Exception.Message)")
            }
        }
        else {
            Write-Host "`nTemporary monitor is already absent from the managed list; verifying desktop cleanup."
        }

        try {
            $deadline = [DateTime]::UtcNow.AddSeconds(10)
            $baselineDeviceNames = @($baselineDisplays | ForEach-Object { $_.device })
            do {
                $managed = Get-VirtumonJson -Arguments @('list', '--managed', '--json') -Quiet
                $displays = Get-VirtumonJson -Arguments @('list', '--json') -Quiet
                $stillManaged = @($managed | Where-Object { $_.name -eq $testName }).Count -gt 0
                $stillVisible = $false
                if ($null -ne $created -and $created.device) {
                    $stillVisible = @($displays | Where-Object { $_.device -eq $created.device }).Count -gt 0
                }
                # Failed creation may return no identity, so an empty managed
                # list alone cannot prove that its native display was removed.
                $additionalDisplays = @($displays | Where-Object { $baselineDeviceNames -notcontains $_.device })
                if (-not $stillManaged -and -not $stillVisible -and $additionalDisplays.Count -eq 0) { break }
                Start-Sleep -Milliseconds 500
            } while ([DateTime]::UtcNow -lt $deadline)
            Assert-Check (-not $stillManaged) 'Test name remains in the managed list after cleanup.'
            Assert-Check (-not $stillVisible) 'Test display remains active in Windows after cleanup.'
            $additionalDeviceNames = @($additionalDisplays | ForEach-Object { $_.device }) -join ', '
            Assert-Check ($additionalDisplays.Count -eq 0) "Additional Windows displays remain after cleanup: $additionalDeviceNames. Their ownership is unverified; they were not modified."
            Assert-DisplaySchema -Displays $displays
            Assert-ExistingMonitorsPreserved -Displays $displays -Managed $managed
            Write-Host 'Cleanup verified; pre-existing displays and managed identities are preserved.'
        }
        catch {
            Set-FailureStatus
            [Console]::Error.WriteLine("Post-cleanup check failed: $($_.Exception.Message)")
        }
    }
}

if ($script:VerificationExitCode -eq 0) {
    if ($Exercise) {
        Write-Host "`nPASS: Windows monitor exercise completed."
    }
    else {
        Write-Host "`nPASS: Read-only checks completed. Use -Exercise to test creation and removal."
    }
}
exit $script:VerificationExitCode
