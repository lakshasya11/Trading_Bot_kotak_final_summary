from core.database import today_engine
from sqlalchemy import text
from datetime import datetime

with today_engine.connect() as conn:
    # Read today's summary from trades
    r = conn.execute(text("""
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
    """)).fetchone()

    if not r:
        print("No trades found. Nothing to restore.")
        exit()

    ucc        = r[0]
    login_time = r[1]
    logout_time= r[2]
    trades     = int(r[3])
    wins       = int(r[4])
    losses     = int(r[5])
    gross_pnl  = float(r[6])
    charges    = float(r[7])
    net_pnl    = float(r[8])
    date_str   = datetime.now().strftime("%Y-%m-%d")

    conn.execute(text("""
        INSERT INTO bot_sessions
            (client_id, name, login_time, logout_time, mode, date,
             pnl, total_trades, wins, losses, gross_pnl, charges,
             signup_client_id, user_ucc)
        VALUES
            (:client_id, :name, :login_time, :logout_time, :mode, :date,
             :pnl, :total_trades, :wins, :losses, :gross_pnl, :charges,
             :signup_client_id, :user_ucc)
    """), {
        "client_id"      : ucc,
        "name"           : "Srinidhi Joshi",
        "login_time"     : login_time,
        "logout_time"    : logout_time,
        "mode"           : "PAPER",
        "date"           : date_str,
        "pnl"            : net_pnl,
        "total_trades"   : trades,
        "wins"           : wins,
        "losses"         : losses,
        "gross_pnl"      : gross_pnl,
        "charges"        : charges,
        "signup_client_id": "AG0007",
        "user_ucc"       : ucc,
    })
    conn.commit()

    print(f"Restored today's session for {ucc}:")
    print(f"  Trades : {trades} (Wins: {wins}, Losses: {losses})")
    print(f"  Net PnL: Rs.{net_pnl:.2f}")
    print(f"  Date   : {date_str}")
    print("Session successfully re-inserted into bot_sessions!")
