@echo off
title Flow Manager - Izolalt Oracle proba
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start_Oracle_Shadow_Trial.ps1"
echo.
pause
