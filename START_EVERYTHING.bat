@echo off
REM ============================================================================
REM START EVERYTHING - Complete Trading Bot System
REM ============================================================================
REM This will start:
REM 1. Backend (Trading Engine + API) with Auto-Login
REM 2. Frontend (Web UI)
REM Both in separate windows for easy monitoring
REM ============================================================================

title Trading Bot - Master Launcher
color 0E

echo.
echo ============================================================================
echo           TRADING BOT - COMPLETE SYSTEM STARTUP
echo ============================================================================
echo.
echo This will start:
echo   [1] Backend  - Trading engine with auto-login (Port 8000)
echo   [2] Frontend - Web interface (Port 5173)
echo   [3] Browser  - Auto-open web UI
echo.
echo Everything will open automatically!
echo.
echo ============================================================================
echo.

REM Store the current directory
set "ROOT_DIR=%~dp0"

echo [STEP 1/2] Starting Backend (Trading Engine + Auto-Login)...
echo.

REM Start backend in a new window
start "Trading Bot - Backend (Auto-Login)" cmd /k "cd /d "%ROOT_DIR%" && START_BOT_AUTO_LOGIN.bat"

REM Start frontend immediately (no need to wait for backend)
echo.
echo [STEP 2/2] Starting Frontend (Web UI)...
echo.

REM Start frontend in a new window
start "Trading Bot - Frontend UI" cmd /k "cd /d "%ROOT_DIR%" && START_FRONTEND_UI.bat"

REM Wait 3 seconds then open browser
echo [INFO] Waiting 3 seconds for frontend to start...
timeout /t 3 /nobreak >nul

REM Auto-open browser
echo [STEP 3/3] Opening web interface in browser...
echo.
start http://localhost:5173

echo.
echo ============================================================================
echo           STARTUP COMPLETE!
echo ============================================================================
echo.
echo Backend:  http://localhost:8000  (Trading Engine + API)
echo Frontend: http://localhost:5173  (Web Interface)
echo.
echo [✓] Backend window opened  - Trading engine with auto-login
echo [✓] Frontend window opened - Web UI server
echo [✓] Browser opened         - http://localhost:5173
echo.
echo ============================================================================
echo.
echo TIPS:
echo - Browser will open automatically in 5 seconds
echo - Wait 5-10 seconds for everything to fully load
echo - If auto-login triggers, it takes 10-15 seconds
echo - Watch the backend window for login status
echo.
echo To stop everything:
echo - Close both terminal windows
echo - Or press Ctrl+C in each window
echo.
echo ============================================================================
echo.
echo You can close this window now. Both services are running.
echo.

pause
