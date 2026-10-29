# Jalankan backend mode dev + konsol test web di http://localhost:8000/console
# Pemakaian:  powershell -ExecutionPolicy Bypass -File scripts\run_console.ps1 [-Port 8000] [-Reset]
param([int]$Port = 8000, [switch]$Reset)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "Virtualenv .venv belum ada. Jalankan: python -m venv .venv; .venv\Scripts\pip install -r requirements-dev.txt"; exit 1 }

$db = Join-Path $root "potani-dev.db"
if ($Reset -and (Test-Path $db)) { Remove-Item $db }

$env:DATABASE_URL = "sqlite:///" + ($db -replace '\\', '/')
$env:AUTH_MODE = "dev"
$env:CROPS_FILE = "crops.dev.json"
$env:REMEDIATION_FILE = "remediation_rules.dev.json"
if (-not $env:TIME_ACCELERATION) { $env:TIME_ACCELERATION = "1440" }   # 1 hari = 1 menit
if (-not $env:LOG_LEVEL) { $env:LOG_LEVEL = "WARNING" }

& (Join-Path $root ".venv\Scripts\alembic.exe") upgrade head
& $py -m app.seed_demo --uid demo | Out-Null
Write-Host ""
Write-Host "Konsol test: http://localhost:$Port/console   (pengguna: demo)" -ForegroundColor Green
Write-Host "Dokumentasi API: http://localhost:$Port/docs" -ForegroundColor Green
Write-Host "Ctrl+C untuk berhenti."
& $py -m uvicorn app.main:app --port $Port
