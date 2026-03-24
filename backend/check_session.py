from core.database import today_engine
from sqlalchemy import text
from datetime import datetime

with today_engine.connect() as conn:
    # Get bot_sessions columns
    cols = conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'bot_sessions' ORDER BY ordinal_position"
    )).fetchall()
    print("bot_sessions columns:", [c[0] for c in cols])
    print()

    # Reconstruct today's session from trades
    result = conn.execute(text("""
        SELECT
            ucc,
            MIN(timestamp) as first_trade,
            MAX(timestamp) as last_trade,
            COUNT(*) as total_trades,
            SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) as losses,
            COALESCE(SUM(pnl), 0) as gross_pnl,
            COALESCE(SUM(charges), 0) as charges,
            COALESCE(SUM(net_pnl), 0) as net_pnl
        FROM trades
        GROUP BY ucc
    """)).fetchall()

    if result:
        for r in result:
            print(f"UCC/Client : {r[0]}")
            print(f"First Trade: {r[1]}")
            print(f"Last Trade : {r[2]}")
            print(f"Trades     : {r[3]}  (Wins: {r[4]}, Losses: {r[5]})")
            print(f"Gross PnL  : Rs.{float(r[6]):.2f}")
            print(f"Charges    : Rs.{float(r[7]):.2f}")
            print(f"Net PnL    : Rs.{float(r[8]):.2f}")
    else:
        print("No trades found in today's database")
