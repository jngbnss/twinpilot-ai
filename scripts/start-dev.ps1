# Start TwinPilot backend (8770) and dashboard (5290) in two new windows.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1
$root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path "$root\ai\models\detector-v1.joblib")) {
    Write-Host "Anomaly model not found. Run first:" -ForegroundColor Yellow
    Write-Host "  uv run python -m twinpilot_ai.training.make_dataset --version v1 --workers 10"
    Write-Host "  uv run python -m twinpilot_ai.evaluation.evaluate"
}
if (-not (Test-Path "$root\frontend\node_modules")) {
    Push-Location "$root\frontend"; npm install; Pop-Location
}

Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root'; uv run python -m uvicorn app.main:app --app-dir backend --port 8770"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\frontend'; npx vite"
Start-Sleep -Seconds 6
Start-Process "http://127.0.0.1:5290"
