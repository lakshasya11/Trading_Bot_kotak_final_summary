Trading Bot Kotak — Breakout Logic Trading System
A fully automated algorithmic trading bot for Kotak Neo featuring a Supertrend breakout strategy, real-time monitoring, and multi-user support.

🎯 Features
✅ Automated Trading – Breakout strategy with Supertrend indicator.

✅ Multi-User Support – Manage multiple trading accounts simultaneously.

✅ Real-Time Dashboard – React-based Web UI with live trade monitoring.

✅ Risk Management – Hardcoded and dynamic stop-loss/take-profit.

✅ WebSocket Updates – High-frequency live price and trade status updates.

✅ Auto-Login – Seamless session management with 2FA/TOTP support.

✅ Trade History – SQLite logging and performance analytics.

✅ REST API – FastAPI backend for easy integration.

📋 Requirements
Python 3.9+

Node.js 16+ (for frontend)

Kotak Neo API Access (Consumer Key & Secret)

Operating System: Windows, macOS, or Linux

⚠️ **IMPORTANT**: Because this project contains sensitive trading credentials, several configuration files are hidden from GitHub using `.gitignore`. 

👉 **[READ THIS FIRST: Local Setup & Sensitive Files Guide](./LOCAL_SETUP_GUIDE.md)** for instructions on how to create the missing files needed to run the bot.

🚀 Quick Start
1. Clone Repository
Bash
git clone https://github.com/lakshasya11/Trading-Bot-Kotak.git
cd Trading-Bot-Kotak
2. Set up Backend
Bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
3. Configure Credentials
Create a broker_config.json file in the backend folder:

⚠️ IMPORTANT: Never commit this file to GitHub. Add it to your .gitignore.

JSON
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
4. Setup Frontend
Bash
cd ../frontend
npm install
npm run dev
5. Start the Bot
Bash
cd ../backend
python -m uvicorn main:app --port 8000 --reload
⚙️ Configuration
Strategy Parameters (strategy_params.json)
JSON
{
  "supertrend_period": 10,
  "supertrend_multiplier": 3.0,
  "atr_period": 14,
  "sl_percent": 2.0,
  "tp_percent": 5.0,
  "max_trades_per_hour": 5
}
📊 Trading Strategy
Breakout Logic
Entry Signal: Supertrend breakout confirmed by volume surge and candle body strength.

Position Sizing: Calculated automatically based on available margin.

Stop Loss: Fixed at 2% or dynamic based on the "No-Wick" candle low.

Exit: Automatic triggers at TP/SL or real-time reversal detection.

Market Hours
Trading Window: 09:15 – 15:30 IST (Monday–Friday).

Instruments: Index Options (BANKNIFTY, NIFTY, FINNIFTY, SENSEX).
