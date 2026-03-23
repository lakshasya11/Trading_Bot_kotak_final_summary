@echo off
REM ============================================================================
REM INSTALL AUTO-LOGIN DEPENDENCIES
REM ============================================================================
REM This batch file installs the required packages for auto-login:
REM - selenium (browser automation)
REM - pyotp (TOTP generation)
REM - webdriver-manager (ChromeDriver management)
REM ============================================================================

title Install Auto-Login Dependencies
color 0C

echo.
echo ============================================================================
echo           INSTALLING AUTO-LOGIN DEPENDENCIES
echo ============================================================================
echo.
echo This will install:
echo   - selenium (browser automation)
echo   - pyotp (TOTP generation)
echo   - webdriver-manager (ChromeDriver management)
echo.

REM Change to backend directory
cd /d "%~dp0backend"

REM Activate virtual environment if exists
if exist ".venv\Scripts\activate.bat" (
    echo [INFO] Activating virtual environment (.venv)...
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    echo [INFO] Activating virtual environment (venv)...
    call venv\Scripts\activate.bat
) else (
    echo [WARNING] No virtual environment found.
    echo [WARNING] Packages will be installed to system Python.
    echo.
    set /p continue="Continue anyway? (Y/N): "
    if /i not "%continue%"=="Y" exit /b
)

echo.
echo [INFO] Upgrading pip...
python -m pip install --upgrade pip

echo.
echo [INFO] Installing dependencies from requirements.txt...
pip install -r requirements.txt

echo.
echo ============================================================================
echo.

REM Verify installations
echo [INFO] Verifying installations...
echo.

python -c "import selenium; print('[OK] selenium installed:', selenium.__version__)" 2>nul
if errorlevel 1 echo [ERROR] selenium not found!

python -c "import pyotp; print('[OK] pyotp installed:', pyotp.__version__)" 2>nul
if errorlevel 1 echo [ERROR] pyotp not found!

python -c "from webdriver_manager.chrome import ChromeDriverManager; print('[OK] webdriver-manager installed')" 2>nul
if errorlevel 1 echo [ERROR] webdriver-manager not found!

echo.
echo ============================================================================
echo           INSTALLATION COMPLETE
echo ============================================================================
echo.
echo Next steps:
echo 1. Fill credentials in backend\user_profiles.json
echo 2. Run: TEST_AUTO_LOGIN.bat
echo.

pause
