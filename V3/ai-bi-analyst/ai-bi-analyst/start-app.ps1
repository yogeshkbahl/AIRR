# Starts AI BI Analyst in Docker and opens it in the browser.
# Usually launched by double-clicking start-app.bat.
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Fail([string]$message) {
    Write-Host ''
    Write-Host $message -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

Write-Host 'AI BI Analyst' -ForegroundColor Cyan

# 1. Docker must be installed and running.
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail 'Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this again.'
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Docker is not running. Trying to start Docker Desktop...'
    # Machine-wide and per-user installs live in different places.
    $desktop = @(
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe')
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($desktop) { Start-Process $desktop }
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 3
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    }
    if (-not $ready) { Fail 'Docker did not start. Open Docker Desktop, wait until it says it is running, then run this again.' }
}

# 2. First run on this machine: create .env from answers, never from a file with someone else's keys.
if (-not (Test-Path '.env')) {
    Write-Host ''
    Write-Host 'First run: choose the AI provider (keys stay on this machine).'
    Write-Host '  1) Anthropic Claude   2) OpenAI   3) None (offline, rule-based)'
    $choice = Read-Host 'Choice [1]'
    $provider = 'anthropic'; $keyName = 'ANTHROPIC_API_KEY'
    if ($choice -eq '2') { $provider = 'openai'; $keyName = 'OPENAI_API_KEY' }
    if ($choice -eq '3') { $provider = 'heuristic'; $keyName = $null }
    $lines = @("LLM_PROVIDER=$provider", 'APP_PORT=8080')
    if ($keyName) {
        $secure = Read-Host "Paste your $keyName" -AsSecureString
        $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
        $lines += "$keyName=$key"
    }
    # UTF-8 without BOM so Docker reads the first line correctly.
    [IO.File]::WriteAllLines((Join-Path $PSScriptRoot '.env'), $lines, (New-Object Text.UTF8Encoding $false))
    Write-Host 'Saved settings to .env'
}

$port = 8080
$portLine = Select-String -Path '.env' -Pattern '^\s*APP_PORT\s*=\s*(\d+)' | Select-Object -First 1
if ($portLine) { $port = [int]$portLine.Matches[0].Groups[1].Value }
$url = "http://localhost:$port"

New-Item -ItemType Directory -Force -Path 'logs' | Out-Null

# Containers default to UTC; pass the host zone so log folders match the local day.
# Windows uses its own zone names, so map the common ones and fall back to a fixed offset.
if (-not $env:TZ) {
    $local = [TimeZoneInfo]::Local
    $known = @{
        'Pacific Standard Time' = 'America/Los_Angeles'; 'Mountain Standard Time' = 'America/Denver'
        'US Mountain Standard Time' = 'America/Phoenix'; 'Central Standard Time' = 'America/Chicago'
        'Eastern Standard Time' = 'America/New_York'; 'Alaskan Standard Time' = 'America/Anchorage'
        'Hawaiian Standard Time' = 'Pacific/Honolulu'; 'GMT Standard Time' = 'Europe/London'
        'W. Europe Standard Time' = 'Europe/Berlin'; 'Romance Standard Time' = 'Europe/Paris'
        'Central Europe Standard Time' = 'Europe/Budapest'; 'India Standard Time' = 'Asia/Kolkata'
        'China Standard Time' = 'Asia/Shanghai'; 'Tokyo Standard Time' = 'Asia/Tokyo'
        'Singapore Standard Time' = 'Asia/Singapore'; 'AUS Eastern Standard Time' = 'Australia/Sydney'
        'Arabian Standard Time' = 'Asia/Dubai'; 'UTC' = 'UTC'
    }
    if ($known.ContainsKey($local.Id)) {
        $env:TZ = $known[$local.Id]
    } else {
        # Etc/GMT signs are inverted: UTC-7 is Etc/GMT+7. Whole hours only, no DST.
        $hours = [int][Math]::Round(-$local.BaseUtcOffset.TotalHours)
        $env:TZ = if ($hours -eq 0) { 'UTC' } elseif ($hours -gt 0) { "Etc/GMT+$hours" } else { "Etc/GMT$hours" }
    }
}

# 3. Build (first run takes a few minutes) and start in the background.
Write-Host ''
Write-Host 'Building and starting containers (the first run takes a few minutes)...'
docker compose -f docker-compose.app.yml up -d --build
if ($LASTEXITCODE -ne 0) { Fail 'Docker could not start the app. Scroll up for the error.' }

# 4. Wait until the API answers through nginx, then open the browser.
Write-Host "Waiting for $url ..."
$up = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "$url/api/v1/health" | Out-Null
        $up = $true; break
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $up) { Fail "The app did not respond. Check: docker compose -f docker-compose.app.yml logs" }

Start-Process $url
Write-Host ''
Write-Host "Running at $url" -ForegroundColor Green
Write-Host 'It keeps running in the background. Use stop-app.bat to stop it.'
Start-Sleep -Seconds 4
