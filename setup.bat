@echo off
title Form Auto-Filler - one-time setup
cd /d "%~dp0"

set PY=python
where python >nul 2>&1 || set PY=py -3

echo Installing what the app needs. This can take a minute...
echo.
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo.
echo ==========================================
echo  Setup finished. Now double-click start.bat
echo  The app uses Brave (or Chrome / Edge) that is already on this PC.
echo ==========================================
pause
exit /b 0

:failed
echo.
echo Something went wrong. Make sure Python 3 is installed (python.org),
echo then run setup.bat again. Send a screenshot of this window if it still fails.
pause
exit /b 1
