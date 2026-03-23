@echo off
REM ============================================================================
REM AUTO-LOGIN BATCH FILE - Start Trading Bot with Automatic Zerodha Login
REM ============================================================================
REM This batch file will:
REM 1. Start the trading bot
REM 2. Automatically login to Zerodha if session expired
REM 3. Handle TOTP generation automatically
REM No manual intervention required!
REM ============================================================================

REM Set codepage to UTF-8 for Unicode support
chcp 65001 >nul

title Trading Bot - Auto Login Enabled
color 0A

echo.
echo ============================================================================
echo           TRADING BOT - AUTOMATIC LOGIN SYSTEM
echo ============================================================================
echo.
echo Starting bot with auto-login enabled...
echo If session is expired, bot will automatically:
echo   1. Open browser (headless)
echo   2. Login to Zerodha
echo   3. Generate and enter TOTP
echo   4. Start trading
echo.
echo No manual TOTP entry required!
echo.
echo ============================================================================
echo.

REM Change to backend directory
cd /d "%~dp0backend"

REM Set UTF-8 encoding for Python to avoid Unicode errors
set "PYTHONIOENCODING=utf-8"

REM Disable Python output buffering to see logs immediately
set "PYTHONUNBUFFERED=1"

REM Activate virtual environment if exists
if exist ".venv\Scripts\activate.bat" (
    echo [INFO] Activating virtual environment (.venv...
    call ".venv\Scripts\activate.bat"
) else (
    if exist "venv\Scripts\activate.bat" (
        echo [INFO] Activating virtual environment (venv...
        call "venv\Scripts\activate.bat"
    ) else (
        echo [WARNING] No virtual environment found. Using system Python...
    )
)

echo.
echo [INFO] Starting trading bot...
echo [INFO] Auto-login will trigger if session is invalid/expired
echo.
echo ============================================================================
echo.

REM Start the bot with unbuffered output (-u flag) for immediate log display
python -u main.py

REM Always keep window open to see status
echo.
echo ============================================================================
if errorlevel 1 (
    echo [ERROR] Bot stopped with error code: %errorlevel%
) else (
    echo [INFO] Bot has stopped
)
echo ============================================================================
echo.
pause
