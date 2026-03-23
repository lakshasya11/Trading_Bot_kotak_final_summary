import asyncio
import numpy as np
import pandas as pd
from datetime import datetime
from .database import today_engine, all_engine, sql_text

def sanitize_value(v):
    """Convert numpy/pandas types to native Python types for database compatibility."""
    if isinstance(v, (np.integer, np.floating)):
        return v.item()
    if isinstance(v, np.ndarray):
        return v.tolist()
    if pd.isna(v):
        return None
    return v

class TradeLogger:
    """Handles all database interactions for logging trades using a connection pool."""
    def __init__(self, db_lock):
        self.db_lock = db_lock
        self.engines = [today_engine, all_engine]

    async def log_trade(self, trade_info):
        """Asynchronously logs a completed trade to the databases using the pool."""
        # Sanitize data: convert numpy/pandas types to native Python types
        sanitized_info = {k: sanitize_value(v) for k, v in trade_info.items()}
        
        def db_call():
            columns = ", ".join(sanitized_info.keys())
            placeholders = ", ".join(f":{key}" for key in sanitized_info.keys())
            sql = f"INSERT INTO trades ({columns}) VALUES ({placeholders})"
            
            for engine in self.engines:
                try:
                    with engine.begin() as conn:
                        conn.execute(sql_text(sql), sanitized_info)
                except Exception as e:
                    import logging
                    logger = logging.getLogger("core.trade_logger")
                    db_name = engine.url.database
                    logger.error(f"CRITICAL DB ERROR writing to {db_name}: {e}")
                    # Also print for console visibility
                    print(f"CRITICAL DB ERROR writing to {db_name}: {e}")

        async with self.db_lock:
            await asyncio.to_thread(db_call)

    @staticmethod
    def setup_databases():
        """
        Creates/updates tables if they don't exist and clears the 'today'
        database if it's a new day.
        """
        if today_engine.dialect.name == 'sqlite':
            create_table_sql = sql_text('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    trigger_reason TEXT NOT NULL,
                    symbol TEXT,
                    quantity INTEGER,
                    pnl REAL,
                    entry_price REAL,
                    exit_price REAL,
                    exit_reason TEXT,
                    trend_state TEXT,
                    atr REAL,
                    charges REAL,
                    net_pnl REAL,
                    entry_time TEXT,
                    exit_time TEXT,
                    duration_seconds REAL,
                    max_price REAL,
                    signal_time TEXT,
                    order_time TEXT,
                    expected_entry REAL,
                    expected_exit REAL,
                    entry_slippage REAL,
                    exit_slippage REAL,
                    latency_ms INTEGER
                )
            ''')
        else:
            # PostgreSQL syntax
            create_table_sql = sql_text('''
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    trigger_reason TEXT NOT NULL,
                    symbol TEXT,
                    quantity INTEGER,
                    pnl REAL,
                    entry_price REAL,
                    exit_price REAL,
                    exit_reason TEXT,
                    trend_state TEXT,
                    atr REAL,
                    charges REAL,
                    net_pnl REAL,
                    entry_time TEXT,
                    exit_time TEXT,
                    duration_seconds REAL,
                    max_price REAL,
                    signal_time TEXT,
                    order_time TEXT,
                    expected_entry REAL,
                    expected_exit REAL,
                    entry_slippage REAL,
                    exit_slippage REAL,
                    latency_ms INTEGER
                )
            ''')
        
        def upgrade_schema(engine):
            from sqlalchemy import inspect
            with engine.connect() as conn:
                conn.execute(create_table_sql)
                if hasattr(conn, 'commit'): conn.commit()
                
                inspector = inspect(engine)
                columns = [col['name'] for col in inspector.get_columns('trades')]
                
                # Add missing columns if they don't exist
                new_columns = [
                    ('charges', 'REAL'),
                    ('net_pnl', 'REAL'),
                    ('entry_time', 'TEXT'),
                    ('exit_time', 'TEXT'),
                    ('duration_seconds', 'REAL'),
                    ('max_price', 'REAL'),
                    ('signal_time', 'TEXT'),
                    ('order_time', 'TEXT'),
                    ('expected_entry', 'REAL'),
                    ('expected_exit', 'REAL'),
                    ('entry_slippage', 'REAL'),
                    ('exit_slippage', 'REAL'),
                    ('latency_ms', 'INTEGER'),
                    ('trading_mode', 'TEXT'),  # Track Paper vs Live
                    # CONFIRMATORY momentum checks (lagging indicators)
                    ('momentum_price_rising', 'INTEGER'),  # 0/1 for False/True
                    ('momentum_accelerating', 'INTEGER'),  # 0/1 for False/True
                    ('momentum_index_sync', 'INTEGER'),  # 0/1 for False/True
                    ('momentum_volume_surge', 'INTEGER'),  # 0/1 for False/True
                    ('momentum_checks_passed', 'INTEGER'),  # Confirmatory checks passed (0-3)
                    # PREDICTIVE momentum checks (leading indicators)
                    ('predictive_order_flow', 'INTEGER'),  # 0/1 - Order Flow Bullish
                    ('predictive_divergence', 'INTEGER'),  # 0/1 - Positive Divergence
                    ('predictive_structure', 'INTEGER'),  # 0/1 - Structure Break
                    ('predictive_checks_passed', 'INTEGER'),  # Predictive checks passed (0-3)
                    ('trigger_system', 'TEXT'),  # Which system triggered: PREDICTIVE, CONFIRMATORY, BOTH, NONE
                    # 🆕 Entry/Exit Type Tracking
                    ('entry_type', 'TEXT'),  # NO_WICK_BYPASS, TREND_CONTINUATION, SUPERTREND_ENTRY
                    ('supertrend_hold_mode', 'TEXT'),  # TRENDING, FLAT, None
                    ('entry_option_st_state', 'TEXT'),  # UPTREND, DOWNTREND, None (option supertrend at entry)
                    ('exit_supertrend_reason', 'TEXT'),  # OPTION, INDEX, ENTRY_PRICE_HIT, etc.
                    # 🆕 Exit Mode Tracking
                    ('exit_mode', 'TEXT'),  # Standard or Aggressive Hold
                    ('direction', 'TEXT'),  # CE or PE
                    # 🆕 Candle Data Tracking
                    ('candle_open_price', 'REAL'),  # Candle open price at entry
                    ('candle_close_price', 'REAL'),  # Candle close price at exit
                    ('ucc', 'TEXT')  # User UCC for per-user data separation
                ]
                
                for col_name, col_type in new_columns:
                    if col_name not in columns:
                        conn.execute(sql_text(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type};"))
                
                if hasattr(conn, 'commit'): conn.commit() 

        upgrade_schema(today_engine)
        upgrade_schema(all_engine)
        
        try:
            with open("last_run_date.txt", "r") as f: last_run_date = f.read().strip()
        except FileNotFoundError: last_run_date = ""

        today_date = datetime.now().strftime("%Y-%m-%d")
        
        if last_run_date != today_date:
            print(f"New day detected. Clearing today's trade log...")
            # --- THIS IS THE CORRECTED LOGIC ---
            # It now ONLY clears the today_engine.
            with today_engine.begin() as conn:
                conn.execute(sql_text("DELETE FROM trades"))
            
            # The incorrect block that cleared all_engine has been REMOVED.
            
            with open("last_run_date.txt", "w") as f: f.write(today_date)
            print("Today's trade log cleared.")

        print("Databases setup complete.")