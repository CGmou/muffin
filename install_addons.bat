@echo off
REM ============================================================
REM  Muffin - install the DCC submitters (Blender add-on + Maya
REM  shelf) on this machine. Pass "blender" or "maya" to do just
REM  one; no argument does both. A console window stays open so
REM  you can read the result.
REM ============================================================
cd /d "%~dp0"
where py >nul 2>nul && (set "MUFFIN_PY=py") || (set "MUFFIN_PY=python")
where %MUFFIN_PY% >nul 2>nul || (
    echo Python was not found on this PC.
    echo Install Python 3 from https://www.python.org/downloads/ and re-run this installer.
    echo.
    pause
    exit /b 1
)
"%MUFFIN_PY%" "%~dp0integrations\install_addons.py" %*
echo.
pause
