@echo off
echo ========================================
echo RESTARTING TRADING BOT
echo ========================================
echo.

echo Step 1: Killing old processes...
taskkill /F /IM python.exe 2>nul
timeout /t 2 >nul

echo Step 2: Starting backend...
cd /d "%~dp0"
start /min cmd /c "python backend\main.py > backend_log.txt 2>&1"
timeout /t 5 >nul

echo Step 3: Starting frontend...
cd frontend
start /min cmd /c "npm start > frontend_log.txt 2>&1"
cd ..

echo.
echo ========================================
echo BOT RESTARTED!
echo ========================================
echo Backend: Running on port 8000
echo Frontend: Running on port 3000
echo.
echo Open browser: http://localhost:3000
echo.
pause
