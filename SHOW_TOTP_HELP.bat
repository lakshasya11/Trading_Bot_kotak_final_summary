@echo off
REM ============================================================================
REM Show TOTP Setup Instructions
REM ============================================================================

setlocal enabledelayedexpansion

REM Set colors
set "GREEN=[92m"
set "YELLOW=[93m"
set "RED=[91m"
set "CYAN=[96m"
set "WHITE=[97m"
set "RESET=[0m"

cls
echo.
echo %CYAN%========================================================================%RESET%
echo %YELLOW%              HOW TO GET YOUR TOTP SECRET KEY%RESET%
echo %CYAN%========================================================================%RESET%
echo.
echo %WHITE%Step-by-Step Instructions:%RESET%
echo.
echo %GREEN%1. LOGIN TO ZERODHA KITE%RESET%
echo    Go to https://kite.zerodha.com
echo    Login with your credentials
echo.
echo %GREEN%2. OPEN SETTINGS%RESET%
echo    Click your name (top-right corner)
echo    Select "Settings" from dropdown
echo.
echo %GREEN%3. GO TO SECURITY%RESET%
echo    Click "Security" in left sidebar
echo    Find "Two-factor authentication" section
echo.
echo %GREEN%4. RE-GENERATE 2FA%RESET%
echo    Click "Re-generate" button
echo    (Don't worry - this won't break your existing authenticator)
echo.
echo %GREEN%5. GET THE SECRET KEY%RESET%
echo    When QR code appears, click "Can't scan the QR code?"
echo    You'll see a text string like: %YELLOW%JBSWY3DPEHPK3PXP%RESET%
echo    This is your TOTP SECRET - copy it!
echo.
echo %GREEN%6. SAVE IN TWO PLACES%RESET%
echo    - Your authenticator app (Google Authenticator, Authy, etc.)
echo    - backend\user_profiles.json → totp_secret field
echo.
echo %GREEN%7. COMPLETE SETUP%RESET%
echo    Finish the 2FA setup in Zerodha as normal
echo    Your authenticator app should now show codes
echo.
echo %CYAN%========================================================================%RESET%
echo %YELLOW%              WHERE TO PUT IT IN CONFIG%RESET%
echo %CYAN%========================================================================%RESET%
echo.
echo Edit: %WHITE%backend\user_profiles.json%RESET%
echo.
echo {
echo   "users": [
echo     {
echo       "id": "user1",
echo       "name": "Ratan",
echo       "user_id": "AB1234",                    ^<- Your Zerodha User ID
echo       "password": "YourPassword",             ^<- Your Zerodha Password
echo       "totp_secret": "JBSWY3DPEHPK3PXP",      ^<- PASTE YOUR SECRET HERE
echo       ...
echo     }
echo   ]
echo }
echo.
echo %CYAN%========================================================================%RESET%
echo %RED%              SECURITY NOTES%RESET%
echo %CYAN%========================================================================%RESET%
echo.
echo %YELLOW%- Keep this secret SECURE - it's like a password%RESET%
echo %YELLOW%- Don't share it with anyone%RESET%
echo %YELLOW%- Don't commit it to GitHub (user_profiles.json is in .gitignore)%RESET%
echo %YELLOW%- Backup safely (password manager recommended)%RESET%
echo.
echo %CYAN%========================================================================%RESET%
echo.

REM Check current configuration
python -c "import json; data=json.load(open('backend/user_profiles.json')); user=next((u for u in data['users'] if u['id']==data['active_user']), None); print('\nCURRENT CONFIGURATION STATUS:'); print('Active User:', user.get('name'), '(ID:', user.get('id'), ')'); print('  User ID:    ', 'Configured' if user.get('user_id','').strip() else 'Missing'); print('  Password:   ', 'Configured' if user.get('password','').strip() else 'Missing'); print('  TOTP Secret:', 'Configured' if user.get('totp_secret','').strip() else 'Missing')" 2>nul

if %errorlevel% neq 0 (
    echo %YELLOW%Could not check configuration status%RESET%
)

echo.
set /p OPEN_FILE="Open user_profiles.json to edit now? (y/n): "
if /i "!OPEN_FILE!" equ "y" (
    notepad backend\user_profiles.json
    echo.
    echo %GREEN%Save the file when done!%RESET%
)

echo.
echo For more details, see: %WHITE%AUTO_LOGIN_SETUP_GUIDE.md%RESET%
echo.
pause
