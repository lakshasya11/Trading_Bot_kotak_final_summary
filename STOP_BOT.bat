@echo off
echo ========================================
echo      V47.14 Trading Bot - Stopping
echo ========================================
echo.

echo 🛑 Stopping all bot processes...

REM Kill processes on port 8000 (backend)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000.*LISTENING"') do (
    taskkill /F /PID %%a 2>nul
    if %errorlevel% == 0 (
        echo ✅ Backend stopped (port 8000)
    )
)

REM Kill processes on port 5173 (frontend)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173.*LISTENING"') do (
    taskkill /F /PID %%a 2>nul
    if %errorlevel% == 0 (
        echo ✅ Frontend stopped (port 5173)
    )
)

REM Kill processes on port 5174 (alternate frontend)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5174.*LISTENING"') do (
    taskkill /F /PID %%a 2>nul
    if %errorlevel% == 0 (
        echo ✅ Frontend stopped (port 5174)
    )
)

REM Additional cleanup for any stray processes
taskkill /f /fi "WINDOWTITLE eq V47.14 Trading Bot" 2>nul

echo.
echo ✅ V47.14 Trading Bot stopped successfully
echo.
pause