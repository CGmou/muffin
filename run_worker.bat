@echo off
REM ============================================================
REM  Muffin - launch the Worker GUI (no console window)
REM ============================================================
cd /d "%~dp0"
start "" pythonw -m muffin.gui.worker
