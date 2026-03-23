@echo off
REM ============================================================================
REM MANUAL AUTO-LOGIN - Trigger Auto-Login Manually
REM ============================================================================
REM Use this to test auto-login without starting the full bot
REM ============================================================================

title Manual Auto-Login Test
color 0E

echo.
echo ============================================================================
echo           MANUAL AUTO-LOGIN TRIGGER
echo ============================================================================
echo.
echo This will attempt to login to Zerodha automatically...
echo.

REM Change to project root directory
cd /d "%~dp0"

REM Activate virtual environment if exists
if exist "backend\.venv\Scripts\activate.bat" (
    call backend\.venv\Scripts\activate.bat
) else if exist "backend\venv\Scripts\activate.bat" (
    call backend\venv\Scripts\activate.bat
)

echo [INFO] Attempting auto-login...
echo [INFO] Browser will open in headless mode (invisible)
echo [INFO] This may take 10-15 seconds...
echo.

REM Run manual auto-login
python manual_auto_login.py

echo.
if errorlevel 1 (
    echo [ERROR] Auto-login failed!
    echo Check the error messages above for details.
) else (
    echo [SUCCESS] Auto-login completed!
    echo Session has been saved.
)

echo.
pause
