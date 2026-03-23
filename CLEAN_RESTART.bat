@echo off
echo ========================================
echo CLEAN RESTART - Trading Bot
echo ========================================
echo.

echo [1/6] Stopping all processes...
taskkill /F /IM python.exe 2>nul
taskkill /F /IM node.exe 2>nul
timeout /t 2 >nul

echo [2/6] Backing up databases...
if exist trading_bot.db move /Y trading_bot.db trading_bot_backup_%date:~-4%%date:~3,2%%date:~0,2%_%time:~0,2%%time:~3,2%.db >nul 2>&1
if exist trading_data_today.db move /Y trading_data_today.db trading_data_backup_%date:~-4%%date:~3,2%%date:~0,2%_%time:~0,2%%time:~3,2%.db >nul 2>&1

echo [3/6] Clearing daemon log...
echo Clean restart at %date% %time% > daemon.log

echo [4/6] Starting backend...
cd /d "%~dp0"
start "Trading Bot Backend" /MIN cmd /c "python backend\main.py 2>&1 | tee backend_log.txt"
timeout /t 5 >nul

echo [5/6] Checking backend health...
powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:8000/health' -TimeoutSec 3 -UseBasicParsing; Write-Host '  ✅ Backend is healthy' } catch { Write-Host '  ❌ Backend not responding' }"

echo [6/6] Starting frontend...
cd frontend
start "Trading Bot Frontend" /MIN cmd /c "npm start 2>&1 | tee frontend_log.txt"
cd ..

echo.
echo ========================================
echo RESTART COMPLETE!
echo ========================================
echo.
echo Backend:  http://localhost:8000
echo Frontend: http://localhost:3000
echo.
echo Waiting 10 seconds before opening browser...
timeout /t 10 >nul
start http://localhost:3000

echo.
echo ✅ Trading bot is starting...
echo    Watch for scalper logs in the dashboard!
echo.
pause
