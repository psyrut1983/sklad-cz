@echo off
chcp 65001 >nul 2>&1
setlocal

cd /d "%~dp0" || (
    echo [ERROR] Cannot open project folder: %~dp0
    pause
    exit /b 1
)

echo ============================================
echo   Chestny Znak setup and launch
echo ============================================
echo.
echo setup.bat is kept for compatibility.
echo The recommended launcher is start_chestny.bat.
echo.

call "%~dp0start_chestny.bat"
exit /b %ERRORLEVEL%
