@echo off
setlocal
title Short Drama Studio Public Tunnel
cd /d "%~dp0"

set "STUDIO_AUTH_ENABLED=1"
if not defined STUDIO_USERNAME set "STUDIO_USERNAME=admin"
if not defined STUDIO_PASSWORD set "STUDIO_PASSWORD=Video2026!"
if not defined STUDIO_AUTH_SECRET set "STUDIO_AUTH_SECRET=local-public-tunnel-2026"

set "PYTHON_EXE="
set "CLOUDFLARED_EXE="
call :select_python
call :select_port
call :select_cloudflared

if not defined PYTHON_EXE (
  echo.
  echo   ERROR: No usable Python interpreter was found.
  echo.
  pause
  exit /b 1
)

if not defined CLOUDFLARED_EXE (
  echo.
  echo   cloudflared was not found.
  echo   Install Cloudflare Tunnel first, then run this file again.
  echo   Download: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
  echo   You can also put cloudflared.exe in this project's tools folder.
  echo.
  echo   Login username: %STUDIO_USERNAME%
  echo   Login password: %STUDIO_PASSWORD%
  echo.
  pause
  exit /b 1
)

echo.
echo   ============================================
echo     Short Drama Studio Public Access
echo   ============================================
echo.
echo   Local URL: http://127.0.0.1:%SHORT_DRAMA_PORT%
echo   Login username: %STUDIO_USERNAME%
echo   Login password: %STUDIO_PASSWORD%
echo.
echo   Keep both windows open while others are using it.
echo.

start "Short Drama Studio API" /D "%~dp0" cmd /k "%PYTHON_EXE% api_server.py"
timeout /t 5 /nobreak >nul
"%CLOUDFLARED_EXE%" tunnel --url http://127.0.0.1:%SHORT_DRAMA_PORT%
exit /b %ERRORLEVEL%

:select_cloudflared
if exist "tools\cloudflared.exe" (
  "tools\cloudflared.exe" --version >nul 2>&1
  if not errorlevel 1 set "CLOUDFLARED_EXE=tools\cloudflared.exe"
)
if defined CLOUDFLARED_EXE exit /b 0
cloudflared --version >nul 2>&1
if not errorlevel 1 set "CLOUDFLARED_EXE=cloudflared"
exit /b 0

:select_port
for /L %%P in (18888,1,18897) do (
  if not defined SHORT_DRAMA_PORT (
    netstat -ano | findstr /R /C:":%%P .*LISTENING" >nul
    if errorlevel 1 set "SHORT_DRAMA_PORT=%%P"
  )
)
if not defined SHORT_DRAMA_PORT set "SHORT_DRAMA_PORT=18888"
exit /b 0

:select_python
if exist ".venv\Scripts\python.exe" call :check_python ".venv\Scripts\python.exe"
if defined PYTHON_EXE exit /b 0
if exist "..\svh\python.exe" call :check_python "..\svh\python.exe"
if defined PYTHON_EXE exit /b 0
call :check_python "python"
if defined PYTHON_EXE exit /b 0
call :check_python "py"
exit /b 0

:check_python
"%~1" --version >nul 2>&1
if not errorlevel 1 set "PYTHON_EXE=%~1"
exit /b 0
