# Trading Bot Kotak — Breakout Logic Trading System

A fully automated algorithmic trading bot for Kotak Neo featuring a Supertrend breakout strategy, real-time monitoring, multi-user support, and automated email notifications.

---

## 🎯 Features

- ✅ **Automated Trading** – Breakout strategy with Supertrend indicator
- ✅ **Multi-User Support** – Manage multiple trading accounts simultaneously
- ✅ **Real-Time Dashboard** – React-based Web UI with live trade monitoring
- ✅ **Risk Management** – Hardcoded and dynamic stop-loss/take-profit
- ✅ **WebSocket Updates** – High-frequency live price and trade status updates
- ✅ **Auto-Login** – Seamless session management with 2FA/TOTP support
- ✅ **Trade History** – PostgreSQL logging and performance analytics
- ✅ **REST API** – FastAPI backend for easy integration
- ✅ **Email Notifications** – Automated emails on bot start, stop, and daily summary at 3:31 PM

---

## 📧 Email Notifications

The bot automatically sends emails for the following events:

### 🟢 Bot Start
Sent every time the bot is started.
```
Bot Login Notification
Client ID | UCC | User Name | Mode | Login Time | Date
```

### 🔴 Bot Stop
Sent when the bot is stopped (via Stop button, Logout, or server restart).
```
Bot Session Report
Client ID | UCC | User Name | Mode
Login Time | Logout Time | Total Trades | Wins | Losses | Final Net P&L
+ Full trade history table for that session
```

### 📊 Daily Summary — 3:31 PM (Auto)
Sent automatically every day at 3:31 PM regardless of bot state.
```
Daily Summary Report
Client ID | UCC | User Name | Mode | Date
Total Trades | Wins | Losses | Final Net P&L
+ Full trade history table for the entire day
```

> Configure email in `login/server/.env`:
> ```
> SMTP_EMAIL=your@gmail.com
> SMTP_PASSWORD=your_app_password
> NOTIFICATION_EMAIL=recipient@gmail.com
> ```

---

## 📋 Requirements

- Python 3.9+
- Node.js 16+ (for frontend)
- PostgreSQL (for trade & session logging)
- Kotak Neo API Access (Consumer Key & Secret)
- Operating System: Windows, macOS, or Linux

---

> ⚠️ **IMPORTANT**: This project contains sensitive trading credentials. Several configuration files are hidden from GitHub using `.gitignore`.
>
> 👉 **[READ THIS FIRST: Local Setup & Sensitive Files Guide](./LOCAL_SETUP_GUIDE.md)**

---

## 🚀 Quick Start

### 1. Clone Repository
```bash
git clone https://github.com/lakshasya11/Trading_Bot_kotak_final_summary.git
cd Trading_Bot_kotak_final_summary
```

### 2. Set up Backend
```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure Credentials
Create `backend/broker_config.json`:
```json
{
  "users": {
    "user1": {
      "kotak_ucc": "YOUR_UCC",
      "kotak_mobile": "+91XXXXXXXXXX",
      "kotak_mpin": "YOUR_MPIN",
      "kotak_totp_secret": "YOUR_TOTP_SECRET"
    }
  }
}
```

Create `login/server/.env`:
```
SMTP_EMAIL=your@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
NOTIFICATION_EMAIL=recipient@gmail.com
DB_HOST=localhost
DB_NAME=trading_master_db
DB_USER=postgres
DB_PASSWORD=your_db_password
DB_PORT=5432
```

### 4. Setup Frontend
```bash
cd ../frontend
npm install
npm run dev
```

### 5. Start the Bot
```bash
cd ../backend
python -m uvicorn main:app --port 8000 --reload
```

---

## ⚙️ Configuration

### Strategy Parameters (`strategy_params.json`)
```json
{
  "supertrend_period": 10,
  "supertrend_multiplier": 3.0,
  "atr_period": 14,
  "sl_percent": 2.0,
  "tp_percent": 5.0,
  "max_trades_per_hour": 5
}
```

---

## 📊 Trading Strategy

### Breakout Logic
- **Entry Signal** – Supertrend breakout confirmed by volume surge and candle body strength
- **Position Sizing** – Calculated automatically based on available margin
- **Stop Loss** – Fixed at 2% or dynamic based on the "No-Wick" candle low
- **Exit** – Automatic triggers at TP/SL or real-time reversal detection

### Market Hours
- **Trading Window** – 09:15 – 15:30 IST (Monday–Friday)
- **Instruments** – Index Options (BANKNIFTY, NIFTY, FINNIFTY, SENSEX)

---

## 🔒 Files Hidden from GitHub

| File | Reason |
|------|--------|
| `broker_config.json` | Kotak UCC, MPIN, TOTP secret |
| `login/server/users.json` | Signup user credentials |
| `.env` / `login/server/.env` | SMTP, DB passwords, API keys |
| `*.db` | Trade & session databases |
| `*.log` | Debug logs |
| `strategy_params.json` | Runtime strategy config |
