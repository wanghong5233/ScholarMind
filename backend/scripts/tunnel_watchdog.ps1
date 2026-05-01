param(
  [string]$LocalHealthUrl = "http://127.0.0.1:8000/health",
  [string]$PublicHealthUrl = "",
  [string]$PublicHealthEnvKey = "SM_PUBLIC_HEALTH_URL",
  [int]$FailThreshold = 3,
  [int]$CooldownMinutes = 10,
  [int]$MaxRestartsPerDay = 8,
  [int]$HttpTimeoutSec = 8,
  [string]$ContainerName = "cf_tunnel_scholarmind"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
$backendDir = Split-Path -Parent $scriptDir
$stateDir = Join-Path $backendDir ".watchdog"
$stateFile = Join-Path $stateDir "tunnel_watchdog_state.json"
$logFile = Join-Path $stateDir "tunnel_watchdog.log"

function Ensure-Directory {
  param([string]$Path)
  if (-not (Test-Path -Path $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }
}

function Write-Log {
  param(
    [string]$Level,
    [string]$Message
  )
  $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
  $line = "[$timestamp][$Level] $Message"
  Write-Output $line
  Add-Content -Path $logFile -Value $line
}

function Get-DotEnvValue {
  param(
    [string]$FilePath,
    [string]$Key
  )
  if (-not (Test-Path -Path $FilePath)) {
    return ""
  }
  foreach ($rawLine in Get-Content -Path $FilePath) {
    $line = $rawLine.Trim()
    if ($line.Length -eq 0 -or $line.StartsWith("#")) {
      continue
    }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) {
      continue
    }
    $name = $line.Substring(0, $idx).Trim()
    if ($name -ne $Key) {
      continue
    }
    $value = $line.Substring($idx + 1).Trim()
    if (
      ($value.StartsWith('"') -and $value.EndsWith('"')) -or
      ($value.StartsWith("'") -and $value.EndsWith("'"))
    ) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    return $value
  }
  return ""
}

function Test-HealthUrl {
  param(
    [string]$Url,
    [int]$TimeoutSec
  )
  try {
    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.Method = "GET"
    $request.Timeout = $TimeoutSec * 1000
    $request.ReadWriteTimeout = $TimeoutSec * 1000
    $request.Proxy = $null
    $response = $request.GetResponse()
    try {
      $statusCode = [int]([System.Net.HttpWebResponse]$response).StatusCode
      return ($statusCode -ge 200 -and $statusCode -lt 300)
    } finally {
      $response.Close()
    }
  } catch {
    return $false
  }
}

function ConvertTo-Hashtable {
  param($Obj)
  $result = @{}
  if ($null -eq $Obj) {
    return $result
  }
  foreach ($p in $Obj.PSObject.Properties) {
    $result[$p.Name] = $p.Value
  }
  return $result
}

function Load-State {
  param([string]$Path)
  $defaultState = @{
    consecutive_public_failures = 0
    last_restart_utc = ""
    restarts_by_date = @{}
  }
  if (-not (Test-Path -Path $Path)) {
    return $defaultState
  }
  try {
    $raw = Get-Content -Path $Path -Raw
    if ([string]::IsNullOrWhiteSpace($raw)) {
      return $defaultState
    }
    $obj = $raw | ConvertFrom-Json
    $state = @{
      consecutive_public_failures = [int]$obj.consecutive_public_failures
      last_restart_utc = [string]$obj.last_restart_utc
      restarts_by_date = ConvertTo-Hashtable -Obj $obj.restarts_by_date
    }
    if ($null -eq $state.restarts_by_date) {
      $state.restarts_by_date = @{}
    }
    return $state
  } catch {
    return $defaultState
  }
}

function Save-State {
  param(
    [string]$Path,
    $State
  )
  $json = $State | ConvertTo-Json -Depth 8
  Set-Content -Path $Path -Value $json -Encoding UTF8
}

function In-CooldownWindow {
  param(
    [string]$LastRestartUtc,
    [int]$CooldownMinutes
  )
  if ([string]::IsNullOrWhiteSpace($LastRestartUtc)) {
    return $false
  }
  try {
    $last = [DateTime]::Parse($LastRestartUtc).ToUniversalTime()
    $now = (Get-Date).ToUniversalTime()
    $elapsedMinutes = (New-TimeSpan -Start $last -End $now).TotalMinutes
    return $elapsedMinutes -lt $CooldownMinutes
  } catch {
    return $false
  }
}

function Ensure-CloudflaredRunning {
  param(
    [string]$BackendDir,
    [string]$ContainerName
  )
  $exists = & docker ps -a --filter "name=^/${ContainerName}$" --format "{{.Names}}" 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "docker ps failed, please verify Docker daemon is running."
  }

  if ($exists -match $ContainerName) {
    & docker restart $ContainerName | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw "docker restart $ContainerName failed."
    }
    return "restart"
  }

  Push-Location $BackendDir
  try {
    & docker compose --profile public up -d cloudflared | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw "docker compose --profile public up -d cloudflared failed."
    }
  } finally {
    Pop-Location
  }
  return "compose_up"
}

Ensure-Directory -Path $stateDir

if ([string]::IsNullOrWhiteSpace($PublicHealthUrl)) {
  $PublicHealthUrl = Get-DotEnvValue -FilePath (Join-Path $backendDir ".env") -Key $PublicHealthEnvKey
}
if ([string]::IsNullOrWhiteSpace($PublicHealthUrl)) {
  $PublicHealthUrl = "https://api-scholarmind.wh5233.me/health"
}

$state = Load-State -Path $stateFile
$today = (Get-Date).ToString("yyyy-MM-dd")
$todayRestarts = 0
if ($state.restarts_by_date.ContainsKey($today)) {
  $todayRestarts = [int]$state.restarts_by_date[$today]
}

$localHealthy = Test-HealthUrl -Url $LocalHealthUrl -TimeoutSec $HttpTimeoutSec
$publicHealthy = Test-HealthUrl -Url $PublicHealthUrl -TimeoutSec $HttpTimeoutSec

if (-not $localHealthy) {
  $state.consecutive_public_failures = 0
  Save-State -Path $stateFile -State $state
  Write-Log -Level "WARN" -Message "Local API is unhealthy ($LocalHealthUrl). Skip tunnel recovery."
  exit 0
}

if ($publicHealthy) {
  if ([int]$state.consecutive_public_failures -gt 0) {
    Write-Log -Level "INFO" -Message "Public health recovered. Reset consecutive failure counter."
  }
  $state.consecutive_public_failures = 0
  Save-State -Path $stateFile -State $state
  exit 0
}

$state.consecutive_public_failures = [int]$state.consecutive_public_failures + 1
Write-Log -Level "WARN" -Message "Public health failed ($PublicHealthUrl). consecutive=$($state.consecutive_public_failures)/$FailThreshold"

if ([int]$state.consecutive_public_failures -lt $FailThreshold) {
  Save-State -Path $stateFile -State $state
  exit 0
}

if (In-CooldownWindow -LastRestartUtc $state.last_restart_utc -CooldownMinutes $CooldownMinutes) {
  Save-State -Path $stateFile -State $state
  Write-Log -Level "WARN" -Message "Cooldown active (${CooldownMinutes}m). Skip restart to avoid restart storm."
  exit 0
}

if ($todayRestarts -ge $MaxRestartsPerDay) {
  Save-State -Path $stateFile -State $state
  Write-Log -Level "ERROR" -Message "Restart cap reached for today (${todayRestarts}/${MaxRestartsPerDay}). Skip restart."
  exit 0
}

try {
  $action = Ensure-CloudflaredRunning -BackendDir $backendDir -ContainerName $ContainerName
  $state.last_restart_utc = (Get-Date).ToUniversalTime().ToString("o")
  $state.restarts_by_date[$today] = $todayRestarts + 1
  $state.consecutive_public_failures = 0
  Save-State -Path $stateFile -State $state
  Write-Log -Level "WARN" -Message "Tunnel recovery action succeeded. action=$action today_restarts=$($state.restarts_by_date[$today])"
  exit 0
} catch {
  Save-State -Path $stateFile -State $state
  Write-Log -Level "ERROR" -Message "Tunnel recovery action failed: $($_.Exception.Message)"
  exit 1
}
