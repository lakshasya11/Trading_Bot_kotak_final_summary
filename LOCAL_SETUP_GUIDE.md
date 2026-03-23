# 🔐 Local Setup & Sensitive Files Guide

Since this repository uses a `.gitignore` to protect your private data, several critical files are **not included** in the GitHub repository. To run this bot locally, you must create these files manually.

---

### 1. Backend Configuration (`/backend/`)

#### 📄 `.env`
Create this file in the `backend` folder to store your database credentials.
```env
DB_HOST=localhost
DB_NAME=trading_bot_db
DB_USER=postgres
DB_PASSWORD=your_password_here
DB_PORT=5432
```

#### 📄 `broker_config.json`
Create this file in the `backend` folder to store your Kotak Neo API credentials.
```json
{
  "users": {
    "V1X8N": {
      "kotak_ucc": "V1X8N",
      "kotak_mobile": "+91XXXXXXXXXX",
      "kotak_mpin": "XXXX",
      "kotak_totp_secret": "YOUR_SECRET_KEY_HERE"
    }
  }
}
```

#### 📄 `strategy_params.json`
Your trading rules and multiplier settings.
```json
{
  "supertrend_period": 10,
  "supertrend_multiplier": 3.0,
  "sl_percent": 1.0,
  "tp_percent": 2.0
}
```

---

### 2. Login Server Configuration (`/login/server/`)

#### 📄 `users.json`
This file stores registered user accounts and their 2FA secrets. **Never share this file.**
```json
[
  {
    "client_id": "AG0001",
    "firstName": "Admin",
    "lastName": "User",
    "email": "admin@example.com",
    "password": "SecurePassword123!",
    "totp_secret": "B2PYONVOKLDDZ7HP3XRKBMD5KY2AMZDQ",
    "created_at": "2024-01-09 12:10:00"
  }
]
```

#### 📄 `.env` (Login Server)
Create matches for the master registration database.
```env
DB_HOST=localhost
DB_NAME=trading_master_db
DB_USER=postgres
DB_PASSWORD=your_password_here
DB_PORT=5432
SMTP_EMAIL=your_email@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
```

---

### 3. Automatically Created Files (Ignore These)
The bot will create these files automatically when it runs. You do not need to create them manually:
- `*.db` (SQLite databases)
- `last_run_date.txt`
- `*.log` (Debug logs)
- `pending_sync.json`

---

### ⚠️ Security Reminder
**Never** remove these files from the `.gitignore`. If you accidentally push them to GitHub, your trading account and personal data could be compromised. Change your passwords and API keys immediately if any of these files are exposed.
