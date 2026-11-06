param([switch]$Install,[switch]$Lan)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

function Find-Python {
    $candidates = @(Get-Command python.exe,python3.exe -All -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    $candidates += @(Get-ChildItem -LiteralPath "$env:LOCALAPPDATA\Python" -Filter python.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        try {
            & $candidate -c 'import sys; sys.exit(0 if (3,12) <= sys.version_info[:2] <= (3,14) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { }
    }
    throw 'Please install Python 3.12, 3.13 or 3.14, then run .\start.ps1 -Install.'
}

if (!(Test-Path -LiteralPath $projectPython)) {
    $basePython = Find-Python
    & $basePython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    $Install = $true
}
if ($Install) {
    & $projectPython -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& $projectPython -c 'import cv2,fastapi,PIL,numpy,uvicorn,multipart'
if ($LASTEXITCODE -ne 0) { throw 'Dependencies are missing. Run .\start.ps1 -Install.' }

$nodeCandidates = @(Get-Command node.exe -All -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
$nvmInstalls = Join-Path $env:LOCALAPPDATA 'Author Software\nvm\installs'
if (Test-Path -LiteralPath $nvmInstalls) {
    $nodeCandidates += @(Get-ChildItem -LiteralPath $nvmInstalls -Filter node.exe -Recurse | Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName)
}
$projectNode = $null
foreach ($candidate in ($nodeCandidates | Select-Object -Unique)) {
    try {
        $nodeVersion = & $candidate --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $nodeVersion -match '^v(\d+)' -and [int]$Matches[1] -ge 20) {$projectNode=$candidate;break}
    } catch { }
}
if (!$projectNode) { throw 'Node.js 20 or newer is required. Select an installed version in your Node version manager.' }

foreach ($port in @(8000,5178)) {
    $connection = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($connection) { throw "Port $port is already in use. Close the existing service before starting another copy." }
}
New-Item -ItemType Directory -Path artifacts -Force | Out-Null
$apiProcess=$null;$webProcess=$null
try {
    $apiProcess=Start-Process -FilePath $projectPython -ArgumentList @('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput artifacts\backend.log -RedirectStandardError artifacts\backend-error.log
    $webArguments=@('frontend/server.mjs')
    if ($Lan) { $webArguments+='--lan' }
    $webProcess=Start-Process -FilePath $projectNode -ArgumentList $webArguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput artifacts\frontend.log -RedirectStandardError artifacts\frontend-error.log
    $ready=$false
    for ($attempt=0;$attempt -lt 30;$attempt++) {
        if ($apiProcess.HasExited -or $webProcess.HasExited) { throw 'A service exited. See artifacts/*-error.log.' }
        try {
            Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 1 | Out-Null
            Invoke-WebRequest 'http://127.0.0.1:5178' -TimeoutSec 1 -UseBasicParsing | Out-Null
            $ready=$true;break
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (!$ready) { throw 'Services did not become ready. See artifacts/*-error.log.' }
    Write-Host 'Avatar Mask is ready: http://127.0.0.1:5178' -ForegroundColor Green
    Write-Host 'API documentation: http://127.0.0.1:8000/docs'
    if ($Lan) {
        Write-Host 'Phone access is enabled on your local network. Use the same Wi-Fi.' -ForegroundColor Yellow
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { Write-Host "Phone URL: http://$($_.IPAddress):5178" }
    }
    Write-Host 'Keep this window open. Press Ctrl+C to stop both services.'
    while (!$apiProcess.HasExited -and !$webProcess.HasExited) { Start-Sleep -Seconds 1 }
} finally {
    foreach ($process in @($apiProcess,$webProcess)) {
        if ($null -ne $process -and !$process.HasExited) { $process.Kill() }
    }
}
