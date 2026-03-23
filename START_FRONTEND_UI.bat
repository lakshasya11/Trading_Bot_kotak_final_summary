@echo off
REM ============================================================================
REM START FRONTEND UI - Launch Trading Bot Web Interface
REM ============================================================================

title Trading Bot - Frontend UI
color 0B

echo.
echo ============================================================================
echo           STARTING TRADING BOT FRONTEND UI
echo ============================================================================
echo.

REM Change to frontend directory
cd /d "%~dp0frontend"

REM Check if node_modules exists
if not exist "node_modules" (
    echo [WARNING] node_modules not found!
    echo [INFO] Installing dependencies first...
    echo.
    call npm install
    echo.
)

echo [INFO] Starting Vite development server...
echo [INFO] Frontend will open at: http://localhost:5173
echo.
echo ============================================================================
echo.

REM Start the frontend
call npm run dev

REM Keep window open if there's an error
if errorlevel 1 (
    echo.
    echo ============================================================================
    echo [ERROR] Frontend failed to start!
    echo ============================================================================
    echo.
    pause
)
