@echo off
REM ============================================================
REM  Muffin Node - manager control + settings (no console window)
REM ============================================================
REM  pythonw.exe runs the GUI without a console; "start" detaches
REM  it so this batch window closes immediately.

cd /d "%~dp0"
start "" pythonw -m muffin.gui
