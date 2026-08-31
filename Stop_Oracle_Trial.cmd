@echo off
title Flow Manager - Oracle proba leallitasa
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Stop_Oracle_Shadow_Trial.ps1"
echo.
pause
