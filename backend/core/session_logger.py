from datetime import datetime
from sqlalchemy import text
from core.database import today_engine
from core.email_notifier import EmailNotifier
from core.sync_service import ClientBotSync

class SessionLogger:
    @staticmethod
    def _get_active_user_info():
        """Return (signup_client_id, login_username) from users.json (single shared login account)."""
        import json, os
        try:
            users_file = os.path.join(os.path.dirname(__file__), '..', '..', 'login', 'server', 'users.json')
            with open(users_file, 'r') as f:
                users = json.load(f)
            if users:
                u = users[0]
                return u.get('client_id') or u.get('clientId'), u.get('username')
        except Exception as e:
            print(f"[SessionLogger] Error reading users.json: {e}")
        return None, None

    @staticmethod
    def create_table():
        """Create sessions table if not exists"""
        with today_engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS bot_sessions (
                    id SERIAL PRIMARY KEY,
                    client_id VARCHAR(100),
                    name VARCHAR(200),
                    login_time TIMESTAMP,
                    logout_time TIMESTAMP,
                    mode VARCHAR(50),
                    date DATE,
                    pnl DECIMAL(10,2),
                    total_trades INTEGER,
                    wins INTEGER,
                    losses INTEGER,
                    gross_pnl DECIMAL(10,2),
                    charges DECIMAL(10,2),
                    signup_client_id VARCHAR(100)
                )
            """))
            conn.commit()
    
    @staticmethod
    def log_login(client_id: str, name: str, mode: str):
        """Log bot start/login"""
        signup_client_id, login_username = SessionLogger._get_active_user_info()
        
        login_time = datetime.now()
        date = login_time.date()
        
        try:
            with today_engine.connect() as conn:
                conn.execute(text("""
                    INSERT INTO bot_sessions (client_id, name, login_time, mode, date, signup_client_id)
                    VALUES (:client_id, :name, :login_time, :mode, :date, :signup_client_id)
                """), {
                    "client_id": client_id,
                    "name": name,
                    "login_time": login_time,
                    "mode": mode,
                    "date": date,
                    "signup_client_id": signup_client_id
                })
                conn.commit()
            print(f"[SessionLogger] Login logged for {client_id} ({mode})")
        except Exception as e:
            print(f"[SessionLogger] ERROR logging login for {client_id}: {e}")
        
        # Send email notification
        EmailNotifier.send_login_notification(
            client_id=signup_client_id or client_id,
            name=name,
            kite_id=client_id,
            mode=mode,
            login_time=login_time,
            date=date.strftime('%Y-%m-%d')
        )
        
        # Sync to Central (PC2)
        sync_manager = ClientBotSync()
        session_payload = {
            "client_id": signup_client_id or client_id,
            "kite_id": client_id,
            "username": login_username,    # Login Username
            "kite_username": name,         # Zerodha Name
            "session_date": date.isoformat(),
            "login_time": login_time.isoformat(),
            "mode": mode,
            # --- NEW: Sending Initial Stats on Login (Initialized to 0) ---
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "net_pnl": 0.0,
            "gross_pnl": 0.0,
            "charges": 0.0,
            "logout_time": None  # Explicitly None for Active session
        }
        sync_manager.sync_session_to_central(session_payload)
    
    @staticmethod
    def log_logout(client_id: str, pnl: float = 0, total_trades: int = 0, wins: int = 0, losses: int = 0, gross_pnl: float = 0, charges: float = 0):
        """Log bot stop/logout. Stats are recalculated from DB for this session's time window only."""
        logout_time = datetime.now()
        
        import json
        import os
        import pandas as pd
        _, login_username = SessionLogger._get_active_user_info()

        with today_engine.connect() as conn:
            # Get session details (login_time is critical for session-scoped trade calculation)
            session_result = conn.execute(text("""
                SELECT id, client_id, name, mode, login_time, signup_client_id
                FROM bot_sessions
                WHERE client_id = :client_id AND logout_time IS NULL
                ORDER BY login_time DESC
                LIMIT 1
            """), {"client_id": client_id})
            session = session_result.fetchone()

            # --- FIX: Calculate stats from DB scoped to THIS session's time window ---
            # This prevents using the daily total (all sessions) as this session's stats.
            if session and session.login_time:
                login_dt = session.login_time
                session_mode = session.mode  # e.g. 'LIVE' or 'PAPER'
                signup_id = session.signup_client_id or client_id

                try:
                    all_trades_df = pd.read_sql_query("SELECT * FROM trades", conn)
                    if not all_trades_df.empty and 'timestamp' in all_trades_df.columns:
                        all_trades_df['timestamp'] = pd.to_datetime(all_trades_df['timestamp'], errors='coerce')

                        # Filter by time window: [login_time, logout_time]
                        time_mask = (all_trades_df['timestamp'] >= login_dt) & \
                                    (all_trades_df['timestamp'] <= logout_time)

                        # Filter by client ID using ucc column
                        ucc_col = 'ucc' if 'ucc' in all_trades_df.columns else 'client_id'
                        client_mask = (all_trades_df[ucc_col] == client_id) | (all_trades_df[ucc_col].isnull())

                        # Filter by trading mode
                        target_mode = 'Live Trading' if session_mode == 'LIVE' else 'Paper Trading'
                        if 'trading_mode' in all_trades_df.columns:
                            if target_mode == 'Paper Trading':
                                mode_mask = (all_trades_df['trading_mode'] == target_mode) | \
                                            (all_trades_df['trading_mode'].isnull())
                            else:
                                mode_mask = (all_trades_df['trading_mode'] == target_mode)
                        else:
                            mode_mask = pd.Series([True] * len(all_trades_df))

                        session_trades = all_trades_df[time_mask & client_mask & mode_mask]
                        total_trades = len(session_trades)

                        if total_trades > 0:
                            gross_pnl = float(session_trades['pnl'].sum()) if 'pnl' in session_trades.columns else 0.0
                            pnl = float(session_trades['net_pnl'].sum()) if 'net_pnl' in session_trades.columns else gross_pnl
                            charges = float(session_trades['charges'].sum()) if 'charges' in session_trades.columns else (gross_pnl - pnl)
                            check_col = 'net_pnl' if 'net_pnl' in session_trades.columns else 'pnl'
                            wins = int(len(session_trades[session_trades[check_col] > 0]))
                            losses = int(len(session_trades[session_trades[check_col] <= 0]))
                        else:
                            # Truly 0 trades this session — reset all to 0
                            gross_pnl = 0.0
                            pnl = 0.0
                            charges = 0.0
                            wins = 0
                            losses = 0

                        print(f"[SessionLogger] Session stats recalculated: {total_trades} trades, "
                              f"net_pnl={pnl:.2f}, wins={wins}, losses={losses}")
                    else:
                        total_trades = 0; gross_pnl = 0.0; pnl = 0.0; charges = 0.0; wins = 0; losses = 0
                except Exception as e:
                    print(f"[SessionLogger] Error recalculating session stats: {e}")
                    # Fall back to passed-in values (less accurate but avoids crash)

            # Update logout in DB
            result = conn.execute(text("""
                UPDATE bot_sessions 
                SET logout_time = :logout_time, pnl = :pnl, total_trades = :total_trades,
                    wins = :wins, losses = :losses, gross_pnl = :gross_pnl, charges = :charges
                WHERE id = (
                    SELECT id FROM bot_sessions
                    WHERE client_id = :client_id AND logout_time IS NULL
                    ORDER BY login_time DESC
                    LIMIT 1
                )
            """), {
                "client_id": client_id,
                "logout_time": logout_time,
                "pnl": pnl,
                "total_trades": total_trades,
                "wins": wins,
                "losses": losses,
                "gross_pnl": gross_pnl,
                "charges": charges
            })
            conn.commit()
            
            if session:
                # Build trades list for email
                trades_list = []
                try:
                    if not all_trades_df.empty:
                        check_col = 'net_pnl' if 'net_pnl' in all_trades_df.columns else 'pnl'
                        s_trades = all_trades_df[time_mask & client_mask & mode_mask] if total_trades > 0 else pd.DataFrame()
                        trades_list = s_trades.to_dict('records') if not s_trades.empty else []
                except Exception:
                    trades_list = []

                # Send email notification
                EmailNotifier.send_logout_notification(
                    client_id=session.signup_client_id or session.client_id,
                    name=session.name,
                    kite_id=session.client_id,
                    mode=session.mode,
                    login_time=session.login_time,
                    logout_time=logout_time,
                    total_trades=total_trades,
                    net_pnl=pnl,
                    wins=wins,
                    losses=losses,
                    trades=trades_list
                )
                
                # Sync to Central (PC2)
                sync_manager = ClientBotSync()
                session_payload = {
                    "client_id": session.signup_client_id or session.client_id,
                    "kite_id": session.client_id,
                    "username": login_username,    # Login Username
                    "kite_username": session.name, # Zerodha Name
                    "session_date": session.login_time.date().isoformat(),
                    "login_time": session.login_time.isoformat(),
                    "logout_time": logout_time.isoformat(),
                    "mode": session.mode,
                    "total_trades": total_trades,
                    "wins": wins,
                    "losses": losses,
                    "net_pnl": float(pnl),
                    "gross_pnl": float(gross_pnl),
                    "charges": float(charges)
                }
                sync_manager.sync_session_to_central(session_payload)

    @staticmethod
    def update_active_session(client_id: str):
        """Recalculate and update trades/pnl for the currently active (running) session."""
        import pandas as pd
        try:
            with today_engine.connect() as conn:
                session_result = conn.execute(text("""
                    SELECT id, login_time, mode FROM bot_sessions
                    WHERE client_id = :client_id AND logout_time IS NULL
                    ORDER BY login_time DESC LIMIT 1
                """), {"client_id": client_id})
                session = session_result.fetchone()
                if not session:
                    return

                login_dt = session.login_time
                session_mode = session.mode
                now = datetime.now()

                all_trades_df = pd.read_sql_query("SELECT * FROM trades", conn)
                total_trades = 0; gross_pnl = 0.0; pnl = 0.0; charges = 0.0; wins = 0; losses = 0

                if not all_trades_df.empty and 'timestamp' in all_trades_df.columns:
                    all_trades_df['timestamp'] = pd.to_datetime(all_trades_df['timestamp'], errors='coerce')
                    time_mask = (all_trades_df['timestamp'] >= login_dt) & (all_trades_df['timestamp'] <= now)
                    ucc_col = 'ucc' if 'ucc' in all_trades_df.columns else 'client_id'
                    client_mask = (all_trades_df[ucc_col] == client_id) | (all_trades_df[ucc_col].isnull())
                    target_mode = 'Live Trading' if session_mode == 'LIVE' else 'Paper Trading'
                    if 'trading_mode' in all_trades_df.columns:
                        mode_mask = (all_trades_df['trading_mode'] == target_mode) | \
                                    (all_trades_df['trading_mode'].isnull() if target_mode == 'Paper Trading' else pd.Series([False]*len(all_trades_df)))
                    else:
                        mode_mask = pd.Series([True] * len(all_trades_df), index=all_trades_df.index)

                    session_trades = all_trades_df[time_mask & client_mask & mode_mask]
                    total_trades = len(session_trades)
                    if total_trades > 0:
                        gross_pnl = float(session_trades['pnl'].sum()) if 'pnl' in session_trades.columns else 0.0
                        pnl = float(session_trades['net_pnl'].sum()) if 'net_pnl' in session_trades.columns else gross_pnl
                        charges = float(session_trades['charges'].sum()) if 'charges' in session_trades.columns else (gross_pnl - pnl)
                        check_col = 'net_pnl' if 'net_pnl' in session_trades.columns else 'pnl'
                        wins = int(len(session_trades[session_trades[check_col] > 0]))
                        losses = int(len(session_trades[session_trades[check_col] <= 0]))

                conn.execute(text("""
                    UPDATE bot_sessions SET total_trades=:t, pnl=:p, gross_pnl=:g, charges=:c, wins=:w, losses=:l
                    WHERE id=:id
                """), {"t": total_trades, "p": pnl, "g": gross_pnl, "c": charges, "w": wins, "l": losses, "id": session.id})
                conn.commit()
        except Exception as e:
            print(f"[SessionLogger] update_active_session error: {e}")

    @staticmethod
    def cleanup_orphaned_sessions():
        """
        Cleanup any sessions that were left open (logout_time is NULL) 
        due to server crash or restart.
        Recalculates stats from trades table to ensure accuracy.
        """
        cleanup_time = datetime.now()
        import os
        import json
        import pandas as pd

        _, login_username = SessionLogger._get_active_user_info()

        with today_engine.connect() as conn:
            # 1. Find orphaned sessions
            result = conn.execute(text("""
                SELECT id, client_id, name, mode, login_time, signup_client_id
                FROM bot_sessions 
                WHERE logout_time IS NULL
                ORDER BY login_time
            """))
            orphaned_sessions = result.fetchall()

            if not orphaned_sessions:
                return

            print(f"Found {len(orphaned_sessions)} orphaned sessions. Recalculating stats and cleaning up...")

            # 2. Load trades for calculation
            try:
                all_trades_df = pd.read_sql_query("SELECT * FROM trades", conn)
                if not all_trades_df.empty and 'timestamp' in all_trades_df.columns:
                    all_trades_df['timestamp'] = pd.to_datetime(all_trades_df['timestamp'], errors='coerce')
            except Exception:
                all_trades_df = pd.DataFrame()

            # 3. Process each orphaned session
            for session in orphaned_sessions:
                # Find start time of the NEXT session to set as logout time for this session (prevent overlap)
                next_session_q = conn.execute(text("""
                    SELECT min(login_time) FROM bot_sessions 
                    WHERE login_time > :this_login_time
                """), {"this_login_time": session.login_time})
                next_start = next_session_q.scalar()
                
                # If there's a next session, end this one there. Otherwise, end at now.
                logout_time = next_start if next_start else cleanup_time
                
                # Calculate Stats
                pnl = 0.0
                total_trades = 0
                wins = 0
                losses = 0
                gross_pnl = 0.0
                charges = 0.0
                
                if not all_trades_df.empty:
                    # Filter by time window [login, logout)
                    time_mask = (all_trades_df['timestamp'] >= session.login_time) & (all_trades_df['timestamp'] < logout_time)
                    
                    # Filter by mode
                    target_mode = 'Live Trading' if session.mode == 'LIVE' else 'Paper Trading'
                    if 'trading_mode' in all_trades_df.columns:
                        if target_mode == 'Paper Trading':
                            # Include if matches Paper OR if NULL (legacy data assumed Paper)
                            mode_mask = (all_trades_df['trading_mode'] == target_mode) | (all_trades_df['trading_mode'].isnull())
                        else:
                            mode_mask = (all_trades_df['trading_mode'] == target_mode)
                        
                        final_mask = time_mask & mode_mask
                    else:
                        final_mask = time_mask
                    
                    session_trades = all_trades_df[final_mask]
                    total_trades = len(session_trades)
                    
                    if total_trades > 0:
                        # P&L
                        if 'net_pnl' in session_trades.columns:
                            pnl = session_trades['net_pnl'].sum()
                        elif 'pnl' in session_trades.columns:
                            pnl = session_trades['pnl'].sum() # Fallback to gross if net missing
                        
                        # Gross P&L
                        if 'pnl' in session_trades.columns:
                            gross_pnl = session_trades['pnl'].sum()
                            
                        # Charges
                        if 'charges' in session_trades.columns:
                            charges = session_trades['charges'].sum()
                        else:
                            charges = gross_pnl - pnl
                            
                        # Wins/Losses
                        # If pnl > 0 it's a win? or check 'pnl' column per trade? 
                        # Usually net_pnl is used for overall, but individual trade 'pnl' (gross) dictates green/red?
                        # Let's use net_pnl per trade if available
                        check_col = 'net_pnl' if 'net_pnl' in session_trades.columns else 'pnl'
                        wins = len(session_trades[session_trades[check_col] > 0])
                        losses = len(session_trades[session_trades[check_col] <= 0])

                # Update DB with calculated values
                conn.execute(text("""
                    UPDATE bot_sessions 
                    SET logout_time = :logout_time,
                        pnl = :pnl,
                        total_trades = :total_trades,
                        wins = :wins,
                        losses = :losses,
                        gross_pnl = :gross_pnl,
                        charges = :charges
                    WHERE id = :id
                """), {
                    "logout_time": logout_time,
                    "pnl": float(pnl),
                    "total_trades": int(total_trades),
                    "wins": int(wins),
                    "losses": int(losses),
                    "gross_pnl": float(gross_pnl),
                    "charges": float(charges),
                    "id": session.id
                })
                conn.commit()

                # Notify and Sync
                sync_manager = ClientBotSync()
                
                # Send email
                try:
                    EmailNotifier.send_logout_notification(
                        client_id=session.signup_client_id or session.client_id,
                        name=f"{session.name} (Auto-Closed)",
                        kite_id=session.client_id,
                        mode=session.mode,
                        login_time=session.login_time,
                        logout_time=logout_time,
                        total_trades=total_trades,
                        net_pnl=pnl
                    )
                except Exception as e:
                    print(f"Failed to send cleanup email for {session.client_id}: {e}")

                # Sync to Central
                try:
                    session_payload = {
                        "client_id": session.signup_client_id or session.client_id,
                        "kite_id": session.client_id,
                        "username": login_username,
                        "kite_username": session.name,
                        "session_date": session.login_time.date().isoformat(),
                        "login_time": session.login_time.isoformat(),
                        "logout_time": logout_time.isoformat(),
                        "mode": session.mode,
                        "total_trades": int(total_trades),
                        "net_pnl": float(pnl),
                        "gross_pnl": float(gross_pnl),
                        "charges": float(charges)
                    }
                    sync_manager.sync_session_to_central(session_payload)
                except Exception as e:
                    print(f"Failed to sync cleanup session for {session.client_id}: {e}")
