@echo off
rem Start the whole chumba stack. Double-click to run.
setlocal
cd /d "%~dp0"
title chumba start

docker info >nul 2>&1
if not errorlevel 1 goto daemon_ok

echo Docker daemon is not running. Trying to start Docker Desktop...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" 2>nul

set /a tries=0
:wait_daemon
docker info >nul 2>&1
if not errorlevel 1 goto daemon_ok
set /a tries+=1
if %tries% geq 60 (
    echo.
    echo Docker daemon did not start within ~2 minutes.
    echo Start Docker Desktop manually and run this file again.
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
goto wait_daemon

:daemon_ok
echo Starting chumba stack ^(first run downloads images, takes a while^)...
docker compose up -d
if errorlevel 1 (
    echo.
    echo Failed to start the stack. See the error above.
    pause
    exit /b 1
)

docker compose ps
echo.
echo Stack is running: http://127.0.0.1:3000
echo Note: after a long idle the model sleeps; the first message
echo wakes it up and can take about 2 minutes.
pause
