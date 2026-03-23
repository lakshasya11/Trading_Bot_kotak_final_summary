@echo off
REM Silent launcher - No terminal window
REM This script runs the GUI launcher without showing any console windows

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM Run Python GUI launcher hidden
start "" /B pythonw "%SCRIPT_DIR%trading_bot_launcher.py"

REM Exit immediately (window closes)
exit
