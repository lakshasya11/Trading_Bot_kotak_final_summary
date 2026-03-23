@echo off
title Trading Bot - Automated Startup
color 0A
echo.
echo ========================================
echo    🚀 TRADING BOT LAUNCHER 🚀
echo ========================================
echo.

REM Get the directory where this batch file is located
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo [1/5] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ ERROR: Python not found!
    echo.
    echo Please install Python 3.9+ from: https://python.org
    echo Make sure to check "Add Python to PATH" during installation
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo ✅ Python %PYTHON_VERSION% is installed

echo.
echo [2/5] Setting up virtual environment...
cd backend
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ❌ Failed to create virtual environment
        pause
        exit /b 1
    )
    echo ✅ Virtual environment created
) else (
    echo ✅ Virtual environment already exists
)

echo.
echo [3/5] Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo ❌ Failed to activate virtual environment
    pause
    exit /b 1
)

echo.
echo [4/5] Installing/updating dependencies...
echo This may take a moment on first run...
pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo ❌ Failed to install dependencies
    echo Trying again with verbose output...
    pip install -r requirements.txt
    pause
    exit /b 1
)
echo ✅ Dependencies installed successfully

echo.
echo [5/5] Checking configuration...
if not exist ".env" (
    echo ⚠️  Creating .env template file...
    echo # Zerodha Kite API Credentials > .env
    echo API_KEY="your_kite_api_key_here" >> .env
    echo API_SECRET="your_kite_api_secret_here" >> .env
    echo. >> .env
    echo # Replace the values above with your actual credentials >> .env
    echo # Get them from: https://kite.trade/ >> .env
    echo.
    echo ❌ SETUP REQUIRED: Please edit backend\.env with your real API credentials!
    echo.
    echo 1. Open: backend\.env in notepad
    echo 2. Replace "your_kite_api_key_here" with your actual API key
    echo 3. Replace "your_kite_api_secret_here" with your actual API secret
    echo 4. Save the file and run this script again
    echo.
    pause
    exit /b 1
)
echo ✅ Configuration file found

echo.
echo ========================================
echo    🎯 STARTING TRADING BOT...
echo ========================================
echo.
echo 📡 Backend URL: http://localhost:8000
echo 📊 API Documentation: http://localhost:8000/docs
echo 📈 WebSocket Status: Will connect automatically
echo.
echo ℹ️  Bot is starting... Please wait 10-15 seconds
echo 🔴 Press Ctrl+C to stop the bot
echo.

REM Start the bot using the exact command from README
uvicorn main:app --reload --port 8000

echo.
echo 🔴 Trading Bot has stopped.
echo.
echo Press any key to exit...
pause >nul