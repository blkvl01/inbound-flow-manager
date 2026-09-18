@echo off
setlocal EnableExtensions
title Flow Manager -- Build

set "FLOW_MANAGER_VERSION=%~1"
if "%FLOW_MANAGER_VERSION%"=="" set "FLOW_MANAGER_VERSION=0.0.0"

echo.
echo  ============================================
echo   FLOW MANAGER  --  Egyfajlos build
echo   HGL Group Hungary - Ecommerce Flow
echo  ============================================
echo.

:: Python ellenorzés
echo  [ELLENORZES] Python verzio...
python --version
if errorlevel 1 (
    echo  [HIBA] Python nem talalhato. Telepitsd a Python 3.10+ verziot.
    pause
    exit /b 1
)
echo.

:: Csomagok
echo  [1/4] Python csomagok telepitese / ellenorzese...
echo        (requirements.txt alapjan)
echo.
python -m pip install -r requirements.txt --quiet --quiet
if errorlevel 1 (
    echo  [HIBA] Csomag telepites sikertelen. Ellenorizd a net kapcsolatot.
    pause
    exit /b 1
)
echo  [OK]  Alap csomagok rendben.
echo.

echo  [2/4] PyInstaller ellenorzese / telepitese...
python -m pip install pyinstaller --quiet --quiet
if errorlevel 1 (
    echo  [HIBA] PyInstaller telepites sikertelen.
    pause
    exit /b 1
)
echo  [OK]  PyInstaller rendben.
echo.

:: Vendor assets
echo  [3/4] Vendor assets elkeszitese (Bootstrap + Inter font)...
python prepare_assets.py
if errorlevel 1 (
    echo  [HIBA] Assets letoltese sikertelen. Ellenorizd a net kapcsolatot.
    pause
    exit /b 1
)
echo  [OK]  Vendor assets rendben.
echo.

:: Build
echo  [4/4] Build inditasa...
echo        Regi dist/build mappak torlese...
if exist dist  rmdir /s /q dist
if exist build rmdir /s /q build
python scripts\write_build_version.py --version "%FLOW_MANAGER_VERSION%" --output "flow_manager_version.txt"
if errorlevel 1 (
    echo  [HIBA] A build verzio fajl letrehozasa sikertelen.
    exit /b 1
)
echo        PyInstaller futtatasa (ez 2-5 percet vehet igenybe)...
echo.

set "FLOW_MANAGER_VERSION=%FLOW_MANAGER_VERSION%"
:: A FlowManager.spec EXE konfiguracioja PyInstaller --onefile modot hasznal.
python -m PyInstaller --clean --noconfirm FlowManager.spec
set "PYINSTALLER_EXIT=%ERRORLEVEL%"
if exist flow_manager_version.txt del /q flow_manager_version.txt

if not "%PYINSTALLER_EXIT%"=="0" (
    echo.
    echo  [HIBA] PyInstaller build sikertelen. Nezd meg a fenti hibauzeneteket.
    pause
    exit /b 1
)

:: Kesz
echo.
echo  ============================================
echo   BUILD KESZ!
echo  ============================================
echo.
if not exist release mkdir release
if exist release\FlowManager.exe del /q release\FlowManager.exe
if exist release\manifest.json del /q release\manifest.json
copy /y "dist\FlowManager.exe" "release\FlowManager.exe" >nul
python scripts\create_release_manifest.py --exe "release\FlowManager.exe" --version "%FLOW_MANAGER_VERSION%" --output "release\manifest.json"
if errorlevel 1 (
    echo  [HIBA] Manifest keszitese sikertelen.
    exit /b 1
)
copy /y "docs\RELEASE_NOTES.md" "release\RELEASE_NOTES.md" >nul
copy /y "docs\UPDATER.md" "release\UPDATER.md" >nul
python scripts\verify_release.py release
if errorlevel 1 (
    echo  [HIBA] A kiadasi mappa ellenorzese sikertelen.
    exit /b 1
)
echo  Eredmeny:   release\
echo.
echo  Telepites:
echo    1. A release\FlowManager.exe fajlt tetszoleges irhato helyrol inditsd.
echo    2. A kozosen hasznalt Excel/OneDrive munkater marad a jelenlegi helyen.
echo    3. Frissiteskor az EXE melle kerul ideiglenesen a hash-elt letoltes.
echo.
pause
