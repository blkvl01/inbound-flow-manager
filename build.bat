@echo off
title Flow Manager -- Build

echo.
echo  ============================================
echo   FLOW MANAGER  --  Build szkript
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
echo        PyInstaller futtatasa (ez 2-5 percet vehet igenybe)...
echo.

python -m PyInstaller ^
  --onedir ^
  --name "FlowManager" ^
  --add-data "assets;assets" ^
  --collect-all dash ^
  --collect-all dash_bootstrap_components ^
  --collect-all plotly ^
  --copy-metadata plotly ^
  --copy-metadata dash ^
  --copy-metadata dash-bootstrap-components ^
  --copy-metadata flask ^
  --copy-metadata werkzeug ^
  --copy-metadata pandas ^
  --hidden-import pyxlsb ^
  --hidden-import pyxlsb.biff_record ^
  --hidden-import openpyxl ^
  --hidden-import openpyxl.styles ^
  --hidden-import openpyxl.utils ^
  --collect-all oracledb ^
  --collect-all cryptography ^
  --hidden-import oracledb ^
  --hidden-import cryptography.hazmat.primitives.kdf ^
  --hidden-import pandas ^
  --hidden-import pandas.io.formats.style ^
  --hidden-import flask ^
  --hidden-import flask_compress ^
  --hidden-import multiprocessing ^
  app.py

if errorlevel 1 (
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
echo  Eredmeny:   dist\FlowManager\
echo.
echo  Telepites:
echo    1. Masold a dist\FlowManager\ mappa TARTALMAT
echo       a kozos OneDrive mappaba
echo    2. Mindenki dupla klikk: FlowManager.exe
echo    3. Elso inditaskor automatikusan generelodik a config.json
echo.
pause
