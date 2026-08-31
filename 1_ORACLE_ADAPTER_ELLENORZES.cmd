@echo off
title Flow Manager - UJ Oracle adapter ellenorzese
cd /d "%~dp0"
echo.
echo  UJ FLOW MANAGER ORACLE ADAPTER ELLENORZESE
echo  Ez nem a korabbi Excel-Oracle osszehasonlitas.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Check_Oracle_Adapter.ps1"
echo.
echo  Az ablak bezarhato.
pause
