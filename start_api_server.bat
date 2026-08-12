@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="
call :select_python
call :select_port

if not defined PYTHON_EXE (
  echo ERROR: No usable Python interpreter was found. >> api_server.task.log
  echo Please install Python 3.12+ or repair the project virtual environment. >> api_server.task.log
  exit /b 1
)

"%PYTHON_EXE%" api_server.py >> api_server.task.log 2>&1
exit /b %ERRORLEVEL%

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
if exist "..\..\svh\python.exe" call :check_python "..\..\svh\python.exe"
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
