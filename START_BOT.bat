@echo off
title V47.14 Trading Bot
echo ========================================
echo      V47.14 Trading Bot - Starting
echo ========================================
echo.

REM Check if setup was completed
if not exist "backend\venv" (
    echo ❌ Setup not completed! Please run SETUP_WINDOWS.bat first
    pause
    exit /b 1
)

REM Check if configuration files exist
REM Prefer user_profiles.json for auto-login, fallback to access_token.json
if not exist "backend\user_profiles.json" (
    if not exist "backend\access_token.json" (
        echo.
        echo ❌ No authentication configured!
        echo.
        echo Please configure ONE of the following:
        echo   1. user_profiles.json (RECOMMENDED - for auto-login)
        echo   2. access_token.json (fallback - manual token)
        echo.
        echo Run SETUP_WINDOWS.bat and follow AUTO_LOGIN_SETUP_GUIDE.md
        echo.
        pause
        exit /b 1
    ) else (
        echo ⚠️  Using access_token.json (manual authentication mode)
        echo    For better experience, configure user_profiles.json for auto-login
    )
) else (
    echo ✓ user_profiles.json found - auto-login will be used
)

echo 🚀 Starting V47.14 Trading Bot...
echo.
echo 📍 Services will be available at:
echo    Frontend: http://localhost:3000
echo    Backend:  http://localhost:8000
echo    API Docs: http://localhost:8000/docs
echo.
echo 🔐 Authentication Mode:
if exist "backend\user_profiles.json" (
    echo    ✓ Auto-login enabled (using user_profiles.json)
) else (
    echo    ✓ Manual authentication (using access_token.json)
)
echo.
echo ⏹️  To stop the bot, close this window or press Ctrl+C
echo.
echo Starting services...

REM Clean up any existing processes on ports 8000 and 5173
echo 🧹 Cleaning up any existing processes...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000.*LISTENING"') do (
    taskkill /F /PID %%a 2>nul >nul
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173.*LISTENING"') do (
    taskkill /F /PID %%a 2>nul >nul
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5174.*LISTENING"') do (
    taskkill /F /PID %%a 2>nul >nul
)
timeout /t 1 /nobreak >nul
echo ✓ Ports cleaned
echo.

REM Start backend in separate window with debug logs visible
echo [1/2] Starting backend server...
cd backend
start "Trading Bot - Backend Debug Logs" cmd /c "title Trading Bot - Backend Debug Logs && color 0A && venv\Scripts\activate.bat && set PYTHONUNBUFFERED=1 && set PYTHONIOENCODING=utf-8 && echo ============================================ && echo      Backend Server - Debug Logs Enabled && echo ============================================ && echo. && python -u main.py && echo. && echo Bot stopped. Press any key to close... && pause"
cd ..

REM Wait for backend to start and show startup complete message
echo ⏳ Waiting for backend to start...
echo    Check the GREEN terminal window for debug logs
timeout /t 5 /nobreak >nul
echo ✓ Backend started - debug logs visible in separate window
echo.

REM Start frontend
echo [2/2] Starting frontend server...
echo ⏳ This may take 30-60 seconds on first run...
cd frontend
npm run dev

REM If we get here, frontend stopped
echo.
echo ============================================================
echo.
echo 🛑 Frontend stopped. Cleaning up backend...
taskkill /f /im python.exe 2>nul
echo ✓ Backend stopped
echo ✓ Bot shutdown complete
echo.
pause