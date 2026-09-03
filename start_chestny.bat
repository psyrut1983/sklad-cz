@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
if errorlevel 1 (
    echo Cannot open project folder: %~dp0
    pause
    exit /b 1
)

set LOG=start_chestny.log
echo [%date% %time%] start_chestny.bat started >%LOG%

:: ---- Find Python ----
set PYTHON=py -3.12
%PYTHON% -c "print(1)" >nul 2>&1
if errorlevel 1 set PYTHON=py -3.11
%PYTHON% -c "print(1)" >nul 2>&1
if errorlevel 1 set PYTHON=py -3.13
%PYTHON% -c "print(1)" >nul 2>&1
if errorlevel 1 set PYTHON=python
%PYTHON% -c "print(1)" >nul 2>&1
if errorlevel 1 (
    echo Python 3.11-3.13 not found.
    echo Install Python 3.12 from python.org
    pause
    exit /b 1
)
echo Python: %PYTHON% >>%LOG%

:: ---- Create venv ----
if not exist venv\Scripts\python.exe (
    echo Creating virtual environment...
    rmdir /s /q venv >nul 2>&1
    %PYTHON% -m venv venv
    if errorlevel 1 (
        %PYTHON% -m venv venv --without-pip
        if errorlevel 1 (
            echo Venv creation failed.
            echo Try: py -3.12 -m venv D:\sklad-cz\venv
            pause
            exit /b 1
        )
        echo Installing pip...
        venv\Scripts\python -m ensurepip --upgrade
        if errorlevel 1 (
            echo Pip install failed. Reinstall Python 3.12.
            pause
            exit /b 1
        )
    )
    echo Venv created >>%LOG%
)

:: ---- Install dependencies ----
if not exist venv\requirements.installed (
    echo Installing dependencies (first run)...
    venv\Scripts\pip install --upgrade pip >nul 2>&1
    venv\Scripts\pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
    copy nul venv\requirements.installed >nul
    echo Dependencies installed >>%LOG%
)

:: ---- Check port ----
netstat -an 2>nul | findstr /c:":5100 " >nul 2>&1
if not errorlevel 1 (
    echo Port 5100 is busy. Is the app already running?
    echo Open http://127.0.0.1:5100
    pause
    exit /b 1
)

:: ---- Launch ----
echo.
echo ============================================
echo   Chestny Znak
echo   http://127.0.0.1:5100
echo ============================================
echo.
echo Starting server...
start http://127.0.0.1:5100
venv\Scripts\python -m app.chestny.runner --port 5100
echo.
pause
