# Asset Agent - one-command installer for Windows.
#
# This file is a template: Odoo fills in the server URL and database from the
# address you fetched it from, so there is nothing in here to edit by hand.
#
#   powershell -ExecutionPolicy Bypass -Command "irm __SERVER_URL__/agent/install.ps1 | iex"
#
# Re-running it is the upgrade path: the service is stopped, the exe replaced
# and the service started again, and any settings already in config.json that
# are not the server coordinates are kept.

$ErrorActionPreference = 'Stop'

$ServerUrl    = '__SERVER_URL__'
$Database     = '__DATABASE__'
$BootstrapUrl = '__BOOTSTRAP_URL__'
$AgentUrl     = '__AGENT_URL__'
$NssmUrl      = '__NSSM_URL__'

$ServiceName     = 'AssetAgent'
$InstallDir      = Join-Path $env:ProgramFiles 'AssetAgent'
$DataDir         = Join-Path $env:ProgramData  'AssetAgent'
$ExePath         = Join-Path $InstallDir 'AssetAgent.exe'
$ConfigPath      = Join-Path $InstallDir 'config.json'
$NssmPath        = Join-Path $InstallDir 'nssm.exe'
$NssmFallbackUrl = 'https://nssm.cc/release/nssm-2.24.zip'

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "    $m" -ForegroundColor Green }
function Write-Note($m) { Write-Host "    $m" -ForegroundColor Yellow }

Write-Host ""
Write-Host "Asset Agent installer" -ForegroundColor White
Write-Host "  server:   $ServerUrl"
Write-Host "  database: $Database"
Write-Host ""

# --- elevation ------------------------------------------------------------
# The service runs as LocalSystem and writes under Program Files, so this
# needs Administrator. Rather than telling the user to start over in an
# elevated prompt, re-fetch and re-run ourselves through UAC - the bootstrap
# URL is known server-side, so the elevated copy is the same one command.
# -NoExit keeps that second window open so its output is actually readable.
$isAdmin = ([Security.Principal.WindowsPrincipal] `
                [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Step 'Administrator rights are required - re-launching through UAC...'
    # -EncodedCommand rather than -Command: Start-Process joins an argument
    # array with plain spaces and adds no quoting of its own, so a command
    # containing spaces and a pipe is at the mercy of how the child re-parses
    # it. Base64 removes the quoting question entirely.
    $inner = "irm '$BootstrapUrl' | iex"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($inner))
    Start-Process powershell -Verb RunAs -ArgumentList `
        "-NoProfile -NoExit -ExecutionPolicy Bypass -EncodedCommand $encoded"
    return
}

# TLS 1.2 is not the default on older Windows builds and the download fails
# with a bare "could not create SSL/TLS secure channel" without this.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

function Get-RemoteFile($Url, $Dest) {
    # Invoke-WebRequest's progress bar slows large downloads to a crawl in a
    # non-interactive console; suppressing it is the documented workaround.
    $prev = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing -TimeoutSec 600
    } finally {
        $ProgressPreference = $prev
    }
}

# --- stop an existing install --------------------------------------------
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Step "Existing $ServiceName service found - stopping it to upgrade"
    if (Test-Path $NssmPath) { & $NssmPath stop $ServiceName 2>&1 | Out-Null }
    else { Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue }

    # Windows reports Stopped slightly before the exe's file lock is released,
    # and replacing it too early fails with "file in use".
    for ($i = 0; $i -lt 15; $i++) {
        $s = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if (-not $s -or $s.Status -eq 'Stopped') { break }
        Start-Sleep -Seconds 1
    }
    Start-Sleep -Seconds 2
}

New-Item -ItemType Directory -Force -Path $InstallDir, $DataDir | Out-Null

# --- agent ----------------------------------------------------------------
Write-Step "Downloading the agent"
Write-Host "    $AgentUrl"
$tmpExe = "$ExePath.new"
Get-RemoteFile $AgentUrl $tmpExe
Move-Item -LiteralPath $tmpExe -Destination $ExePath -Force
Write-Ok ("installed to {0} ({1:N1} MB)" -f $ExePath, ((Get-Item $ExePath).Length / 1MB))

# --- config ---------------------------------------------------------------
Write-Step 'Writing config.json'
$config = [ordered]@{}
if (Test-Path $ConfigPath) {
    # Only the server coordinates are ours to set. A serial override, a
    # pre-staged Win11 installer path or tuned intervals were put there
    # deliberately and must survive a reinstall.
    try {
        (Get-Content $ConfigPath -Raw | ConvertFrom-Json).PSObject.Properties |
            ForEach-Object { $config[$_.Name] = $_.Value }
    } catch {
        Write-Note 'existing config.json could not be parsed - writing a fresh one'
        $config = [ordered]@{}
    }
}
$config['server_url'] = $ServerUrl
$config['database']   = $Database

# Must be written WITHOUT a byte-order mark: the agent reads this with a
# plain open()/json.load(), and a BOM makes that raise, which would silently
# drop it back to its built-in localhost defaults.
$json = $config | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Ok $ConfigPath

# --- service wrapper ------------------------------------------------------
# AssetAgent.exe is an ordinary console program, not a Service Control
# Manager-aware binary, so "sc create" would start it and then kill it for
# failing to report ready. NSSM supervises it properly and is what this
# fleet's install_service.bat already standardises on.
if (-not (Test-Path $NssmPath)) {
    Write-Step 'Fetching nssm.exe (service wrapper)'
    $haveNssm = $false
    try {
        Get-RemoteFile $NssmUrl $NssmPath
        $haveNssm = $true
        Write-Ok 'from the Odoo server'
    } catch {
        Write-Note 'not staged on the Odoo server - falling back to nssm.cc'
    }
    if (-not $haveNssm) {
        $zip = Join-Path $env:TEMP 'nssm.zip'
        $ext = Join-Path $env:TEMP 'nssm-extract'
        Get-RemoteFile $NssmFallbackUrl $zip
        if (Test-Path $ext) { Remove-Item $ext -Recurse -Force }
        Expand-Archive -LiteralPath $zip -DestinationPath $ext -Force
        $arch = if ([Environment]::Is64BitOperatingSystem) { 'win64' } else { 'win32' }
        $src = Get-ChildItem $ext -Recurse -Filter 'nssm.exe' |
               Where-Object { $_.FullName -like "*\$arch\*" } |
               Select-Object -First 1
        if (-not $src) { throw "nssm.exe for $arch was not found inside $NssmFallbackUrl" }
        Copy-Item $src.FullName $NssmPath -Force
        Remove-Item $zip, $ext -Recurse -Force -ErrorAction SilentlyContinue
        Write-Ok 'from nssm.cc'
    }
}

Write-Step "Registering the $ServiceName service"
if ($existing) {
    & $NssmPath remove $ServiceName confirm 2>&1 | Out-Null
    Start-Sleep -Seconds 2
}
& $NssmPath install $ServiceName $ExePath                                   2>&1 | Out-Null
& $NssmPath set $ServiceName Start SERVICE_AUTO_START                       2>&1 | Out-Null
& $NssmPath set $ServiceName ObjectName LocalSystem                         2>&1 | Out-Null
& $NssmPath set $ServiceName AppDirectory $InstallDir                       2>&1 | Out-Null
& $NssmPath set $ServiceName AppStdout (Join-Path $DataDir 'service_stdout.log') 2>&1 | Out-Null
& $NssmPath set $ServiceName AppStderr (Join-Path $DataDir 'service_stderr.log') 2>&1 | Out-Null
& $NssmPath set $ServiceName AppRotateFiles 1                               2>&1 | Out-Null
& $NssmPath set $ServiceName AppRotateBytes 10485760                        2>&1 | Out-Null
& $NssmPath set $ServiceName Description "Asset Management agent - reports to $ServerUrl" 2>&1 | Out-Null
& $NssmPath start $ServiceName                                              2>&1 | Out-Null

# --- verify ---------------------------------------------------------------
Write-Step 'Verifying'
$svc = $null
for ($i = 0; $i -lt 20; $i++) {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq 'Running') { break }
    Start-Sleep -Seconds 1
}

if (-not $svc -or $svc.Status -ne 'Running') {
    Write-Host ""
    Write-Host "$ServiceName did not reach Running." -ForegroundColor Red
    foreach ($log in @('service_stderr.log', 'agent.log')) {
        $p = Join-Path $DataDir $log
        if (Test-Path $p) {
            Write-Host "--- $p (last 20 lines) ---" -ForegroundColor Red
            Get-Content $p -Tail 20
        }
    }
    throw "$ServiceName failed to start."
}

Write-Ok "$ServiceName is running as LocalSystem"

# The agent syncs once immediately on startup, so a few seconds is enough for
# the first check-in to have been attempted and logged either way.
Start-Sleep -Seconds 8
$agentLog = Join-Path $DataDir 'agent.log'
if (Test-Path $agentLog) {
    Write-Host ""
    Write-Host "--- first lines of agent.log ---" -ForegroundColor DarkGray
    Get-Content $agentLog -Tail 15
}

Write-Host ""
Write-Host "Done. The asset appears in Odoo under its serial number." -ForegroundColor Green
Write-Host "  status:    sc query $ServiceName"
Write-Host "  logs:      $agentLog"
Write-Host "  config:    $ConfigPath"
Write-Host "  uninstall: `"$NssmPath`" stop $ServiceName; `"$NssmPath`" remove $ServiceName confirm"
Write-Host ""
