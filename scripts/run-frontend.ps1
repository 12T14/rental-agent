$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $projectRoot 'frontend')

try {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\node_modules'))) {
        throw 'Frontend dependencies not found. Run setup.bat first.'
    }
    Write-Host 'Frontend: http://127.0.0.1:5173' -ForegroundColor Cyan
    & npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Frontend exited with code $exitCode."
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Press Enter to close the frontend window'
    exit 1
}
