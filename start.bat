@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "%~dp0autofill_app.py"
    exit /b 0
)
rem set up before setup.bat made the .venv folder: use the Python on this PC
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw "%~dp0autofill_app.py"
) else (
    start "" py -3w "%~dp0autofill_app.py"
)
