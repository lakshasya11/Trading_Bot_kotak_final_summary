# ============================================================================
#          TRADING BOT STARTUP - AUTO-AUTHENTICATION FIX
# ============================================================================

Write-Host "`n" -ForegroundColor Green
Write-Host "============================================================================" -ForegroundColor Green
Write-Host "          TRADING BOT STARTUP - V47.14" -ForegroundColor Cyan
Write-Host "============================================================================`n" -ForegroundColor Green

# Step 1: Kill existing processes on port 8000
Write-Host "[1/4] Freeing port 8000..." -ForegroundColor Cyan
$portProcesses = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
if ($portProcesses) {
    foreach ($pid in $portProcesses) {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue | Out-Null
    }
    Write-Host "      [✓] Port freed" -ForegroundColor Green
    Start-Sleep -Seconds 1
} else {
    Write-Host "      [✓] Port already free" -ForegroundColor Green
}

# Step 2: Check and setup authentication
Write-Host "`n[2/4] Checking authentication..." -ForegroundColor Cyan
$botDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $botDir "backend"
$user1Token = Join-Path $backendDir "access_token_user1.json"
$user2Token = Join-Path $botDir "access_token_user2.json"

if (Test-Path $user1Token) {
    Write-Host "      [✓] User1 token found (Ratan)" -ForegroundColor Green
} elseif (Test-Path $user2Token) {
    Write-Host "      [!] User1 token missing, but User2 token found (Vinay)" -ForegroundColor Yellow
    Write-Host "      → Copy User2 token to backend folder" -ForegroundColor Yellow
    Copy-Item $user2Token $user1Token -Force -ErrorAction SilentlyContinue
    Write-Host "      [✓] User2 token copied" -ForegroundColor Green
} else {
    Write-Host "      [!] No access token found" -ForegroundColor Yellow
    Write-Host "      → Running auto-login..." -ForegroundColor Yellow
    
    Set-Location $botDir
    & python manual_auto_login.py
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "      [✓] Auto-login successful" -ForegroundColor Green
    } else {
        Write-Host "      [!] Auto-login failed - you'll need to authenticate via web UI" -ForegroundColor Yellow
    }
}

# Step 3: Start the bot
Write-Host "`n[3/4] Starting bot backend..." -ForegroundColor Cyan
Set-Location $backendDir

Write-Host "      [✓] Backend starting..." -ForegroundColor Green
Write-Host "`n============================================================================" -ForegroundColor Green
Write-Host "Bot is starting. You can access it at: http://localhost:8000" -ForegroundColor Green
Write-Host "============================================================================`n" -ForegroundColor Green

# Start the backend
& python main.py

Write-Host "`n============================================================================" -ForegroundColor Red
Write-Host "Bot has stopped" -ForegroundColor Red
Write-Host "============================================================================`n" -ForegroundColor Red
