@echo off
title Oracle location field check
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Inspect_Oracle_Location_Fields.ps1"
echo.
pause
