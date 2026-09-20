[CmdletBinding()]
param(
    [switch]$WithMongo,
    [switch]$SkipPlaywright,
    [string]$Python
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Stop-WithMessage([string]$Message) {
    throw "[rental-agent setup] $Message"
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "Command failed (exit $LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

function Find-Application([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name)) {
        return $null
    }

    # 允许调用方传入完整路径；Windows PowerShell 对“完整路径 +
    # -CommandType Application”的处理与 PowerShell 7 不完全一致。
    if (Test-Path -LiteralPath $Name -PathType Leaf) {
        return [pscustomobject]@{ Source = (Resolve-Path -LiteralPath $Name).Path }
    }

    # PATH 中可能同时存在多个同名命令；只选实际优先级最高的可执行文件。
    $command = Get-Command -Name $Name -All -ErrorAction SilentlyContinue | Where-Object { $_.CommandType -eq 'Application' } | Select-Object -First 1
    return $command
}

$pythonArgs = @()
if ($Python) {
    $pythonCommand = Find-Application $Python
} else {
    $pythonCommand = Find-Application 'py'
    if ($pythonCommand) {
        $pythonArgs = @('-3')
    } else {
        $pythonCommand = Find-Application 'python'
    }
}
if (-not $pythonCommand) {
    Stop-WithMessage 'Python not found. Install Python 3.11+ and enable Add Python to PATH, or pass -Python with an executable path.'
}

$pythonVersion = (& $pythonCommand.Source @pythonArgs --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersion -notmatch 'Python\s+(\d+)\.(\d+)') {
    Stop-WithMessage "Cannot detect Python version: $pythonVersion"
}
if ([int]$Matches[1] -lt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -lt 11)) {
    Stop-WithMessage "Python 3.11+ is required. Found: $pythonVersion"
}

$nodeCommand = Find-Application 'node'
$npmCommand = Find-Application 'npm.cmd'
if (-not $nodeCommand -or -not $npmCommand) {
    Stop-WithMessage 'Node.js/npm not found. Install Node.js 22 LTS or newer.'
}
$nodeVersion = (& $nodeCommand.Source --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch 'v(\d+)') {
    Stop-WithMessage "Cannot detect Node.js version: $nodeVersion"
}
if ([int]$Matches[1] -lt 18) {
    Stop-WithMessage "Node.js 18+ is required. Found: $nodeVersion"
}

$venvRoot = Join-Path $projectRoot '.venv'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host 'Creating the project virtual environment .venv ...' -ForegroundColor Cyan
    Invoke-Checked $pythonCommand.Source ($pythonArgs + @('-m', 'venv', $venvRoot))
}

Write-Host 'Installing backend dependencies ...' -ForegroundColor Cyan
Invoke-Checked $venvPython @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-Checked $venvPython @('-m', 'pip', 'install', '-r', (Join-Path $projectRoot 'backend\requirements.txt'))

if ($WithMongo) {
    Write-Host 'Installing the optional MongoDB Python adapter ...' -ForegroundColor Cyan
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '-r', (Join-Path $projectRoot 'backend\requirements-mongodb.txt'))
}

if (-not $SkipPlaywright) {
    Write-Host 'Installing Playwright Chromium ...' -ForegroundColor Cyan
    Invoke-Checked $venvPython @('-m', 'playwright', 'install', 'chromium')
}

Write-Host 'Installing frontend dependencies ...' -ForegroundColor Cyan
Push-Location (Join-Path $projectRoot 'frontend')
try {
    if (Test-Path -LiteralPath 'node_modules') {
        # npm ci removes node_modules first, which fails when a running dev server
        # has a native package open. Re-running setup should update in place.
        Invoke-Checked $npmCommand.Source @('install')
    } elseif (Test-Path -LiteralPath 'package-lock.json') {
        Invoke-Checked $npmCommand.Source @('ci')
    } else {
        Invoke-Checked $npmCommand.Source @('install')
    }
} finally {
    Pop-Location
}

foreach ($directory in @(
    (Join-Path $projectRoot 'backend\runtime\sessions'),
    (Join-Path $projectRoot 'backend\artifacts')
)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

function Ensure-PrivateTemplate([string]$Template, [string]$PrivateFile) {
    if (-not (Test-Path -LiteralPath $PrivateFile)) {
        Copy-Item -LiteralPath $Template -Destination $PrivateFile
        Write-Host "Created private configuration: $PrivateFile" -ForegroundColor Green
    } else {
        Write-Host "Kept existing private configuration: $PrivateFile" -ForegroundColor DarkGray
    }
}

Ensure-PrivateTemplate (Join-Path $projectRoot 'backend\.env.example') (Join-Path $projectRoot 'backend\.env')
Ensure-PrivateTemplate (Join-Path $projectRoot 'frontend\.env.example') (Join-Path $projectRoot 'frontend\.env.local')

Write-Host ''
Write-Host 'Setup complete. New configuration files use these defaults:' -ForegroundColor Green
Write-Host '  CHECKPOINT_BACKEND=memory'
Write-Host '  RENTAL_DEMO_MODE=offline'
Write-Host ''
Write-Host 'Existing configuration values are never changed.'
Write-Host 'AI chat requires MAIN_MODEL_API_KEY in backend\.env, including offline fixture mode.'
Write-Host 'Location lookup requires AMAP_WEB_SERVICE_KEY in backend\.env.'
Write-Host 'The interactive map uses frontend\.env.local. All keys stay in private files.'
Write-Host 'Real platform searches require RENTAL_DEMO_MODE=live and acceptance of the platform terms.'
Write-Host 'For persistence, install -WithMongo and configure a separately managed MongoDB service.'
Write-Host 'Next: run start.bat or .\start.ps1' -ForegroundColor Cyan
