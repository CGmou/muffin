@echo off
REM ============================================================
REM  Muffin - launch the Monitor GUI only (no console window)
REM ============================================================
cd /d "%~dp0"
start "" pythonw -m muffin.gui.monitor
