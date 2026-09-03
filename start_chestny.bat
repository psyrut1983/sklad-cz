@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

title Честный Знак — запуск

set "ROOT=%~dp0"
set "VENV=%ROOT%venv"
set "MARKER=%VENV%\.installed"
set "PORT=5100"

:: ── 1. Найти Python ────────────────────────────────────────────────
set "PYTHON="
where python >nul 2>&1
if not errorlevel 1 set "PYTHON=python"
if not defined PYTHON (
    where py >nul 2>&1
    if not errorlevel 1 set "PYTHON=py -3"
)
if not defined PYTHON (
    echo.
    echo   [!] Python не найден. Установите Python 3.10+ и добавьте в PATH.
    echo.
    pause
    exit /b 1
)

:: Проверим, что python реально есть
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [!] Python найден, но не запускается: %PYTHON%
    echo.
    pause
    exit /b 1
)

:: ── 2. Виртуальное окружение ───────────────────────────────────────
if not exist "%VENV%\Scripts\python.exe" (
    echo.
    echo   Создание виртуального окружения...
    %PYTHON% -m venv "%VENV%"
    if errorlevel 1 (
        echo   [!] Не удалось создать venv.
        pause
        exit /b 1
    )
    del "%MARKER%" 2>nul
    echo   Готово.
)

set "PY_VENV=%VENV%\Scripts\python.exe"
set "PIP=%VENV%\Scripts\pip.exe"

:: ── 3. Установка зависимостей ──────────────────────────────────────
if not exist "%MARKER%" (
    echo.
    echo   Установка зависимостей (первый запуск)...
    echo.
    "%PIP%" install --upgrade pip >nul 2>&1
    "%PIP%" install -r "%ROOT%requirements.txt"
    if errorlevel 1 (
        echo.
        echo   [!] Ошибка установки зависимостей.
        echo       Проверьте подключение к интернету и requirements.txt
        pause
        exit /b 1
    )
    copy nul "%MARKER%" >nul
    echo   Зависимости установлены.
)

:: ── 4. Проверить порт ──────────────────────────────────────────────
netstat -an 2>nul | findstr "127.0.0.1:%PORT% " >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   [!] Порт %PORT% уже занят.
    echo       Возможно, приложение уже запущено.
    echo       http://127.0.0.1:%PORT%
    pause
    exit /b 1
)

:: ── 5. Запустить сервер ────────────────────────────────────────────
echo.
echo   ============================================
echo     Честный Знак
echo     http://127.0.0.1:%PORT%
echo   ============================================
echo.
echo   Запуск сервера...

:: Открыть браузер через 3 секунды (даём серверу время стартануть)
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%PORT%"

:: Запуск приложения
"%PY_VENV%" -m app.chestny.runner --port %PORT%
if errorlevel 1 (
    echo.
    echo   [!] Сервер завершился с ошибкой (код: !errorlevel!).
    pause
    exit /b 1
)

pause
