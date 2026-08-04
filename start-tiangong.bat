@echo off
setlocal EnableExtensions
cd /d "%~dp0"
where powershell.exe >nul 2>nul || (
  echo [ERROR] PowerShell is required.
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-source.ps1" %*
exit /b %ERRORLEVEL%
