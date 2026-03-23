@echo off
REM ============================================================================
REM Setup Auto-Login - Quick Batch Version
REM ============================================================================
REM This batch file helps you set up automatic login
REM ============================================================================

setlocal enabledelayedexpansion

REM Set colors
set "GREEN=[92m"
set "YELLOW=[93m"
set "RED=[91m"
set "CYAN=[96m"
set "WHITE=[97m"
set "RESET=[0m"

REM Get script directory
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

cls
echo.
echo %CYAN%============================================================================%RESET%
echo %YELLOW%          AUTO-LOGIN SETUP WIZARD%RESET%
echo %CYAN%============================================================================%RESET%
echo.

REM Step 1: Check Python
echo %GREEN%Step 1: Checking Python...%RESET%
echo ----------------------------------------------------------------------------
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo %RED%ERROR: Python is not installed%RESET%
    echo Please install Python 3.8 or higher from https://www.python.org/
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do set PYTHON_VERSION=%%i
echo %GREEN%[OK] %PYTHON_VERSION% detected%RESET%
echo.

REM Step 2: Check/Create Virtual Environment
echo %GREEN%Step 2: Setting up Virtual Environment...%RESET%
echo ----------------------------------------------------------------------------
if exist "backend\venv\Scripts\activate.bat" (
    echo %GREEN%[OK] Virtual environment found%RESET%
    call backend\venv\Scripts\activate.bat
) else if exist "backend\.venv\Scripts\activate.bat" (
    echo %GREEN%[OK] Virtual environment found%RESET%
    call backend\.venv\Scripts\activate.bat
) else (
    echo %YELLOW%Creating virtual environment...%RESET%
    cd backend
    python -m venv venv
    call venv\Scripts\activate.bat
    cd ..
    echo %GREEN%[OK] Virtual environment created%RESET%
)
echo.

REM Step 3: Install Dependencies
echo %GREEN%Step 3: Installing Dependencies...%RESET%
echo ----------------------------------------------------------------------------
echo %CYAN%Installing selenium, pyotp, webdriver-manager...%RESET%
cd backend
pip install --upgrade pip >nul 2>&1
pip install selenium pyotp webdriver-manager -q
if %errorlevel% equ 0 (
    echo %GREEN%[OK] Dependencies installed successfully%RESET%
) else (
    echo %RED%[ERROR] Failed to install dependencies%RESET%
)
cd ..
echo.

REM Step 4: Check Chrome
echo %GREEN%Step 4: Checking Chrome Browser...%RESET%
echo ----------------------------------------------------------------------------
set CHROME_FOUND=0
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1

if %CHROME_FOUND% equ 1 (
    echo %GREEN%[OK] Chrome browser detected%RESET%
) else (
    echo %YELLOW%[WARNING] Chrome not found%RESET%
    echo Auto-login requires Chrome browser
    echo Download from: https://www.google.com/chrome/
)
echo.

REM Step 5: Credentials Setup Guide
echo %GREEN%Step 5: Configure Credentials...%RESET%
echo ----------------------------------------------------------------------------
echo.
echo You need to add these credentials to backend\user_profiles.json:
echo.
echo %WHITE%  1. user_id      - Your Zerodha User ID (e.g., AB1234)%RESET%
echo %WHITE%  2. password     - Your Zerodha login password%RESET%
echo %WHITE%  3. totp_secret  - Your 2FA secret key%RESET%
echo.
echo %CYAN%========================================================================%RESET%
echo %YELLOW%              HOW TO GET YOUR TOTP SECRET KEY%RESET%
echo %CYAN%========================================================================%RESET%
echo.
echo %WHITE%1. Login to Zerodha Kite → Settings → Security%RESET%
echo %WHITE%2. Click "Two-factor authentication" → "Re-generate"%RESET%
echo %WHITE%3. When QR code appears, click "Can't scan the QR code?"%RESET%
echo %WHITE%4. Copy the SECRET KEY (e.g., JBSWY3DPEHPK3PXP)%RESET%
echo %WHITE%5. Paste it into backend\user_profiles.json → totp_secret field%RESET%
echo.
echo %RED%IMPORTANT: This secret is like a password - keep it secure!%RESET%
echo.
echo %CYAN%========================================================================%RESET%
echo.

REM Check if already configured
python -c "import json; data=json.load(open('backend/user_profiles.json')); user=next((u for u in data['users'] if u['id']==data['active_user']), None); exit(0 if user and user.get('user_id','').strip() and user.get('password','').strip() and user.get('totp_secret','').strip() else 1)" >nul 2>&1

if %errorlevel% equ 0 (
    echo %GREEN%[OK] Credentials already configured!%RESET%
    echo.
    set /p SKIP_EDIT="Skip editing user_profiles.json? (y/n): "
    if /i "!SKIP_EDIT!" neq "y" (
        notepad backend\user_profiles.json
    )
) else (
    echo %YELLOW%[ACTION REQUIRED] Please add your credentials%RESET%
    echo.
    set /p OPEN_FILE="Open user_profiles.json now? (y/n): "
    if /i "!OPEN_FILE!" equ "y" (
        notepad backend\user_profiles.json
        echo.
        echo %GREEN%Save the file when done and press any key...%RESET%
        pause >nul
    )
)

echo.

REM Step 6: Test Setup
echo %GREEN%Step 6: Test Auto-Login...%RESET%
echo ----------------------------------------------------------------------------
echo.
set /p RUN_TEST="Run auto-login test now? (y/n): "
if /i "!RUN_TEST!" equ "y" (
    echo.
    echo %CYAN%Running test...%RESET%
    echo.
    python test_auto_login.py
)

REM Final Summary
echo.
echo %CYAN%============================================================================%RESET%
echo %GREEN%          SETUP COMPLETE!%RESET%
echo %CYAN%============================================================================%RESET%
echo.
echo %WHITE%Next Steps:%RESET%
echo.
echo %YELLOW%1. Make sure you filled all credentials in backend\user_profiles.json%RESET%
echo    - user_id
echo    - password
echo    - totp_secret
echo.
echo %YELLOW%2. Test the setup:%RESET%
echo    Double-click: TEST_AUTO_LOGIN.bat
echo.
echo %YELLOW%3. Start your bot:%RESET%
echo    Double-click: START_BOT_WITH_AUTO_LOGIN.bat
echo.
echo %GREEN%The bot will now automatically login when needed!%RESET%
echo %GREEN%No more manual TOTP entry required!%RESET%
echo.
pause
