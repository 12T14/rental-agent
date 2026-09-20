@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "exitCode=%ERRORLEVEL%"
pause
exit /b %exitCode%
