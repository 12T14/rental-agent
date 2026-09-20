@echo off
setlocal

rem This is the first-run entry point for a clean Windows machine.
rem It may install Python and Node.js through winget, then runs setup.ps1.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "exitCode=%ERRORLEVEL%"

if not "%exitCode%"=="0" (
    echo.
    echo Installation failed with exit code %exitCode%.
)

pause

exit /b %exitCode%
