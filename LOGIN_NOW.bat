@echo off
REM ============================================================================
REM Manual Auto-Login Trigger
REM ============================================================================
REM This batch file manually triggers the auto-login process
REM Use this to generate a fresh session without starting the bot
REM ============================================================================

setlocal enabledelayedexpansion

REM Set colors
set "GREEN=[92m"
set "YELLOW=[93m"
set "RED=[91m"
set "CYAN=[96m"
set "RESET=[0m"

REM Get script directory
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo.
echo %CYAN%============================================================================%RESET%
echo %YELLOW%          MANUAL AUTO-LOGIN TRIGGER%RESET%
echo %CYAN%============================================================================%RESET%
echo.
echo This will log into Zerodha and generate a fresh session token
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo %RED%ERROR: Python is not installed%RESET%
    pause
    exit /b 1
)

REM Activate virtual environment if exists
if exist "backend\venv\Scripts\activate.bat" (
    call backend\venv\Scripts\activate.bat
) else if exist "backend\.venv\Scripts\activate.bat" (
    call backend\.venv\Scripts\activate.bat
)

REM Run manual login
echo %CYAN%Attempting auto-login...%RESET%
echo.
python manual_auto_login.py

echo.
pause
