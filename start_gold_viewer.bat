@echo off
title Gold live viewer
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
%PY% -m pip install --quiet --disable-pip-version-check MetaTrader5 pandas matplotlib numpy
if errorlevel 1 (
    echo.
    echo Library install failed. Copy the message above and share it.
    pause
    exit /b 1
)

echo.
echo Make sure MetaTrader 5 is open and logged in to your Exness account.
echo Starting the live viewer... close the chart window to stop.
echo.
%PY% live_ha_viewer.py

echo.
pause
