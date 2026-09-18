@echo off
setlocal
title Flow Manager -- GitHub Release csomag

if "%~1"=="" (
    echo Hasznalat: publish.bat ^<verzio^>
    echo Pelda:     publish.bat 1.2.0
    exit /b 2
)

echo.
echo  ============================================
echo   FLOW MANAGER  --  GitHub Release csomag
echo  ============================================
echo.

:: --- Build ---
echo  [1/2] Build inditasa (build.bat)...
echo.
call "%~dp0build.bat" "%~1"
if errorlevel 1 (
    echo.
    echo  [HIBA] A build sikertelen -- publish megszakitva.
    echo.
    pause
    exit /b 1
)

:: --- Ellenorzes ---
if not exist "%~dp0release\FlowManager.exe" (
    echo.
    echo  [HIBA] A lefordított exe nem talalhato a release mappaban.
    echo.
    pause
    exit /b 1
)

:: A kiadasi csomag tudatosan nem kerül a kozosen hasznalt OneDrive-munkaterbe.
:: Feltolteshez a release/ mappabol csak a buildelt asseteket hasznald.

echo.
echo  ============================================
echo   KESZ! Helyi kiadasi csomag:
echo  ============================================
echo.
echo  %~dp0release\
echo.
echo  GitHub Release assetek:
echo    FlowManager.exe
echo    manifest.json
echo.
echo  A kozos OneDrive munkater erintetlen marad.
echo.
pause
endlocal
