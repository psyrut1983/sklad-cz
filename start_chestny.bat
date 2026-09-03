@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion

title Chestny Znak

cd /d "%~dp0" || (
    echo [ERROR] Cannot open project folder: %~dp0
    pause
    exit /b 1
)

set "ROOT=%CD%"
set "LOG=%ROOT%\start_chestny.log"
set "VENV=%ROOT%\venv"
set "MARKER=%VENV%\.installed"
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
    echo   [!] Supported Python not found.
    echo   Install Python 3.12 from python.org
    echo   and check "Add Python to PATH".
    echo   Python 3.14 is not used because Windows dependencies may fail.
    echo.
    echo Supported Python not found >>"%LOG%"
    pause
    exit /b 1
)
echo PYTHON=%PYTHON% >>"%LOG%"
echo PYTHON_VERSION=%PYTHON_VERSION% >>"%LOG%"

%PYTHON% --version >>"%LOG%" 2>&1
if errorlevel 1 (
    echo.
    echo   [!] Python found but not working.
    echo.
    echo Python check failed >>"%LOG%"
    pause
    exit /b 1
)

:: ---- 2. Create venv ----
set "PY_VENV=%VENV%\Scripts\python.exe"

if exist "%PY_VENV%" (
    "%PY_VENV%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3, 11), (3, 12), (3, 13)) else 1)" >>"%LOG%" 2>&1
    if errorlevel 1 (
        echo.
        echo   Existing venv uses unsupported Python. Recreating it...
        echo Recreating unsupported venv >>"%LOG%"
        rmdir /s /q "%VENV%" >>"%LOG%" 2>&1
        if errorlevel 1 (
            echo   [!] Failed to remove old venv folder.
            echo   Close Python processes and delete: %VENV%
            echo.
            pause
            exit /b 1
        )
        if exist "%MARKER%" del "%MARKER%" >nul 2>&1
    )
)

if not exist "%VENV%\Scripts\python.exe" (
    echo.
    echo   Creating virtual environment...
    echo Creating venv... >>"%LOG%"
    if exist "%VENV%" (
        echo Removing incomplete venv folder... >>"%LOG%"
        rmdir /s /q "%VENV%" >>"%LOG%" 2>&1
    )
    echo CMD: %PYTHON% -m venv "%VENV%" >>"%LOG%"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/d','/c','%PYTHON% -m venv ""%VENV%"" >> ""%LOG%"" 2>&1' -PassThru -WindowStyle Hidden; if (-not $p.WaitForExit(180000)) { Add-Content -Path '%LOG%' -Value '[ERROR] venv creation timeout after 180 seconds'; $p.Kill(); exit 124 }; exit $p.ExitCode"
    if errorlevel 1 (
        echo   [!] Failed to create venv.
        echo   Delete folder: %VENV%
        echo   Then run start_chestny.bat again.
        echo.
        echo Venv creation failed >>"%LOG%"
        pause
        exit /b 1
    )
    if not exist "%PY_VENV%" (
        echo   [!] Venv was created, but python.exe is missing.
        echo   Delete folder: %VENV%
        echo   Then run start_chestny.bat again.
        echo.
        echo Venv python.exe missing after creation >>"%LOG%"
        pause
        exit /b 1
    )
    echo Venv created successfully >>"%LOG%"
    del "%MARKER%" 2>nul
)

set "PIP=%VENV%\Scripts\pip.exe"

:: ---- 3. Check requirements.txt ----
if not exist "%ROOT%\requirements.txt" (
    echo.
    echo   [!] requirements.txt not found.
    echo.
    echo requirements.txt not found >>"%LOG%"
    pause
    exit /b 1
)

:: ---- 4. Install dependencies ----
if not exist "%MARKER%" (
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
    copy nul "%MARKER%" >nul
    echo   Dependencies installed.
)

:: ---- 5. Verify runner import ----
"%PY_VENV%" -c "import app.chestny.runner" >>"%LOG%" 2>&1
if errorlevel 1 (
    echo.
    echo   [!] Cannot load application.
    echo   Dependencies may be incomplete.
    echo   Delete venv folder and run again.
    echo   See log: %LOG%
    echo.
    pause
    exit /b 1
)

:: ---- 6. Check port ----
netstat -an 2>>"%LOG%" | findstr "127.0.0.1:%PORT% " >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   [!] Port %PORT% already in use.
    echo   Application may already be running.
    echo   http://127.0.0.1:%PORT%
    echo.
    echo Port %PORT% already in use >>"%LOG%"
    pause
    exit /b 1
)

:: ---- 7. Launch ----
echo.
echo   ============================================
echo     Chestny Znak
echo     http://127.0.0.1:%PORT%
echo   ============================================
echo.
echo   Starting server...

echo Starting server... >>"%LOG%"

:: Open browser after 3 seconds
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:%PORT%'"

"%PY_VENV%" -m app.chestny.runner --port %PORT%
set EXIT_CODE=!ERRORLEVEL!
echo Server exited with code !EXIT_CODE! >>"%LOG%"

if !EXIT_CODE! neq 0 (
    echo.
    echo   [!] Server error (code: !EXIT_CODE!).
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
if "%DETECTED_VERSION%"=="3.11" (
    set "PYTHON=%CANDIDATE%"
    set "PYTHON_VERSION=%DETECTED_VERSION%"
)
if "%DETECTED_VERSION%"=="3.12" (
    set "PYTHON=%CANDIDATE%"
    set "PYTHON_VERSION=%DETECTED_VERSION%"
)
if "%DETECTED_VERSION%"=="3.13" (
    set "PYTHON=%CANDIDATE%"
    set "PYTHON_VERSION=%DETECTED_VERSION%"
)
exit /b 0
