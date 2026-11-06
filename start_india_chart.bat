@echo off
title India chart (Fyers)
cd /d "%~dp0"

echo Checking Python...
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo Python was not found. Install Python 3 from https://www.python.org/downloads/
    echo and tick "Add Python to PATH" during install, then run this file again.
    pause
    exit /b 1
)

echo Installing / checking libraries (first run takes a minute)...
%PY% -m pip install --quiet --disable-pip-version-check fyers-apiv3 pandas numpy
if errorlevel 1 (
    echo.
    echo Library install failed. Copy the message above and share it.
    pause
    exit /b 1
)

echo.
echo Starting the India chart... it opens in your browser. Keep this window open
echo during market hours so ticks are recorded. Close it to stop.
echo.
%PY% india_chart.py

echo.
pause
