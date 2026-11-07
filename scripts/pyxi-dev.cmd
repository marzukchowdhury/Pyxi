@echo off
rem Pyxi, developer version: runs straight from this checkout (desktop\ and .venv), with live
rem reload, your own keys from .env, and its data in .pyxi\ here. Not the installed beta copy.
rem The installed Pyxi is closed first: both listen for Ctrl+Alt and use the same brain port.
title Pyxi Dev
cd /d "%~dp0.."
powershell -NoProfile -Command "Get-Process Pyxi -ErrorAction SilentlyContinue | Stop-Process; Start-Sleep 2; Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '*\pyxi-desktop\resources\python\python.exe' } | Invoke-CimMethod -MethodName Terminate | Out-Null"
if not exist ".venv\Scripts\python.exe" (
  echo Pyxi Dev needs the Python environment in .venv. See README.
  pause
  exit /b 1
)
cd desktop
call npm run dev
