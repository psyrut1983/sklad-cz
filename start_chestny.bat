@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_CMD="
py -3.12 -c "import sys" >nul 2>&1 && set "PYTHON_CMD=py -3.12"
if not defined PYTHON_CMD python -c "import sys; assert sys.version_info >= (3,11) and sys.version_info < (3,14)" >nul 2>&1 && set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
  echo Python 3.11-3.13 not found. Install Python 3.12 from python.org.
  pause
  exit /b 1
)
if not exist "venv\Scripts\python.exe" %PYTHON_CMD% -m venv venv || goto :error
"venv\Scripts\python.exe" -c "import flask, flask_sqlalchemy, openpyxl, requests, waitress" >nul 2>&1
if errorlevel 1 "venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
set "PORT=%CZ_PORT%"
if not defined PORT set "PORT=5100"
start "" "http://127.0.0.1:%PORT%"
"venv\Scripts\python.exe" -m app.chestny.runner --port %PORT%
exit /b %errorlevel%
:error
echo Installation or startup failed.
pause
exit /b 1
