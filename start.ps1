[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$Hidden
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Test-ListeningPort([int]$Port) {
    try {
        return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Select-Object -First 1)
    } catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.venv\Scripts\python.exe'))) {
    throw 'Project .venv not found. Run setup.bat first.'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\node_modules'))) {
    throw 'Frontend dependencies not found. Run setup.bat first.'
}
if (Test-ListeningPort 8090) {
    throw 'Port 8090 is already in use. Close the existing backend before starting another instance.'
}
if (Test-ListeningPort 5173) {
    throw 'Port 5173 is already in use. Close the existing frontend before starting another instance.'
}

$shell = Get-Command pwsh -ErrorAction SilentlyContinue
if (-not $shell) {
    $shell = Get-Command powershell -ErrorAction SilentlyContinue
}
if (-not $shell) {
    throw 'PowerShell not found.'
}

$backendScript = Join-Path $projectRoot 'scripts\run-backend.ps1'
$frontendScript = Join-Path $projectRoot 'scripts\run-frontend.ps1'
$backendArguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$backendScript`""
$frontendArguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$frontendScript`""

function Wait-LocalService([string]$Uri, [System.Diagnostics.Process]$ServiceProcess) {
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        if ($ServiceProcess.HasExited) {
            throw "Service process $($ServiceProcess.Id) exited before $Uri was ready."
        }
        try {
            Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop | Out-Null
            return
        } catch {
            Start-Sleep -Milliseconds 300
        }
    }
    throw "Service did not become ready: $Uri. Check its terminal output."
}

$windowStyle = if ($Hidden) { 'Hidden' } else { 'Normal' }
$startedProcesses = @()
try {
    $backendProcess = Start-Process -FilePath $shell.Source -WorkingDirectory $projectRoot -ArgumentList $backendArguments -WindowStyle $windowStyle -PassThru
    $startedProcesses += $backendProcess
    Wait-LocalService 'http://127.0.0.1:8090/health' $backendProcess
    $frontendProcess = Start-Process -FilePath $shell.Source -WorkingDirectory $projectRoot -ArgumentList $frontendArguments -WindowStyle $windowStyle -PassThru
    $startedProcesses += $frontendProcess
    Wait-LocalService 'http://127.0.0.1:5173/' $frontendProcess
} catch {
    foreach ($startedProcess in $startedProcesses) {
        if (-not $startedProcess.HasExited) {
            & taskkill.exe /PID $startedProcess.Id /T /F 2>$null | Out-Null
        }
    }
    throw
}

if (-not $NoBrowser) {
    Start-Process 'http://127.0.0.1:5173/'
}

Write-Host 'Backend and frontend are ready.' -ForegroundColor Green
Write-Host "Backend terminal PID: $($backendProcess.Id)"
Write-Host "Frontend terminal PID: $($frontendProcess.Id)"
Write-Host 'Open: http://127.0.0.1:5173/' -ForegroundColor Cyan
