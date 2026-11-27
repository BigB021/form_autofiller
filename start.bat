@echo off
cd /d "%~dp0"
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw "%~dp0autofill_app.py"
) else (
    start "" py -3w "%~dp0autofill_app.py"
)
