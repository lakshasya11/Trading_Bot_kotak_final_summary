@echo off
REM ============================================================================
REM Trading Bot Auto-Start with Automatic Login
REM ============================================================================
REM This batch file will:
REM 1. Check if session exists and is valid
REM 2. Automatically login to Zerodha if needed (using credentials from user_profiles.json)
REM 3. Start the trading bot
REM 
REM NO MANUAL INTERVENTION REQUIRED - Just double-click this file!
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
echo %YELLOW%          TRADING BOT - AUTO-START WITH AUTO-LOGIN%RESET%
echo %CYAN%============================================================================%RESET%
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo %RED%ERROR: Python is not installed or not in PATH%RESET%
    echo Please install Python 3.8 or higher
    pause
    exit /b 1
)

echo %GREEN%[OK] Python detected%RESET%

REM Check if virtual environment exists
if exist "backend\venv\Scripts\activate.bat" (
    echo %GREEN%[OK] Virtual environment found%RESET%
    call backend\venv\Scripts\activate.bat
) else if exist "backend\.venv\Scripts\activate.bat" (
    echo %GREEN%[OK] Virtual environment found%RESET%
    call backend\.venv\Scripts\activate.bat
) else (
    echo %YELLOW%[INFO] No virtual environment found, using system Python%RESET%
)

REM Check if required packages are installed
echo.
echo %CYAN%Checking dependencies...%RESET%
python -c "import selenium, pyotp" >nul 2>&1
if %errorlevel% neq 0 (
    echo %YELLOW%[WARNING] Auto-login dependencies not installed%RESET%
    echo %CYAN%Installing selenium and pyotp...%RESET%
    cd backend
    pip install selenium pyotp webdriver-manager -q
    cd ..
    echo %GREEN%[OK] Dependencies installed%RESET%
)

REM Check if credentials are configured
echo.
echo %CYAN%Checking auto-login configuration...%RESET%
python -c "import json; data=json.load(open('backend/user_profiles.json')); user=next((u for u in data['users'] if u['id']==data['active_user']), None); exit(0 if user and user.get('user_id','').strip() and user.get('password','').strip() and user.get('totp_secret','').strip() else 1)" >nul 2>&1

if %errorlevel% equ 0 (
    echo %GREEN%[OK] Auto-login is configured%RESET%
    echo %GREEN%    Bot will automatically login to Zerodha if needed%RESET%
) else (
    echo %YELLOW%[WARNING] Auto-login not configured%RESET%
    echo %YELLOW%    You'll need to login manually if session is expired%RESET%
    echo.
    echo To enable auto-login, fill these fields in backend\user_profiles.json:
    echo   - user_id
    echo   - password
    echo   - totp_secret
    echo.
    echo See AUTO_LOGIN_SETUP_GUIDE.md for instructions
    echo.
)

REM Start the trading bot
echo.
echo %CYAN%============================================================================%RESET%
echo %GREEN%Starting Trading Bot...%RESET%
echo %CYAN%============================================================================%RESET%
echo.
echo %YELLOW%The bot will automatically handle login if session is expired%RESET%
echo %YELLOW%Press Ctrl+C to stop the bot%RESET%
echo.

cd backend
python main.py

REM If bot exits, show message
echo.
echo %CYAN%============================================================================%RESET%
echo %YELLOW%Trading Bot Stopped%RESET%
echo %CYAN%============================================================================%RESET%
echo.
pause
