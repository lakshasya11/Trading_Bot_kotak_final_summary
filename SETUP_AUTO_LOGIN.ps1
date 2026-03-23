# ============================================================================
# Zerodha Auto-Login Setup Script
# ============================================================================
# This script will guide you through setting up automatic login for your bot
# No more manual TOTP entry required!
# ============================================================================

Write-Host "`n============================================================================" -ForegroundColor Cyan
Write-Host "          ZERODHA AUTO-LOGIN SETUP WIZARD" -ForegroundColor Yellow
Write-Host "============================================================================`n" -ForegroundColor Cyan

# Check if running from correct directory
$expectedPath = "Live working 24 1 26-4"
$currentPath = Get-Location
if ($currentPath.Path -notlike "*$expectedPath*") {
    Write-Host "⚠️  Please run this script from the project directory" -ForegroundColor Red
    Write-Host "Expected: *\$expectedPath" -ForegroundColor Yellow
    Write-Host "Current:  $currentPath`n" -ForegroundColor Yellow
    exit 1
}

# Step 1: Install Dependencies
Write-Host "Step 1: Installing Required Packages..." -ForegroundColor Green
Write-Host "─────────────────────────────────────────────────────────────────────────`n" -ForegroundColor Gray

cd backend

# Check if venv exists
if (Test-Path ".venv") {
    Write-Host "✅ Virtual environment found" -ForegroundColor Green
    .\.venv\Scripts\Activate.ps1
} elseif (Test-Path "venv") {
    Write-Host "✅ Virtual environment found" -ForegroundColor Green
    .\venv\Scripts\Activate.ps1
} else {
    Write-Host "⚠️  No virtual environment found" -ForegroundColor Yellow
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv venv
    .\venv\Scripts\Activate.ps1
}

Write-Host "`nInstalling packages (this may take a minute)..." -ForegroundColor Cyan
pip install --upgrade pip -q
pip install -r requirements.txt -q

Write-Host "✅ Dependencies installed`n" -ForegroundColor Green

# Step 2: Check Chrome Installation
Write-Host "`nStep 2: Checking Chrome Browser..." -ForegroundColor Green
Write-Host "─────────────────────────────────────────────────────────────────────────`n" -ForegroundColor Gray

$chromePaths = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)

$chromeFound = $false
foreach ($path in $chromePaths) {
    if (Test-Path $path) {
        Write-Host "✅ Chrome found at: $path" -ForegroundColor Green
        $chromeFound = $true
        break
    }
}

if (-not $chromeFound) {
    Write-Host "❌ Chrome not found!" -ForegroundColor Red
    Write-Host "Please install Google Chrome from: https://www.google.com/chrome/`n" -ForegroundColor Yellow
    $continue = Read-Host "Continue anyway? (y/n)"
    if ($continue -ne 'y') {
        exit 1
    }
} else {
    Write-Host ""
}

# Step 3: Guide User Through Credential Setup
Write-Host "`nStep 3: Configure User Credentials..." -ForegroundColor Green
Write-Host "─────────────────────────────────────────────────────────────────────────`n" -ForegroundColor Gray

Write-Host "You need to add these credentials to user_profiles.json:" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. user_id      - Your Zerodha User ID (e.g., AB1234)" -ForegroundColor White
Write-Host "2. password     - Your Zerodha login password" -ForegroundColor White
Write-Host "3. totp_secret  - Your 2FA secret key (see guide below)" -ForegroundColor White
Write-Host ""

# Show TOTP setup instructions
Write-Host "╔═══════════════════════════════════════════════════════════════════════╗" -ForegroundColor Yellow
Write-Host "║           HOW TO GET YOUR TOTP SECRET KEY                             ║" -ForegroundColor Yellow
Write-Host "╚═══════════════════════════════════════════════════════════════════════╝" -ForegroundColor Yellow
Write-Host ""
Write-Host "1. Login to Zerodha Kite → Settings → Security" -ForegroundColor White
Write-Host "2. Click 'Two-factor authentication' → 'Re-generate'" -ForegroundColor White
Write-Host "3. When QR code appears, click 'Can't scan the QR code?'" -ForegroundColor White
Write-Host "4. Copy the SECRET KEY (looks like: JBSWY3DPEHPK3PXP)" -ForegroundColor White
Write-Host "5. Paste it into user_profiles.json → totp_secret field" -ForegroundColor White
Write-Host "6. Complete setup with your authenticator app as normal" -ForegroundColor White
Write-Host ""
Write-Host "⚠️  IMPORTANT: This secret is like a password - keep it secure!" -ForegroundColor Red
Write-Host ""

$openFile = Read-Host "Open user_profiles.json now to edit? (y/n)"
if ($openFile -eq 'y') {
    notepad user_profiles.json
    Write-Host "`n✅ Edit the file and save when done" -ForegroundColor Green
    Read-Host "Press Enter when you've saved your changes"
}

# Step 4: Verify Setup
Write-Host "`nStep 4: Verifying Setup..." -ForegroundColor Green
Write-Host "─────────────────────────────────────────────────────────────────────────`n" -ForegroundColor Gray

$verify = Read-Host "Ready to test auto-login? (y/n)"
if ($verify -eq 'y') {
    Write-Host "`nRunning verification script...`n" -ForegroundColor Cyan
    cd ..
    python test_auto_login.py
} else {
    Write-Host "`n⚠️  Skipping verification" -ForegroundColor Yellow
    Write-Host "Run this command later to test:" -ForegroundColor Cyan
    Write-Host "  python test_auto_login.py`n" -ForegroundColor White
}

# Final Instructions
Write-Host "`n============================================================================" -ForegroundColor Cyan
Write-Host "                    SETUP COMPLETE!" -ForegroundColor Green
Write-Host "============================================================================`n" -ForegroundColor Cyan

Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host ""
Write-Host "1. ✅ Make sure you filled all credentials in user_profiles.json" -ForegroundColor White
Write-Host "   - user_id" -ForegroundColor Gray
Write-Host "   - password" -ForegroundColor Gray
Write-Host "   - totp_secret" -ForegroundColor Gray
Write-Host ""
Write-Host "2. ✅ Test the setup:" -ForegroundColor White
Write-Host "   python test_auto_login.py" -ForegroundColor Cyan
Write-Host ""
Write-Host "3. ✅ Start your bot normally:" -ForegroundColor White
Write-Host "   It will automatically login when needed!" -ForegroundColor Green
Write-Host ""
Write-Host "For detailed instructions, see:" -ForegroundColor Yellow
Write-Host "  AUTO_LOGIN_SETUP_GUIDE.md`n" -ForegroundColor Cyan

Write-Host "🎉 No more manual TOTP entry required!`n" -ForegroundColor Green
