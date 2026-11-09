@echo off
title India chart (Fyers)
cd /d "%~dp0"

echo Checking Python...
set PY=
where py >nul 2>nul && (
    rem the Fyers libraries have no ready-made files for Python 3.14 yet, so prefer 3.12, 3.13 or 3.11
    for %%V in (3.12 3.13 3.11) do (
        if not defined PY (py -%%V --version >nul 2>nul && set PY=py -%%V)
    )
    if not defined PY set PY=py
)
if not defined PY set PY=python
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo Python was not found. Install Python 3 from https://www.python.org/downloads/
    echo and tick "Add Python to PATH" during install, then run this file again.
    pause
    exit /b 1
)

echo Using: & %PY% --version
echo Installing / checking libraries (first run takes a minute)...
%PY% -m pip install --quiet --disable-pip-version-check --prefer-binary fyers-apiv3 pandas numpy
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
