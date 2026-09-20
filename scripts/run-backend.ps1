$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

try {
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Project .venv not found. Run setup.bat first.'
    }
    Write-Host 'Backend: http://127.0.0.1:8090' -ForegroundColor Cyan
    & $python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8090
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Backend exited with code $exitCode."
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Press Enter to close the backend window'
    exit 1
}
