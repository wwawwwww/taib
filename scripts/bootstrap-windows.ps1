$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "TG Auto bootstrap for Windows"
Write-Host "Project: $ProjectRoot"

$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PythonLauncher) {
    $PythonCmd = "py"
    $PythonArgs = @("-3")
} else {
    $PythonExe = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonExe) {
        throw "Python 3.10+ not found. Install Python and run again."
    }
    $PythonCmd = "python"
    $PythonArgs = @()
}

if (-not (Test-Path ".venv")) {
    & $PythonCmd @PythonArgs -m venv .venv
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -e .

if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example"
}

if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path logs | Out-Null
}

Write-Host ""
Write-Host "Starting tgauto..."
& $VenvPython -m daily_poster.tui
