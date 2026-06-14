@echo off
title Short Drama Studio
cd /d "%~dp0"

echo.
echo   ============================================
echo     Short Drama Studio
echo   ============================================
echo.
echo   Starting server on http://127.0.0.1:18888
echo.

start http://127.0.0.1:18888
.venv\Scripts\python.exe api_server.py
pause
