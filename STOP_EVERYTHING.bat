@echo off
REM ============================================================================
REM STOP EVERYTHING - Stop All Trading Bot Services
REM ============================================================================

title Stop Trading Bot
color 0C

echo.
echo ============================================================================
echo           STOPPING ALL TRADING BOT SERVICES
echo ============================================================================
echo.

echo [INFO] Stopping all Python processes (Backend)...
taskkill /F /IM python.exe /T 2>nul
if errorlevel 1 (
    echo [WARNING] No Python processes found
) else (
    echo [OK] Backend stopped
)

echo.
echo [INFO] Stopping all Node processes (Frontend)...
taskkill /F /IM node.exe /T 2>nul
if errorlevel 1 (
    echo [WARNING] No Node processes found
) else (
    echo [OK] Frontend stopped
)

echo.
echo ============================================================================
echo           ALL SERVICES STOPPED
echo ============================================================================
echo.

pause
