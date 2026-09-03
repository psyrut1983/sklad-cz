@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions

title Chestny Znak

cd /d "%~dp0" || (
    echo [ERROR] Cannot open project folder: %~dp0
    pause
    exit /b 1
)

set "ROOT=%CD%"
set "LOG=%ROOT%\start_chestny.log"
set "VENV=%ROOT%\venv"
set "PORT=%CZ_PORT%"
if not defined PORT set "PORT=5100"

echo [%date% %time%] start_chestny.bat started >"%LOG%"
echo ROOT=%ROOT% >>"%LOG%"
echo PORT=%PORT% >>"%LOG%"

:: ---- 1. Find Python ----
set "PYTHON="
call :try_python "py -3.12"
if not defined PYTHON call :try_python "py -3.11"
if not defined PYTHON call :try_python "py -3.13"
if not defined PYTHON call :try_python "python"

if not defined PYTHON (
    echo.
    echo   [!] Python 3.11-3.13 not found.
    echo   Install Python 3.12 from python.org
    echo   and check "Add Python to PATH".
    echo.
    echo Python not found >>"%LOG%"
    pause
    exit /b 1
)
echo PYTHON=%PYTHON% >>"%LOG%"

:: ---- 2. Create venv ----
set "PY_VENV=%VENV%\Scripts\python.exe"

if not exist "%PY_VENV%" (
    echo.
    echo   Creating virtual environment...
    echo Creating venv... >>"%LOG%"
    if exist "%VENV%" rmdir /s /q "%VENV%" 2>>"%LOG%"
    echo CMD: %PYTHON% -m venv "%VENV%" >>"%LOG%"
    echo.
    %PYTHON% -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo   [!] Failed to create venv. Trying without ensurepip...
        echo.
        echo Venv with pip failed, trying --without-pip >>"%LOG%"
        if exist "%VENV%" rmdir /s /q "%VENV%" 2>>"%LOG%"
        %PYTHON% -m venv "%VENV%" --without-pip
        if errorlevel 1 (
            echo.
            echo   [!] Venv creation failed completely.
            echo   Try this in cmd.exe and paste the error:
            echo     py -3.12 -m venv D:\sklad-cz\test_venv
            echo.
            echo Venv creation failed >>"%LOG%"
            pause
            exit /b 1
        )
        echo.
        echo   Venv created without pip. Installing pip...
        echo Installing pip via ensurepip >>"%LOG%"
        "%PY_VENV%" -m ensurepip --upgrade
        if errorlevel 1 (
            echo   [!] Could not install pip.
            echo   Reinstall Python 3.12 from python.org
            echo   (select "Install pip" during setup).
            echo.
            pause
            exit /b 1
        )
        echo   Pip installed.
    )
    if not exist "%PY_VENV%" (
        echo   [!] python.exe missing after venv creation.
        echo   Reinstall Python 3.12 and try again.
        echo.
        pause
        exit /b 1
    )
    echo Venv created successfully >>"%LOG%"
)

set "PIP=%VENV%\Scripts\pip.exe"

:: ---- 3. Install dependencies ----
if not exist "%VENV%\requirements.installed" (
    echo.
    echo   Installing dependencies (first run)...
    echo Installing dependencies... >>"%LOG%"
    "%PIP%" install --upgrade pip >>"%LOG%" 2>&1
    "%PIP%" install -r "%ROOT%\requirements.txt" >>"%LOG%" 2>&1
    if errorlevel 1 (
        echo.
        echo   [!] Dependency installation failed.
        echo   See log: %LOG%
        echo.
        pause
        exit /b 1
    )
    copy nul "%VENV%\requirements.installed" >nul
    echo   Dependencies installed.
)

:: ---- 4. Verify runner import ----
"%PY_VENV%" -c "import app.chestny.runner; print('OK')" >>"%LOG%" 2>&1
if errorlevel 1 (
    echo.
    echo   [!] Cannot load application.
    echo   Dependencies may be incomplete. See log: %LOG%
    echo   Delete venv folder and run again.
    echo.
    pause
    exit /b 1
)

:: ---- 5. Check port ----
netstat -an 2>nul | findstr /c:"127.0.0.1:%PORT% " >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   [!] Port %PORT% already in use.
    echo   Application may already be running at:
    echo   http://127.0.0.1:%PORT%
    echo.
    echo Port %PORT% already in use >>"%LOG%"
    pause
    exit /b 1
)

:: ---- 6. Launch ----
echo.
echo   ============================================
echo     Chestny Znak
echo     http://127.0.0.1:%PORT%
echo   ============================================
echo.
echo   Starting server...

echo Starting server... >>"%LOG%"

:: Open browser after 3 seconds
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:%PORT%'" >nul 2>&1

"%PY_VENV%" -m app.chestny.runner --port %PORT%
set "EXIT_CODE=%ERRORLEVEL%"
echo Server exited with code %EXIT_CODE% >>"%LOG%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo   [!] Server error (code: %EXIT_CODE%).
    echo   See log: %LOG%
    echo.
    pause
)

pause
exit /b 0

:try_python
set "CANDIDATE=%~1"
set "DETECTED_VERSION="
set "PY_CHECK=%TEMP%\cz_python_check_%RANDOM%.txt"
%CANDIDATE% -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" >"%PY_CHECK%" 2>nul
if exist "%PY_CHECK%" (
    set /p DETECTED_VERSION=<"%PY_CHECK%"
    del "%PY_CHECK%" >nul 2>&1
)
if "%DETECTED_VERSION%"=="3.11" set "PYTHON=%CANDIDATE%"
if "%DETECTED_VERSION%"=="3.12" set "PYTHON=%CANDIDATE%"
if "%DETECTED_VERSION%"=="3.13" set "PYTHON=%CANDIDATE%"
exit /b 0
