@echo off
setlocal

:: ============================================================
::  Flow Manager -- Indito szkript
::  Ezt a fajlt dupla kattintassal kell megnyitni.
::
::  Mit csinal:
::   1. A FlowManager mappat bemasolia a sajat gepedre (%LOCALAPPDATA%)
::      -- csak a megvaltozott fajlokat masolja (gyors, robocopy)
::   2. Beallitja, hogy a kozos "Betarolt tetelek" allapot
::      a SharePoint mappaban maradjon (nem a helyi masolt)
::   3. Elinditja az appot a helyi masoltbol (nincs file-lock)
:: ============================================================

set "SHAREPOINT_DIR=%~dp0"
set "SRC_APP=%SHAREPOINT_DIR%FlowManager"
set "SHARED_STATE=%SHAREPOINT_DIR%_shared_state"
set "LOCAL_APP=%LOCALAPPDATA%\InboundFlowManager\bin"
set "LOCAL_EXE=%LOCAL_APP%\FlowManager.exe"

:: --- Ellenorzes: megvan-e a FlowManager mappa a SharePointon? ---
if not exist "%SRC_APP%\FlowManager.exe" (
    echo.
    echo  [HIBA] A FlowManager mappa nem talalhato:
    echo         %SRC_APP%
    echo.
    echo  Bizonyosodj meg rola, hogy ez a fajl a FlowManager mappa
    echo  MELLETT van, nem belule.
    echo.
    pause
    exit /b 1
)

:: --- Helyi cel mappa letrehozasa ha meg nem letezik ---
if not exist "%LOCAL_APP%" mkdir "%LOCAL_APP%"

:: --- Inkrementalis masolas (csak a megvaltozott fajlok kerulnek at) ---
echo.
echo  Flow Manager frissitese a helyi gepen...
robocopy "%SRC_APP%" "%LOCAL_APP%" /MIR /NFL /NDL /NJH /NJS /NS /NC 2>nul

:: robocopy: 0-7 siker (0=semmi nem valtozott, 1=masolt, stb.)
::           8+ = hiba
if %ERRORLEVEL% GEQ 8 (
    echo.
    echo  [HIBA] A FlowManager masolasa sikertelen (kod: %ERRORLEVEL%).
    echo         Ellenorizd, hogy nincs-e engedely problema a kovetkezo mappanal:
    echo         %LOCAL_APP%
    echo.
    pause
    exit /b 1
)

:: --- Kozos shared state mutatasa a SharePoint mappara ---
set "FLOW_SHARED_STATE_DIR=%SHARED_STATE%"

:: --- Felhasznalonev-specifikus config a SharePoint mappara ---
::     Ha a user mas gepen jelentkezik be, a beallitasai megmaradnak.
set "FLOW_USER_CONFIG_DIR=%SHAREPOINT_DIR%_userconfig"

:: --- Inditas a helyi masoltbol ---
echo  Inditas: %LOCAL_EXE%
echo.
start "" "%LOCAL_EXE%"

endlocal
