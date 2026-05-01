param(
  [string]$TaskName = "ScholarMind-Tunnel-Watchdog",
  [int]$IntervalMinutes = 1,
  [string]$PublicHealthUrl = "",
  [bool]$RunAsSystem = $true
)

$ErrorActionPreference = "Stop"

if ($IntervalMinutes -lt 1) {
  throw "IntervalMinutes must be >= 1."
}

$scriptDir = Split-Path -Parent $PSCommandPath
$watchdogScript = Join-Path $scriptDir "tunnel_watchdog.ps1"
$launcherScript = Join-Path $scriptDir "run_watchdog_hidden.vbs"
if (-not (Test-Path -Path $watchdogScript)) {
  throw "Watchdog script not found: $watchdogScript"
}
if (-not (Test-Path -Path $launcherScript)) {
  throw "Launcher script not found: $launcherScript"
}

$escapedLauncher = $launcherScript.Replace('"', '\"')
$taskCommand = "wscript.exe //B //NoLogo `"$escapedLauncher`""
if (-not [string]::IsNullOrWhiteSpace($PublicHealthUrl)) {
  $escapedPublicHealth = $PublicHealthUrl.Replace('"', '\"')
  $taskCommand += " -PublicHealthUrl `"$escapedPublicHealth`""
}

function Create-Task {
  param(
    [string]$TaskName,
    [string]$TaskCommand,
    [int]$IntervalMinutes,
    [bool]$AsSystem
  )
  $args = @(
    "/Create",
    "/TN", $TaskName,
    "/SC", "MINUTE",
    "/MO", [string]$IntervalMinutes,
    "/TR", $TaskCommand,
    "/F"
  )
  if ($AsSystem) {
    $args += @("/RU", "SYSTEM", "/RL", "HIGHEST")
  }
  $previousErrorAction = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $output = $null
  $exitCode = 1
  try {
    $output = & schtasks.exe $args 2>&1
    $exitCode = $LASTEXITCODE
  } catch {
    $output = $_.Exception.Message
    $exitCode = 1
  } finally {
    $ErrorActionPreference = $previousErrorAction
  }
  return @{
    ExitCode = $exitCode
    Output = $output
  }
}

$createResult = $null
$installMode = "current-user-hidden"

if ($RunAsSystem) {
  $createResult = Create-Task -TaskName $TaskName -TaskCommand $taskCommand -IntervalMinutes $IntervalMinutes -AsSystem $true
  if ($createResult.ExitCode -eq 0) {
    $installMode = "system-hidden"
  } else {
    Write-Warning "Create task as SYSTEM failed, fallback to current user. reason: $($createResult.Output)"
    $createResult = Create-Task -TaskName $TaskName -TaskCommand $taskCommand -IntervalMinutes $IntervalMinutes -AsSystem $false
  }
} else {
  $createResult = Create-Task -TaskName $TaskName -TaskCommand $taskCommand -IntervalMinutes $IntervalMinutes -AsSystem $false
}

if ($createResult.ExitCode -ne 0) {
  throw "Failed to create scheduled task. Output: $($createResult.Output)"
}

$runOutput = & schtasks.exe /Run /TN $TaskName 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Task created, but failed to start immediately. Output: $runOutput"
}

Write-Output "Scheduled task installed successfully."
Write-Output "Task name: $TaskName"
Write-Output "Interval: every $IntervalMinutes minute(s)"
Write-Output "Install mode: $installMode"
Write-Output "Watchdog script: $watchdogScript"
Write-Output "You can inspect it with: schtasks /Query /TN `"$TaskName`" /V /FO LIST"
