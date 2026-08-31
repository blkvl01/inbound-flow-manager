@echo off
setlocal
title Flow Manager -- Build es Publish

set "TARGET=C:\Users\vilmos.bilek\OneDrive - HGL Group Hungary Kft\Ecommerce - Dokumentumok\Flow Manager"

echo.
echo  ============================================
echo   FLOW MANAGER  --  Build + Publish
echo  ============================================
echo.

:: --- Build ---
echo  [1/2] Build inditasa (build.bat)...
echo.
call "%~dp0build.bat"
if errorlevel 1 (
    echo.
    echo  [HIBA] A build sikertelen -- publish megszakitva.
    echo.
    pause
    exit /b 1
)

:: --- Ellenorzes ---
if not exist "%~dp0dist\FlowManager\FlowManager.exe" (
    echo.
    echo  [HIBA] A lefordított exe nem talalhato a dist\FlowManager\ mappaban.
    echo.
    pause
    exit /b 1
)

if not exist "%TARGET%" (
    echo.
    echo  [HIBA] A cel mappa nem talalhato:
    echo         %TARGET%
    echo.
    echo  Bizonyosodj meg rola, hogy a OneDrive szinkronizalva van.
    echo.
    pause
    exit /b 1
)

:: --- Publish ---
echo.
echo  [2/2] Fajlok masolasa a OneDrive mappara...
echo.

robocopy "%~dp0dist\FlowManager" "%TARGET%" /MIR /XD "_shared_state" "_userconfig" /NFL /NDL /NJH /NJS /NS /NC 2>nul
if %ERRORLEVEL% GEQ 8 (
    echo.
    echo  [HIBA] Robocopy hiba (kod: %ERRORLEVEL%).
    echo         Ellenorizd, hogy nincs-e engedely problema.
    echo.
    pause
    exit /b 1
)

:: --- Biztonsagos Oracle shadow indito a publikalt exe melle ---
copy /Y "%~dp0Inditas_Oracle.ps1" "%TARGET%\Inditas_Oracle.ps1" >nul
if errorlevel 1 (
    echo  [HIBA] Az Oracle PowerShell indito masolasa sikertelen.
    pause
    exit /b 1
)
copy /Y "%~dp0Inditas_Oracle.cmd" "%TARGET%\Inditas_Oracle.cmd" >nul
if errorlevel 1 (
    echo  [HIBA] Az Oracle CMD indito masolasa sikertelen.
    pause
    exit /b 1
)

echo.
echo  ============================================
echo   KESZ! Publikalt ide:
echo  ============================================
echo.
echo  %TARGET%
echo.
echo  Frissult fajlok:
echo    FlowManager.exe + fajlok -- egyenesen a Flow Manager mappaba
echo    Inditas_Oracle.cmd/.ps1 -- biztonsagos Oracle shadow inditas
echo.
echo  Erintetlen maradt:
echo    _shared_state\  -- kozos betarolt tetelek
echo    _userconfig\    -- felhasznaloi config fajlok
echo.
pause
endlocal
