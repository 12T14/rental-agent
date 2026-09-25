[CmdletBinding()]
param(
    [switch]$WithMongo,
    [switch]$SkipPlaywright,
    [switch]$SkipPrerequisites
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$initialProcessPath = $env:Path
Set-Location -LiteralPath $projectRoot

function Stop-WithMessage([string]$Message) {
    throw "[rental-agent install] $Message"
}

function Find-Application([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name)) {
        return $null
    }
    if (Test-Path -LiteralPath $Name -PathType Leaf) {
        return [pscustomobject]@{ Source = (Resolve-Path -LiteralPath $Name).Path }
    }
    $command = Get-Command -Name $Name -All -ErrorAction SilentlyContinue | Where-Object { $_.CommandType -eq 'Application' } | Select-Object -First 1
    return $command
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "Command failed (exit $LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

function Refresh-ProcessPath {
    # winget updates the user/machine PATH, but the current PowerShell process
    # does not see that change automatically.
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $parts = @($initialProcessPath, $userPath, $machinePath) |
        ForEach-Object { if ($_ ) { $_ -split ';' } } |
        Where-Object { $_ -and $_.Trim() } |
        Select-Object -Unique
    $env:Path = ($parts -join ';')
}

function Add-KnownToolPaths {
    # These locations cover the default per-user and machine installers used by
    # winget. Put them first so an older executable earlier in PATH is not used.
    $known = @(
        (Join-Path $env:ProgramFiles 'nodejs'),
        (Join-Path $env:LocalAppData 'Programs\nodejs'),
        (Join-Path $env:ProgramFiles 'Python\Launcher'),
        (Join-Path $env:LocalAppData 'Programs\Python\Python314'),
        (Join-Path $env:LocalAppData 'Programs\Python\Python313'),
        (Join-Path $env:LocalAppData 'Programs\Python\Python312'),
        (Join-Path $env:LocalAppData 'Programs\Python\Python311')
    )
    $existing = $known | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    if ($existing) {
        $env:Path = (($existing + ($env:Path -split ';')) |
            Where-Object { $_ -and $_.Trim() } |
            Select-Object -Unique) -join ';'
    }
}

function Get-MinorVersion([string]$Text, [string]$Pattern) {
    if ($Text -match $Pattern) {
        return [pscustomobject]@{
            Major = [int]$Matches[1]
            Minor = [int]$Matches[2]
            Text = $Text.Trim()
        }
    }
    return $null
}

function Get-PythonInfo {
    Add-KnownToolPaths

    $launcher = Find-Application 'py'
    if ($launcher) {
        $output = (& $launcher.Source -3 --version 2>&1 | Out-String).Trim()
        $version = Get-MinorVersion $output 'Python\s+(\d+)\.(\d+)'
        if ($version -and ($version.Major -gt 3 -or ($version.Major -eq 3 -and $version.Minor -ge 11))) {
            $resolvedOutput = @(& $launcher.Source -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1)
            $resolved = if ($resolvedOutput.Count) { [string]$resolvedOutput[0].Trim() } else { '' }
            if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
                $resolved = $launcher.Source
            }
            return [pscustomobject]@{
                Executable = $resolved
                UsesLauncher = ($resolved -eq $launcher.Source)
                Version = $version.Text
            }
        }
    }

    $python = Find-Application 'python'
    if ($python) {
        $output = (& $python.Source --version 2>&1 | Out-String).Trim()
        $version = Get-MinorVersion $output 'Python\s+(\d+)\.(\d+)'
        if ($version -and ($version.Major -gt 3 -or ($version.Major -eq 3 -and $version.Minor -ge 11))) {
            return [pscustomobject]@{
                Executable = $python.Source
                UsesLauncher = $false
                Version = $version.Text
            }
        }
    }

    return $null
}

function Get-NodeInfo {
    Add-KnownToolPaths
    $node = Find-Application 'node'
    $npm = Find-Application 'npm.cmd'
    if (-not $node -or -not $npm) {
        return $null
    }

    $output = (& $node.Source --version 2>&1 | Out-String).Trim()
    $version = Get-MinorVersion $output 'v?(\d+)\.(\d+)'
    if (-not $version -or $version.Major -lt 18) {
        return $null
    }

    return [pscustomobject]@{
        Executable = $node.Source
        Npm = $npm.Source
        Version = $version.Text
    }
}

function Install-WingetPackage([string]$PackageId, [string]$DisplayName) {
    $winget = Find-Application 'winget'
    if (-not $winget) {
        $message = '未发现 winget，无法自动安装 ' + $DisplayName + ".`n请先通过 Microsoft Store 安装应用安装程序，或手动安装 Python 3.11+ 和 Node.js 18+，再重新运行本脚本。"
        Stop-WithMessage $message
    }

    Write-Host "Installing $DisplayName through winget ..." -ForegroundColor Cyan
    $arguments = @(
        'install',
        '--id', $PackageId,
        '--exact',
        '--source', 'winget',
        '--accept-source-agreements',
        '--accept-package-agreements',
        '--silent'
    )
    Invoke-Checked $winget.Source $arguments
    Refresh-ProcessPath
    Add-KnownToolPaths
}

Write-Host 'Checking Windows prerequisites ...' -ForegroundColor Cyan
$pythonInfo = Get-PythonInfo
if ($pythonInfo) {
    Write-Host "Python found: $($pythonInfo.Version)" -ForegroundColor Green
} elseif ($SkipPrerequisites) {
    Stop-WithMessage 'Python 3.11+ was not found and -SkipPrerequisites was specified.'
} else {
    Install-WingetPackage 'Python.Python.3.12' 'Python 3.12'
    $pythonInfo = Get-PythonInfo
    if (-not $pythonInfo) {
        Stop-WithMessage 'Python was installed, but this process could not find it. Close this window, open a new PowerShell, and run install.bat again.'
    }
    Write-Host "Python ready: $($pythonInfo.Version)" -ForegroundColor Green
}

$nodeInfo = Get-NodeInfo
if ($nodeInfo) {
    Write-Host "Node.js found: $($nodeInfo.Version)" -ForegroundColor Green
} elseif ($SkipPrerequisites) {
    Stop-WithMessage 'Node.js 18+ and npm were not found and -SkipPrerequisites was specified.'
} else {
    Install-WingetPackage 'OpenJS.NodeJS.LTS' 'Node.js LTS'
    $nodeInfo = Get-NodeInfo
    if (-not $nodeInfo) {
        Stop-WithMessage 'Node.js was installed, but this process could not find it. Close this window, open a new PowerShell, and run install.bat again.'
    }
    Write-Host "Node.js ready: $($nodeInfo.Version)" -ForegroundColor Green
}

$setupScript = Join-Path $projectRoot 'setup.ps1'
if (-not (Test-Path -LiteralPath $setupScript)) {
    Stop-WithMessage 'setup.ps1 is missing from the project folder.'
}

$setupParameters = @{
    Python = $pythonInfo.Executable
}
if ($WithMongo) {
    $setupParameters['WithMongo'] = $true
}
if ($SkipPlaywright) {
    $setupParameters['SkipPlaywright'] = $true
}
# Pass an explicit executable to the second-stage setup. This avoids relying on
# a parent/child PowerShell process agreeing on the refreshed PATH.

Write-Host ''
Write-Host 'Installing project dependencies and creating private config ...' -ForegroundColor Cyan
& $setupScript @setupParameters
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "Project setup failed (exit $LASTEXITCODE)."
}

Write-Host ''
Write-Host 'First-time installation complete.' -ForegroundColor Green
Write-Host 'Private files created or preserved:'
Write-Host '  backend\.env'
Write-Host '  frontend\.env.local'
Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Cyan
Write-Host '  1. Fill in the model and map keys in backend\.env and frontend\.env.local.'
Write-Host '  2. Keep CHECKPOINT_BACKEND=memory for the simplest first run.'
Write-Host '  3. Run start.bat to launch the backend and frontend.'
Write-Host 'New configuration uses live rental searches. Existing .env files are preserved; check RENTAL_DEMO_MODE when upgrading.'
Write-Host ''
Write-Host 'MongoDB is optional. It was not installed or started by this script.' -ForegroundColor DarkGray
