@echo off
title LeadHub Server - keep this window open
cd /d "%~dp0"
cls
echo.
echo   Starting LeadHub...
echo.
python app.py
echo.
echo   LeadHub has stopped.
pause
