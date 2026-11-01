@echo off
rem Stop the whole chumba stack. Double-click to run.
setlocal
cd /d "%~dp0"
title chumba stop

docker compose stop
if errorlevel 1 (
    echo.
    echo Failed to stop the stack. See the error above.
    pause
    exit /b 1
)

echo.
echo Stack stopped. The model is unloaded: VRAM/RAM are free.
echo Data ^(chats, notes, study progress^) is kept in Docker volumes.
pause
