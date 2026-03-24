from core.database import today_engine
from sqlalchemy import text

with today_engine.connect() as conn:
    print("--- BOT SESSIONS (TODAY) ---")
    rows = conn.execute(text("SELECT id, login_time, logout_time, total_trades, pnl FROM bot_sessions ORDER BY id")).fetchall()
    for r in rows:
        print(f"ID: {r[0]}, Login: {r[1]}, Logout: {r[2]}, Trades: {r[3]}, PnL: {r[4]}")
