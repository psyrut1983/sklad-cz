@echo off
chcp 65001 >nul 2>&1
setlocal

cd /d "%~dp0" || (
    echo [ERROR] Cannot open project folder: %~dp0
    pause
    exit /b 1
)

if not exist "%~dp0start_chestny.bat" (
    echo [ERROR] start_chestny.bat not found.
    echo Download the full project folder from GitHub again.
    pause
    exit /b 1
)

call "%~dp0start_chestny.bat"
exit /b %ERRORLEVEL%
