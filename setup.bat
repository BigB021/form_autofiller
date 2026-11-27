@echo off
setlocal
title Form Auto-Filler - one-time setup
cd /d "%~dp0"

set PY_VERSION=3.13.7
set PY_ARCH=amd64
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set PY_ARCH=arm64
set PY_URL=https://www.python.org/ftp/python/%PY_VERSION%/python-%PY_VERSION%-%PY_ARCH%.exe

echo =============================================
echo   Form Auto-Filler - one-time setup
echo =============================================
echo.
echo Step 1 of 3: Python
call :find_python
if defined PY (
    echo   Python is already installed.
    goto have_python
)
echo   Python is not installed yet. Downloading it (about 30 MB^)...
set INSTALLER=%TEMP%\python-%PY_VERSION%-setup.exe
curl -L --fail --progress-bar -o "%INSTALLER%" "%PY_URL%"
if errorlevel 1 (
    echo   The download did not work. Check the internet connection and try again.
    goto failed
)
echo   Installing Python (a small progress window opens, wait until it closes^)...
rem just for this user: no administrator password needed
"%INSTALLER%" /passive InstallAllUsers=0 PrependPath=1 Include_tcltk=1 Include_pip=1 Include_launcher=0 Include_test=0 Shortcuts=0
del "%INSTALLER%" >nul 2>&1
call :find_python
if not defined PY (
    echo   Python was installed but cannot be used.
    goto failed
)
echo   Python installed.

:have_python
echo.
echo Step 2 of 3: installing what the app needs (this can take a minute)...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import tkinter" >nul 2>&1 || rmdir /s /q ".venv"
)
if not exist ".venv\Scripts\python.exe" (
    "%PY%" -m venv .venv
    if errorlevel 1 (
        echo   Could not prepare the app folder.
        goto failed
    )
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo   Could not install what the app needs. Check the internet connection and try again.
    goto failed
)
echo   Done.

echo.
echo Step 3 of 3: web browser
set BROWSER=
if exist "%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe" set BROWSER=Brave
if exist "%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe" set BROWSER=Brave
if not defined BROWSER if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set BROWSER=Google Chrome
if not defined BROWSER if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set BROWSER=Google Chrome
if not defined BROWSER if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set BROWSER=Microsoft Edge
if defined BROWSER (
    echo   The app will use %BROWSER%.
) else (
    echo   !! No Brave, Chrome or Edge found. Install Google Chrome or Brave, then you are ready.
)

echo.
echo =============================================
echo   Setup finished. Now double-click start.bat
echo =============================================
pause
exit /b 0

:failed
echo.
echo Something went wrong. Take a screenshot of this window and send it
echo to the person who gave you the app.
pause
exit /b 1

rem ---- finds a Python 3.9+ that has tkinter; sets PY to its full path (empty if none)
:find_python
set PY=
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~D\python.exe" call :try_python "%%~D\python.exe"
)
for /f "delims=" %%P in ('where python 2^>nul') do call :try_python "%%~P"
exit /b 0

:try_python
if defined PY exit /b 0
"%~1" -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
exit /b 0
