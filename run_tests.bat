@echo off
title Form Auto-Filler - scenario tests
cd /d "%~dp0"
python -m unittest discover -s tests -v
echo.
pause
