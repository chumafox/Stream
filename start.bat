@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python 3.11+ не найден. Установите Python с https://python.org и включите Add to PATH.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Создаём локальное окружение Python...
    py -3 -m venv .venv
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, websockets, dotenv" >nul 2>nul
if errorlevel 1 (
    echo Устанавливаем зависимости...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :error
)

if not exist ".env" copy ".env.example" ".env" >nul

echo Запускаем StreamBridge. Для остановки нажмите Ctrl+C в этом окне.
".venv\Scripts\python.exe" run.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo StreamBridge остановлен с ошибкой. Прочитайте сообщение выше.
pause
exit /b 1
