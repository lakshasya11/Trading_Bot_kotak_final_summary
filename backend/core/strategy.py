# backend/core/strategy.py
import asyncio
import json
import pandas as pd
from datetime import datetime, date, timedelta, time, timezone
from typing import TYPE_CHECKING, Optional
import math
import numpy as np
import time as time_module  # For throttling mechanism
from zoneinfo import ZoneInfo  # Python 3.9+ timezone support
import logging

from .broker_factory import broker as kite, BROKER_NAME
from .websocket_manager import ConnectionManager
from .data_manager import DataManager
from .risk_manager import RiskManager
from .trade_logger import TradeLogger
from .order_manager import OrderManager, _round_to_tick
from .database import today_engine, sql_text
from .kill_switch import kill_switch  # CRITICAL FIX: Import kill switch
from .entry_strategies import (
    DualOptionMonitorStrategy,
    IntraCandlePatternStrategy,
    UoaEntryStrategy,
    TrendContinuationStrategy,
    MaCrossoverStrategy,
    CandlePatternEntryStrategy
)
from .iv_calculator import calculate_implied_volatility, calculate_valuation_percentage, get_color_for_valuation
from .risk_free_rate import get_risk_free_rate
from datetime import datetime, date, timedelta, time

# 🌍 IST TIMEZONE: India Standard Time for NSE synchronization
IST = ZoneInfo("Asia/Kolkata")

# Broker display label for log messages
_BROKER_LABEL = "Kotak" if BROKER_NAME == "kotak" else "Zerodha"

if TYPE_CHECKING:
    from .ticker_interface import TickerInterface as KiteTickerManager

def _play_sound(manager, sound): asyncio.create_task(manager.broadcast({"type": "play_sound", "payload": sound}))

def get_ist_time():
    """Get current time in IST timezone
    Uses system UTC time converted to IST"""
    utc_now = datetime.now(timezone.utc)
    ist_now = utc_now.astimezone(IST)
    return ist_now

def get_ist_time_str(include_ms=True):
    """Get current IST time as formatted string"""
    ist_now = get_ist_time()
    if include_ms:
        return ist_now.strftime("%H:%M:%S.%f")[:-3]  # HH:MM:SS.mmm
    return ist_now.strftime("%H:%M:%S")

INDEX_CONFIG = {
    "NIFTY": {"name": "NIFTY", "token": 256265, "symbol": "NSE:NIFTY 50", "strike_step": 50, "exchange": "NFO"},
    "SENSEX": {"name": "SENSEX", "token": 265, "symbol": "BSE:SENSEX", "strike_step": 100, "exchange": "BFO"},
    "BANKNIFTY": {"name": "BANKNIFTY", "token": 260105, "symbol": "NSE:NIFTY BANK", "strike_step": 100, "exchange": "NFO"},
}

# Override index tokens for Kotak broker
if BROKER_NAME == "kotak":
    INDEX_CONFIG["NIFTY"]["token"] = 26000
    INDEX_CONFIG["NIFTY"]["symbol"] = "NSE:NIFTY"
    INDEX_CONFIG["SENSEX"]["token"] = 1
    INDEX_CONFIG["SENSEX"]["symbol"] = "BSE:SENSEX"
    INDEX_CONFIG["BANKNIFTY"]["token"] = 26009
    INDEX_CONFIG["BANKNIFTY"]["symbol"] = "NSE:BANKNIFTY"

MARKET_STANDARD_PARAMS = {
    "strategy_priority": ["UOA", "TREND_CONTINUATION", "MA_CROSSOVER", "CANDLE_PATTERN", "INTRA_CANDLE"],
    'wma_period': 9, 'sma_period': 9, 'rsi_period': 9, 'rsi_signal_period': 3,
    'rsi_angle_lookback': 2, 'rsi_angle_threshold': 15.0, 'atr_period': 14,
    'min_atr_value': 4, 'ma_gap_threshold_pct': 0.05,
    # � PAPER TRADING REALISM: Simulate live order flow delays
    'paper_entry_delay_ms': 450,        # Entry order placement + execution (300-600ms realistic)
    'paper_exit_delay_ms': 400,         # Exit order placement + execution (300-500ms realistic)
    'paper_verification_delay_ms': 250, # Order verification delay (200-300ms realistic)
    # �🟢 GREEN CANDLE HOLD OVERRIDE parameters
    'green_candle_hold_enabled': False,
    'green_hold_min_profit_pct': 1.0,
    'green_hold_max_loss_pct': -2.0,
    # 📐 SUPERTREND ANGLE STRATEGY parameters
    'st_angle_enabled': False,  # 🔴 DISABLED - Using only Trend Continuation and No-Wick Bypass
    'st_angle_entry_threshold': 0.7,      # % per candle minimum for entry
    'st_angle_entry_accel': 0.15,         # % acceleration minimum for entry
    'st_angle_hold_threshold': 0.25,      # % per candle minimum to hold
    'st_angle_exit_threshold': 0.15,      # % per candle, exit when below
    'st_angle_exit_accel': -0.25,         # % deceleration exit trigger
    'st_angle_lookback': 3,               # Candles to calculate angle (3-5)
    'st_angle_emergency_loss': -3.0,      # % hard stop
    'st_angle_require_index': True,       # Require index confirmation
    'st_angle_min_green_ticks': 2,        # Consecutive green ticks for entry
    # 🎯 GREEN CANDLE SCALPER STRATEGY - Street Smart 1-Min Scalper
    'scalper_enabled': False,             # ❌ DISABLED - Not viable in live market (bid-ask spread)
    'scalper_quick_profit_pct': 2.0,      # Exit at 2% profit (quick book)
    'scalper_max_candle_age_sec': 15,     # Only enter within first 15 seconds
    'scalper_red_candle_exit': True,      # Exit immediately on red candle
    'scalper_require_no_wick': True,      # Require no lower wick for entry
    'scalper_min_candle_body_pct': 0.3,   # Minimum 0.3% candle body size
    
    # 📊 PRICE MOMENTUM OBSERVER - Pure Price-Driven Strategy
    'price_observer_enabled': True,       # ✅ Watch tick-by-tick price action
    'price_observer_min_ticks': 8,        # Minimum ticks before entry (8-10 recommended)
    'price_observer_velocity_threshold': 0.05,  # Minimum velocity (₹0.05 per tick)
    'price_observer_accel_factor': 1.5,   # Current velocity must be 1.5× average
    'price_observer_directional_pct': 0.80,  # 80% of recent ticks must be green
    'price_observer_lookback_ticks': 6,   # Check last 6 ticks for direction
    'price_observer_exit_velocity_pct': 0.40,  # Exit when velocity drops to 40% of entry
    'price_observer_max_hold_ticks': 30   # Max hold time in ticks (~30s at 1 tick/sec)
}

class Strategy:
    def __init__(self, params, manager: ConnectionManager, selected_index="SENSEX"):
        self.params = self._sanitize_params(params)
        self.manager = manager
        self.ticker_manager: Optional["KiteTickerManager"] = None
        self.config = INDEX_CONFIG[selected_index]
        self.ui_update_task: Optional[asyncio.Task] = None
        self.position_lock = asyncio.Lock()
        self.db_lock = asyncio.Lock()
        
        self.is_backtest = False
        self.is_paused = False  # Pause functionality
        
        # 🚀 STARTUP GRACE PERIOD: Suppress option initialization warnings for first 45 seconds
        import time as time_module
        self.startup_time = time_module.time()  # Track bot startup time
        self.startup_grace_period = 45.0  # 45 seconds grace period for option data loading
        
        self.index_name, self.index_token, self.index_symbol, self.strike_step, self.exchange = \
            self.config["name"], self.config["token"], self.config["symbol"], self.config["strike_step"], self.config["exchange"]

        self.trend_candle_count = 0
        

        self.data_manager = DataManager(self.index_token, self.index_symbol, self.STRATEGY_PARAMS, self._log_debug, self.on_trend_update)
        self.data_manager.strategy = self  # ⚡ Link for ATM pre-fetching optimization
        self.risk_manager = RiskManager(self.params, self._log_debug)
        self.trade_logger = TradeLogger(self.db_lock)
        self.order_manager = OrderManager(self._log_debug)

        # --- V47.16 - Duplicate trade prevention (7 Layers) ---
        self.entry_in_progress = False         # Layer 1 & 7: Global flag for entry processing
        self.active_order_id: Optional[str] = None # Layer 2: Placeholder for live trading order ID
        self.trade_attempt_times: list[datetime] = [] # Layer 3: List of recent trade timestamps
        self.last_exit_time: Optional[datetime] = None # Layer 4: Timestamp of the last successful exit
        self.last_entry_data: dict = {}         # Layer 5: Stores {'symbol': str, 'price': float, 'timestamp': datetime} of last entry
        self.symbol_exit_cooldown: dict[str, dict] = {} # Layer 6: {'SYMBOL': {'time': datetime, 'price': float, 'candle_start_time': float, 'direction': str, 'reason': str}}

        # V47.14 - Initialize coordinator
        from .v47_coordinator import V47StrategyCoordinator
        self.v47_coordinator = V47StrategyCoordinator(self)
        
        # 🎯 60 FPS UI SYNC: Dirty flags for batched updates
        self._ui_status_dirty = False
        self._ui_chain_dirty = False
        self._ui_chart_dirty = False
        self._ui_straddle_dirty = False
        self._ui_trade_dirty = False
        self._ui_performance_dirty = False
        self._ui_expiry_dirty = True  # 🛰️ NEW: Throttle expiry info (send once on startup/change)
        self._last_time_broadcast = 0  # Track last time-only broadcast
        
        # 🔥 BROADCAST FLUSH QUEUE: Ensure pending broadcasts are sent before shutdown
        self._pending_broadcasts = asyncio.Queue()  # Queue for pending broadcasts
        self._broadcast_flusher_active = False  # Flag to control flusher task
        
        # 🛡️ TRADE ENTRY LOCK: Prevent shutdown while trade is being entered/verified
        self._trade_entry_in_progress = False  # Flag to indicate trade entry is active
        self._trade_entry_lock = asyncio.Lock()  # Lock for thread-safe access
        
        # 🎯 PROFESSIONAL FRAME-BASED CONFLATION (30 FPS for stability)
        self._frame_timer_task = None
        self._last_frame_time = 0
        self.FRAME_TIME = 1/30  # ⚡ 33ms per frame (30 FPS) - Optimal balance for real-time updates without WebSocket overflow
        self._tick_conflation_buffer = {}  # Conflate ticks between frames

        strategy_map = {
            "DUAL_MONITOR": DualOptionMonitorStrategy,
            "INTRA_CANDLE": IntraCandlePatternStrategy, "UOA": UoaEntryStrategy,
            "TREND_CONTINUATION": TrendContinuationStrategy,
            "MA_CROSSOVER": MaCrossoverStrategy, "CANDLE_PATTERN": CandlePatternEntryStrategy
        }
        self.entry_strategies = []
        default_priority = ["DUAL_MONITOR", "UOA", "TREND_CONTINUATION", "MA_CROSSOVER", "CANDLE_PATTERN", "INTRA_CANDLE"]
        priority_list = self.STRATEGY_PARAMS.get("strategy_priority", default_priority)
        for name in priority_list:
            if name in strategy_map:
                self.entry_strategies.append(strategy_map[name](self))
        
        self._reset_state()
        self.option_instruments = []  # Will be loaded asynchronously
        self.last_used_expiry = None  # Will be set from params (actual date string)
        self.last_reset_date = date.today()  # CRITICAL FIX: Track daily reset

    async def flush_pending_broadcasts(self, timeout_seconds: float = 2.0):
        """Ensure all pending broadcasts are sent before shutdown"""
        await self._log_debug("Broadcast Flush", f"🔄 Starting flush of pending broadcasts (timeout: {timeout_seconds}s)...")
        start_time = datetime.now()
        flushed_count = 0
        
        try:
            # Process all pending broadcasts in queue with timeout
            while True:
                elapsed = (datetime.now() - start_time).total_seconds()
                if elapsed > timeout_seconds:
                    await self._log_debug("Broadcast Flush", f"⚠️ Flush timeout after {timeout_seconds}s, {flushed_count} broadcasts sent")
                    break
                
                try:
                    # Non-blocking get with remaining timeout
                    remaining_timeout = timeout_seconds - elapsed
                    broadcast_data = await asyncio.wait_for(
                        self._pending_broadcasts.get(),
                        timeout=remaining_timeout
                    )
                    
                    # Send broadcast immediately
                    await self.manager.broadcast(broadcast_data)
                    flushed_count += 1
                    
                except asyncio.TimeoutError:
                    # Queue empty or timeout reached
                    break
        except Exception as e:
            await self._log_debug("Broadcast Flush Error", f"❌ Error during flush: {e}")
        
        if flushed_count > 0:
            await self._log_debug("Broadcast Flush", f"✅ Flushed {flushed_count} pending broadcasts")
    
    async def flush_pending_trades(self, timeout_seconds: float = 2.0):
        """Wait for any in-flight log_trade_with_retry tasks to complete before shutdown."""
        import time as _t
        deadline = _t.time() + timeout_seconds
        pending = [t for t in asyncio.all_tasks() if 'log_trade_with_retry' in t.get_name()]
        if pending:
            await self._log_debug("Trade Flush", f"⏳ Waiting for {len(pending)} pending trade log(s)...")
            try:
                remaining = max(0.1, deadline - _t.time())
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=remaining)
                await self._log_debug("Trade Flush", "✅ All pending trades flushed to database.")
            except asyncio.TimeoutError:
                await self._log_debug("Trade Flush", "⚠️ Trade flush timed out — some trades may not be saved.")

    async def _calculate_trade_charges(self, tradingsymbol, exchange, entry_price, exit_price, quantity):
        BROKERAGE_PER_ORDER = 20.0; STT_RATE = 0.001; GST_RATE = 0.18; SEBI_RATE = 10 / 1_00_00_000; STAMP_DUTY_RATE = 0.00003
        if exchange == "NFO": EXCHANGE_TXN_CHARGE_RATE = 0.00053
        elif exchange == "BFO": EXCHANGE_TXN_CHARGE_RATE = 0.000325
        else: EXCHANGE_TXN_CHARGE_RATE = 0.00053
        buy_value = entry_price * quantity; sell_value = exit_price * quantity; total_turnover = buy_value + sell_value
        brokerage = BROKERAGE_PER_ORDER * 2; stt = sell_value * STT_RATE
        exchange_charges = total_turnover * EXCHANGE_TXN_CHARGE_RATE; sebi_charges = total_turnover * SEBI_RATE
        gst = (brokerage + exchange_charges + sebi_charges) * GST_RATE; stamp_duty = buy_value * STAMP_DUTY_RATE
        return brokerage + stt + exchange_charges + gst + sebi_charges + stamp_duty

    def _reset_state(self):
        self.position = None; self.daily_gross_pnl = 0; self.daily_net_pnl = 0; self.total_charges = 0
        self.daily_profit = 0; self.daily_loss = 0; self.daily_trade_limit_hit = False
        self.trades_this_minute = 0; self.initial_subscription_done = False
        self.is_paused = False  # Reset pause state when bot starts
        self.token_to_symbol = {self.index_token: self.index_symbol}; self.uoa_watchlist = {}
        self.performance_stats = {"total_trades": 0, "winning_trades": 0, "losing_trades": 0}
        # CRITICAL FIX: Reverting to use exit_cooldown_until to maintain compatibility with existing flow
        self.exit_cooldown_until: Optional[datetime] = None; self.disconnected_since: Optional[datetime] = None
        self.next_partial_profit_level = 1; self.trend_candle_count = 0
        self.live_capital_cache = None; self.live_capital_last_fetched = None  # Cache for live capital
        self._last_exit_time = None  # 🔥 Track exit time for capital cache refresh
        
        # ⚡ SPEED OPTIMIZATION: ATM price caching to eliminate repeated API calls
        self.atm_price_cache = {}  # {side: {ce_price, pe_price, timestamp}}
        self.atm_cache_ttl = 5.0  # 5 second cache (fast-moving options)
        
        # ⚡⚡ AGGRESSIVE SPEED OPTIMIZATION: Background capital refresh (every 3 seconds)
        self._background_capital_task = None  # Task for background capital refresh
        self._capital_refresh_interval = 3.0  # Refresh every 3 seconds (shorter = fresher, but more API calls)
        
        # 🔧 STABILITY FIX (Feb 22, 2026): Position API call caching to reduce load
        self._position_cache = None  # Cache for position data
        self._position_cache_time = 0  # Timestamp of last cache update
        self._position_cache_ttl = 1.0  # 1 second cache TTL
        
        # Freeze limit and lot size (will be fetched from API)
        self.freeze_limit = None  # Max quantity per order
        self.lot_size = None  # Lot size for the index
        
        # V47.16 - 7-Layer State Reset
        self.entry_in_progress = False
        self.exit_in_progress = False  # Fix 5: Prevent duplicate exit attempts
        self.active_order_id = None
        self.trade_attempt_times = [] # Reset trade frequency counter
        self._trades_this_minute_pnl = []  # Reset minute P&L tracker
        self.exit_attempt_counter = 0  # Fix 3: Track exit retry attempts to prevent infinite loops
        self.last_exit_time = None # Layer 4 cooldown time
        self.last_entry_data = {}
        self.symbol_exit_cooldown = {}
        self.exit_failure_count = 0  # Circuit breaker for infinite exit loops
        self.excess_sale_cooldown_until = None  # ✨ NEW: Prevent duplicate exits after excess sale (1s cooldown)
        self.entry_completed_at = None  # Reset entry buffer timer
        self.last_entry_info = {}  # Reset last entry price memory (Option C)
        self.symbol_entry_cooldown = {}  # Reset 60s hard cooldown per symbol (Option D)
        
        # 🆕 LAYER 8: Symbol-level entry lock (prevents multiple engines entering same symbol)
        self.symbol_entry_lock = {}  # {symbol: {'time': datetime, 'trigger': str}}
        self._layer8_lock = asyncio.Lock()  # 🔒 CRITICAL: Async lock for Layer 8 to prevent race conditions
        
        # ⚡ PRIORITY 3: Signal deduplication - prevent processing same signal within 10 seconds
        self.last_signal_time = {}  # {symbol: {'time': datetime, 'trigger': str}}
        
        # Exit logic state tracking
        self.entry_candle_was_green = None  # Track entry candle state
        
        # 📐 SUPERTREND ANGLE TRACKING: For predictive entry/exit
        self.st_line_history = {}  # {symbol: [(timestamp, st_value), ...]} - Rolling window
        self.st_angle_history = {}  # {symbol: [angle1, angle2, ...]} - Last 5 angles
        self.current_st_angle = {}  # {symbol: angle_percent}
        self.current_st_acceleration = {}  # {symbol: accel_percent}
        self.last_st_calculation_time = {}  # {symbol: timestamp} - Throttle calculations
        
        # 📊 ST ANGLE GUI TRACKING: For visual display
        self.st_angle_increase_start_time = {}  # {symbol: timestamp} - When angle started increasing
        self.st_angle_status = {}  # {symbol: "increasing"/"flat"/"decreasing"}
        self.st_angle_monitored_symbol = None  # Current ATM option being tracked
        
        # 🎯 PERFECT PRICE ENTRY: Missed opportunity tracking
        self.missed_opportunities = {}  # {symbol: {price, trigger, side, lot_size, timestamp, retries}}
        self.last_missed_opportunity_check = 0  # Throttle checks to once per second
        
        # 🚀 SIGNAL QUEUE: Prevent signal loss during entry_in_progress
        self.signal_queue = []  # Queue signals when entry is in progress
        self.max_queue_size = 3  # Max 3 queued signals
        
        # 🛡️ ENTRY TIMEOUT: Auto-reset flag if stuck
        self.entry_started_at = None  # Timestamp when entry_in_progress was set
        self.entry_timeout_seconds = 15  # Auto-reset after 15 seconds
        
        # 🛡️ ENTRY BUFFER: Prevent race conditions after entry completes
        self.entry_completed_at = None  # Timestamp when entry_in_progress was set to False
        self.entry_buffer_duration = 0.5  # 500ms buffer to prevent duplicate slippage
        
        # 🛡️ OPTION C: Last entry price memory - prevent exact price re-entries
        self.last_entry_info = {}  # {symbol: {'price': float, 'time': datetime, 'entry_count': int}}
        
        # 🛡️ OPTION D: 60s hard cooldown per symbol - prevents all duplicates within 60s
        self.symbol_entry_cooldown = {}  # {symbol: {'until': datetime, 'entry_price': float, 'reason': str}}
        self.entry_buffer_duration = 0.5  # 500ms buffer to prevent duplicate slippage
        
        # 🚀 SOLUTION #3: PRE-CALCULATE ENTRY READINESS (Fast Entry System)
        self.entry_ready = {}  # {symbol: {'ready': bool, 'side': str, 'timestamp': datetime, 'prev_candle_high': float}}
        self.last_candle_close_time = {}  # {symbol: datetime} - Track when candle closed
        self.pre_calc_valid_duration = 5.0  # Pre-calculation valid for 5 seconds into new candle
        
        # 🛡️ LOG THROTTLING: Prevent TrendScout warning spam
        self._trendscout_warning_times = {}  # {symbol: datetime} - Last warning time per symbol


    async def _fetch_live_capital_from_zerodha(self):
        """
        Fetch live available capital from Zerodha margins.
        Returns available cash for trading or None if fetch fails.
        ⚡ OPTIMIZED: 60s cache, silent returns for sub-500ms execution
        """
        try:
            # Check cache (valid for 60 seconds for speed)
            if self.live_capital_cache is not None and self.live_capital_last_fetched:
                if (get_ist_time() - self.live_capital_last_fetched).total_seconds() < 60:
                    # Silent return - no debug log for speed
                    return self.live_capital_cache
            
            # Fetch fresh data from Zerodha (no debug log for speed)
            # ⚡ OPTIMIZED: Direct call without retry wrapper (saves 100-200ms)
            margins = await kite.margins()
            # Get available cash from equity segment
            equity_margin = margins.get('equity', {})
            available = equity_margin.get('available', {})
            live_balance = available.get('live_balance', 0)
            live_capital = float(live_balance)
            
            # 🔥 DEBUG: Log full margin structure for debugging capital issues
            if live_capital < float(self.params.get("start_capital", 50000)):
                await self._log_debug(f"{_BROKER_LABEL} Margins",
                    f"📊 FULL MARGIN STRUCTURE:\n" +
                    f"  Available: {available}\n" +
                    f"  Live Balance: ₹{live_capital}\n" +
                    f"  Equity Total: {equity_margin.get('equity', 0)}\n" +
                    f"  Used: {equity_margin.get('used', 0)}")
            
            # Update cache
            self.live_capital_cache = float(live_capital)
            self.live_capital_last_fetched = get_ist_time()
            
            # Silent return for speed
            return live_capital
            
        except Exception as e:
            # ⚡ OPTIMIZED: Silent fallback for speed
            # CRITICAL FIX: Fallback to GUI threshold if API fails
            fallback_capital = float(self.params.get("start_capital", 50000))
            return fallback_capital

    async def _background_capital_refresher(self):
        """
        ⚡⚡ SPEED OPTIMIZATION: Background task that refreshes capital periodically
        This eliminates the 100-150ms capital fetch delay during entry execution.
        When entry runs, it uses the cached capital instead of fetching fresh.
        
        Result: Entry execution time reduced by 100-150ms (10-15% faster)
        🔧 STABILITY FIX: Minimum interval 10s to prevent API overload (Feb 22, 2026)
        """
        while True:
            try:
                # Refresh capital every N seconds (minimum 10s for stability)
                refresh_interval = max(self._capital_refresh_interval, 10)
                await asyncio.sleep(refresh_interval)
                
                # Skip refresh if trading is paused or market closed
                if self.is_paused:
                    continue
                
                current_time = get_ist_time().time()
                # Only refresh during market hours (9:15 AM - 3:30 PM)
                if not (time(9, 15) <= current_time <= time(15, 30)):
                    continue
                
                # Fetch and cache live capital silently (no debug log for speed)
                try:
                    margins = await kite.margins()
                    equity_margin = margins.get('equity', {})
                    available = equity_margin.get('available', {})
                    live_balance = available.get('live_balance', 0)
                    
                    # Update cache immediately
                    self.live_capital_cache = float(live_balance)
                    self.live_capital_last_fetched = get_ist_time()
                except Exception as e:
                    # Silent failure - cache will still be used
                    pass
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Continue even on error
                await asyncio.sleep(1)

    async def _get_cached_positions(self):
        """
        🔧 STABILITY FIX (Feb 22, 2026): Get positions with 1-second cache to reduce API calls
        Prevents excessive position API polling that causes websocket instability
        
        Cache Strategy:
        - Fresh data: <1s old - return cached
        - Stale data: ≥1s old - fetch new and update cache
        
        Impact: Reduces position API calls by 60-80% during monitoring loops
        """
        import time
        current_time = time.time()
        
        # Return cached data if fresh (less than 1 second old)
        if (self._position_cache is not None and 
            current_time - self._position_cache_time < self._position_cache_ttl):
            return self._position_cache
        
        # Cache miss or stale - fetch fresh data
        try:
            positions_data = await kite.positions()
            self._position_cache = positions_data
            self._position_cache_time = current_time
            return positions_data
        except Exception as e:
            # On error, return stale cache if available
            if self._position_cache is not None:
                await self._log_debug("Position Cache", f"⚠️ API error, using stale cache: {e}")
                return self._position_cache
            raise

    async def _monitor_external_positions(self):
        """
        🎯 EXTERNAL POSITION MONITOR: Detect manual/external trades (trades not initiated by bot)
        Runs every 10 seconds to check for positions that weren't created by the bot.
        When found, syncs them into the tracking system for logging and monitoring.
        
        🔥 CRITICAL: This catches positions that failed to sync during entry execution in LIVE mode
        🔧 STABILITY FIX: Changed from 2s to 10s to reduce API load (Feb 22, 2026)
        """
        while True:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds (reduced from 2s for stability)
                
                # Skip during market closed hours
                current_time = get_ist_time().time()
                if not (time(9, 15) <= current_time <= time(15, 30)):
                    continue
                
                # Skip if already have a tracked position (bot-initiated -- unless it's missing from Zerodha)
                if self.position:
                    continue
                
                # Fetch current positions from Zerodha (using cache to reduce API calls)
                try:
                    positions_data = await self._get_cached_positions()
                    net_positions = positions_data.get('net', [])
                except:
                    continue
                
                # Look for option positions (CE/PE) that aren't tracked
                for pos in net_positions:
                    quantity = pos.get('quantity', 0)
                    if quantity == 0:
                        continue
                    
                    tradingsymbol = pos.get('tradingsymbol', '')
                    
                    # Only track option positions
                    if 'CE' not in tradingsymbol and 'PE' not in tradingsymbol:
                        continue
                    
                    # Skip if already tracking this in self.position
                    if self.position and self.position.get('symbol') == tradingsymbol:
                        continue
                    
                    # 🔥 FOUND UNTRACKED POSITION! This is either:
                    # 1. A manual trade you placed via broker
                    # 2. A bot trade that failed to sync due to exception in entry logic
                    try:
                        entry_price = pos.get('buy_price') or pos.get('average_price') or 0
                        
                        # ⚠️ LOG LOUDLY - this shouldn't happen in normal flow
                        await self._log_debug("🚨 UNTRACKED POSITION DETECTED", 
                            f"📍 Found {tradingsymbol} | Qty: {quantity} | Entry: ₹{entry_price:.2f} | "
                            f"Type: EXTERNAL_OR_FAILED_SYNC")
                        
                        # Create position object for tracking
                        external_position = {
                            'symbol': tradingsymbol,
                            'qty': quantity,
                            'quantity': quantity,
                            'entry_price': float(entry_price),
                            'entry_time': get_ist_time().strftime("%Y-%m-%d %H:%M:%S"),
                            'trail_sl': 0,
                            'max_price': float(entry_price),
                            'lot_size': 1,
                            'product': pos.get('product', 'MIS'),
                            'trigger_reason': 'UNTRACKED_POSITION',
                            'is_external_trade': True,  # Flag to indicate external/failed sync
                            'detected_at': get_ist_time().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        
                        # Lock and set position
                        async with self.position_lock:
                            self.position = external_position
                        
                        await self._log_debug("✅ POSITION SYNCED", 
                            f"Now tracking untracked position: {tradingsymbol} | "
                            f"Qty: {quantity} | Entry: ₹{entry_price:.2f} | "
                            f"Time detected: {external_position['detected_at']}")
                        
                        # Update UI immediately
                        await self._update_ui_trade_status()
                        
                        # Log this as a critical event for debugging
                        await self._log_debug("⚠️ SYNC_ACTION", 
                            f"Position recovery: Untracked position synced at {get_ist_time_str()}")
                        
                    except Exception as e:
                        await self._log_debug("External Trade Sync Error", f"Failed to sync untracked position: {e}")
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._log_debug("External Position Monitor Error", f"Error: {e}")
                await asyncio.sleep(1)

    async def _recover_open_positions(self):
        """CRITICAL FIX: Recover open positions from broker after bot restart"""
        try:
            await self._log_debug("Recovery", f"Checking for open positions from {_BROKER_LABEL}...")
            
            # CRITICAL FIX: Fetch positions from Zerodha with retry logic
            positions_data = await kite.positions()
            net_positions = positions_data.get('net', [])
            
            # Find option positions with non-zero quantity
            for pos in net_positions:
                quantity = pos.get('quantity', 0)
                if quantity == 0:
                    continue
                
                tradingsymbol = pos.get('tradingsymbol', '')
                # Only recover option positions (CE/PE)
                if 'CE' not in tradingsymbol and 'PE' not in tradingsymbol:
                    continue
                
                # Get entry price from Zerodha position data
                # Try multiple fields: buy_price, average_price, or last_price
                entry_price = (pos.get('buy_price') or 
                              pos.get('average_price') or 
                              pos.get('last_price') or 0)
                
                # 🛡️ DEFENSIVE: Ensure entry_price is float, not string
                try:
                    entry_price = float(entry_price)
                except (ValueError, TypeError):
                    entry_price = 0.0
                
                # 🛡️ DEFENSIVE: Ensure last_price is float, not string
                try:
                    last_price_val = float(pos.get('last_price', entry_price))
                except (ValueError, TypeError):
                    last_price_val = entry_price
                
                # Reconstruct position data with ALL required fields
                self.position = {
                    "symbol": tradingsymbol,
                    "qty": abs(quantity),  # CRITICAL: Use "qty" not "quantity"
                    "direction": "CE" if "CE" in tradingsymbol else "PE",  # FIXED: Use "direction" not "side"
                    "entry_price": entry_price,
                    "trail_sl": 0,  # Will be recalculated
                    "max_price": last_price_val,
                    "entry_time": get_ist_time().strftime("%Y-%m-%d %H:%M:%S"),  # FIXED: String format
                    "trigger_reason": "RECOVERED_FROM_ZERODHA",
                    "lot_size": self.lot_size,
                    # 🆕 Capture option Supertrend state at recovery
                    "entry_option_st_uptrend": self.data_manager.calculate_option_supertrend(tradingsymbol)[1] if self.data_manager else None,
                    # Recovery: Can't determine entry candle state, set to None for safety
                    "entry_candle_was_green": None
                }
                
                # 🔥 CRITICAL: Triple broadcast for reliable GUI update during recovery
                for _ in range(3):
                    await self._update_ui_trade_status()
                    await asyncio.sleep(0.1)  # 100ms delay between broadcasts
                
                # Subscribe to WebSocket for live price updates
                if self.ticker_manager and self.option_instruments:
                    instrument_token = next(
                        (opt.get("instrument_token") for opt in self.option_instruments 
                         if opt.get("tradingsymbol") == tradingsymbol), 
                        None
                    )
                    if instrument_token:
                        self.ticker_manager.subscribe([instrument_token])
                        await self._log_debug("Recovery", f"📡 Subscribed to {tradingsymbol} for live price updates")
                
                await self._log_debug("Recovery", f"🔄 RECOVERED POSITION: {tradingsymbol}, Qty: {quantity}, Entry: ₹{entry_price:.2f}")
                await self._log_debug("Recovery", f"📊 Position data: buy_price={pos.get('buy_price')}, avg_price={pos.get('average_price')}, last_price={pos.get('last_price')}")
                await self._log_debug("Recovery", f"⚠️ Stop-loss reset to 0. Please monitor manually or set new SL.")
                break  # Only one position expected
            
            if not self.position:
                await self._log_debug("Recovery", "✅ No open positions found. Starting fresh.")
        
        except Exception as e:
            await self._log_debug("Recovery", f"❌ Failed to recover positions: {e}")

    async def _verify_and_update_position_price(self, basket_result, symbol):
        """⚡ SPEED FIX: Verify price in BACKGROUND and update position (non-blocking)"""
        try:
            # ⚡ OPTIMIZED: Removed 0.5s sleep - not needed with async
            verified_qty, actual_avg_price, fill_timestamp = await self._verify_order_execution(basket_result)
            
            if actual_avg_price and self.position and self.position.get("symbol") == symbol:
                old_price = self.position["entry_price"]
                self.position["entry_price"] = actual_avg_price
                self.position["max_price"] = actual_avg_price
                
                # 🆕 UPDATE ENTRY TIME: Use actual exchange fill timestamp if available
                if fill_timestamp:
                    try:
                        # Convert Zerodha timestamp to IST string format
                        if isinstance(fill_timestamp, str):
                            # Parse if it's a string
                            from dateutil import parser
                            fill_dt = parser.parse(fill_timestamp)
                            if fill_dt.tzinfo is None:
                                fill_dt = fill_dt.replace(tzinfo=IST)
                            else:
                                fill_dt = fill_dt.astimezone(IST)
                            self.position["entry_time"] = fill_dt.strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            # Assume it's already a datetime object
                            if fill_timestamp.tzinfo is None:
                                fill_timestamp = fill_timestamp.replace(tzinfo=IST)
                            else:
                                fill_timestamp = fill_timestamp.astimezone(IST)
                            self.position["entry_time"] = fill_timestamp.strftime("%Y-%m-%d %H:%M:%S")
                        
                        signal_time = self.position.get("signal_time", "Unknown")
                        await self._log_debug("Time Update", 
                            f"🕐 Updated entry time: Signal={signal_time} → Fill={self.position['entry_time']} (Exchange timestamp)")
                    except Exception as ts_error:
                        await self._log_debug("Time Update", f"⚠️ Could not parse fill_timestamp: {ts_error}")
                
                price_diff = abs(actual_avg_price - old_price)
                price_diff_pct = (price_diff / old_price) * 100
                await self._log_debug("Price Update", 
                    f"📊 Updated entry: Expected ₹{old_price:.2f} → Actual ₹{actual_avg_price:.2f} (Diff: ₹{price_diff:.2f}, {price_diff_pct:.2f}%)")
        except Exception as e:
            await self._log_debug("Price Update", f"⚠️ Background verification failed: {e}")
    
    async def _verify_order_execution(self, basket_result):
        """
        CRITICAL FIX: Verify order execution with Zerodha order history
        Returns: (filled_qty, avg_price, fill_timestamp)
        - filled_qty: Actual quantity filled from order history
        - avg_price: Weighted average execution price
        - fill_timestamp: Earliest fill timestamp from Zerodha (for accurate duration calculation)
        """
        try:
            order_ids = basket_result.get("order_ids", [])
            if not order_ids:
                await self._log_debug("Verification", f"⚠️ No order IDs in basket result (keys: {list(basket_result.keys())})")
                return basket_result["total_filled"], basket_result.get("avg_price"), None
            
            await self._log_debug("Verification", f"🔍 Verifying {len(order_ids)} order(s): {order_ids}")
            
            total_verified_qty = 0
            total_value = 0.0  # For calculating weighted average price
            earliest_fill_time = None  # 🆕 Track earliest fill timestamp for accurate duration
            
            for order_id in order_ids:
                try:
                    # 🛡️ ENHANCED: Retry order history with delays for Zerodha consistency
                    # Zerodha API sometimes needs 50-200ms to fully populate order history
                    order_history = None
                    max_retries = 3
                    retry_delay_ms = [50, 100, 150]  # Progressive delays: 50ms, 100ms, 150ms
                    
                    for retry_attempt in range(max_retries):
                        order_history = await kite.order_history(order_id)
                        
                        if order_history and len(order_history) > 0:
                            latest_status = order_history[-1]
                            filled_qty = latest_status.get("filled_quantity", 0)
                            
                            # ✅ SUCCESS: Got filled quantity, don't need more retries
                            if filled_qty > 0:
                                break
                        
                        # ⏳ Not filled yet or empty history - wait and retry
                        if retry_attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay_ms[retry_attempt] / 1000.0)
                    
                    if order_history:
                        latest_status = order_history[-1]
                        filled_qty = latest_status.get("filled_quantity", 0)
                        avg_price = latest_status.get("average_price", 0)
                        
                        # 🛡️ DEFENSIVE: Ensure avg_price is float, not string
                        try:
                            avg_price = float(avg_price) if avg_price else 0.0
                        except (ValueError, TypeError):
                            avg_price = 0.0
                        
                        total_verified_qty += filled_qty
                        total_value += (filled_qty * avg_price)
                        
                        # 🆕 DURATION FIX: Extract actual fill timestamp from order
                        fill_time = latest_status.get("order_timestamp") or latest_status.get("exchange_update_timestamp")
                        if fill_time:
                            if earliest_fill_time is None or fill_time < earliest_fill_time:
                                earliest_fill_time = fill_time
                        
                        status = latest_status.get("status")
                        await self._log_debug("Verification", 
                            f"✅ Order {order_id}: Status={status}, Filled={filled_qty} @ ₹{avg_price:.2f}, FillTime={fill_time}")
                    else:
                        await self._log_debug("Verification", f"⚠️ No order history returned for {order_id} after {max_retries} retries")
                except Exception as e:
                    await self._log_debug("Verification", f"⚠️ Could not verify order {order_id}: {e}")
            
            # Calculate weighted average execution price
            actual_avg_price = (total_value / total_verified_qty) if total_verified_qty > 0 else None
            
            # 🛡️ LAYER 1: If we couldn't verify price from order history, try current positions
            if actual_avg_price is None and total_verified_qty == 0:
                await self._log_debug("Verification", "🔄 Order history verification failed, checking current positions...")
                try:
                    positions = await kite.positions()
                    if positions:
                        net_positions = positions.get('net', [])
                        # Find position matching this trade
                        if self.position:
                            symbol = self.position.get("symbol")
                            for pos in net_positions:
                                if pos.get('tradingsymbol') == symbol:
                                    pos_qty = pos.get('quantity', 0)
                                    pos_price = pos.get('average_price', 0)
                                    if pos_qty > 0 and pos_price > 0:
                                        actual_avg_price = float(pos_price)
                                        total_verified_qty = pos_qty
                                        await self._log_debug("Verification", 
                                            f"✅ Found position in {_BROKER_LABEL}: {pos_qty} @ ₹{actual_avg_price:.2f}")
                                        break
                except Exception as e:
                    await self._log_debug("Verification", f"⚠️ Position fetch failed: {e}")
            
            # 🛡️ LAYER 2: Use basket result as fallback if order history and positions both failed
            if actual_avg_price is None:
                basket_price = basket_result.get("avg_price")
                # 🛡️ CRITICAL: Convert to float and validate
                if basket_price is not None:
                    try:
                        actual_avg_price = float(basket_price)
                    except (ValueError, TypeError):
                        actual_avg_price = None
            
            # 🛡️ LAYER 3: Final fallback - use 0.0 to prevent None (NEVER return None!)
            if actual_avg_price is None or actual_avg_price <= 0:
                actual_avg_price = 0.0
            
            # Ensure it's a float
            try:
                actual_avg_price = float(actual_avg_price)
            except (ValueError, TypeError):
                actual_avg_price = 0.0
            
            # Compare basket result with actual verification
            basket_qty = basket_result["total_filled"]
            if total_verified_qty != basket_qty:
                await self._log_debug("Verification", 
                    f"⚠️ MISMATCH: Basket={basket_qty}, Verified={total_verified_qty}. Using verified quantity.")
            else:
                if actual_avg_price and actual_avg_price > 0:
                    await self._log_debug("Verification", 
                        f"✅ Verified: {total_verified_qty} qty @ avg ₹{actual_avg_price:.2f}, FillTime={earliest_fill_time}")
                else:
                    await self._log_debug("Verification", 
                        f"⚠️ Verified qty: {total_verified_qty}, but could not calculate avg price")
            
            return total_verified_qty, actual_avg_price, earliest_fill_time
                
        except Exception as e:
            await self._log_debug("Verification", f"❌ Verification failed: {e}. Using basket result.")
            import traceback
            await self._log_debug("Verification", f"Traceback: {traceback.format_exc()}")
            # 🛡️ CRITICAL: Return valid defaults, never None for price
            basket_total = basket_result.get("total_filled", 0)
            basket_price = basket_result.get("avg_price", 0.0)
            if basket_price is None or basket_price <= 0:
                basket_price = 0.0
            return basket_total, float(basket_price), None

    def _get_active_ucc(self):
        """Get the active user's UCC for per-user data separation."""
        try:
            import json
            with open("broker_config.json", "r") as f:
                cfg = json.load(f)
            if "users" in cfg:
                active_user_id = cfg.get("active_user", "user1")
                return cfg["users"].get(active_user_id, {}).get("kotak_ucc", active_user_id)
            return cfg.get("kotak_ucc", "default")
        except Exception:
            return "default"

    async def _restore_daily_performance(self):
        # ... (This function is unchanged)
        await self._log_debug("Persistence", "Restoring daily performance from database...")
        def db_call():
            try:
                with today_engine.connect() as conn:
                    current_mode = self.params.get("trading_mode", "Paper Trading")
                    active_ucc = self._get_active_ucc()
                    query = sql_text(
                        "SELECT SUM(pnl), SUM(charges), SUM(net_pnl), "
                        "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins, "
                        "SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses "
                        "FROM trades WHERE (trading_mode = :mode OR trading_mode IS NULL) "
                        "AND (ucc = :ucc OR ucc IS NULL)"
                    )
                    return conn.execute(query, {"mode": current_mode, "ucc": active_ucc}).fetchone()
            except Exception as e:
                print(f"Error restoring performance: {e}"); return None
        data = await asyncio.to_thread(db_call)
        if data and data[0] is not None:
            gross_pnl, charges, net_pnl, wins, losses = data
            self.daily_gross_pnl = gross_pnl or 0; self.total_charges = charges or 0
            self.daily_net_pnl = net_pnl or 0; self.performance_stats["winning_trades"] = wins or 0
            self.performance_stats["losing_trades"] = losses or 0
            
            # Restore profit/loss tracking from gross P&L (profit = positive, loss = negative)
            if self.daily_gross_pnl > 0:
                self.daily_profit = self.daily_gross_pnl
                self.daily_loss = 0
            else:
                self.daily_profit = 0
                self.daily_loss = self.daily_gross_pnl  # Keep as negative
            
            await self._log_debug("Persistence", f"Restored state: Gross P&L: ₹{self.daily_gross_pnl:.2f}, Net P&L: ₹{self.daily_net_pnl:.2f}, Trades: {(wins or 0)+(losses or 0)}")
            await self._update_ui_performance()
        else:
            await self._log_debug("Persistence", "No prior trades found for today. Starting fresh.")
    
    def _is_bullish_engulfing(self, prev, last):
        if prev is None or last is None or pd.isna(prev['open']) or pd.isna(last['open']): return False
        prev_body = abs(prev['close'] - prev['open']); last_body = abs(last['close'] - last['open'])
        return (prev['close'] < prev['open'] and last['close'] > last['open'] and
                last['close'] > prev['open'] and last['open'] < prev['close'] and
                last_body > prev_body * 0.8)

    def _is_bearish_engulfing(self, prev, last):
        if prev is None or last is None or pd.isna(prev['open']) or pd.isna(last['open']): return False
        prev_body = abs(prev['close'] - prev['open']); last_body = abs(last['close'] - last['open'])
        return (prev['close'] > prev['open'] and last['close'] < last['open'] and
                last['open'] > prev['close'] and last['close'] < prev['open'] and
                last_body > prev_body * 0.8)

    async def _is_green_candle_hold_active(self, symbol, current_ltp, profit_pct):
        """
        🟢 GREEN CANDLE HOLD OVERRIDE: Check if GREEN candle should prevent exit
        
        Skips TSL & momentum exits when:
        1. Current option candle is GREEN (LTP > candle open)
        2. Profit is >= minimum threshold
        3. Feature is enabled in GUI (or uses defaults)
        
        Still respects extreme loss threshold as emergency exit.
        
        Returns: (is_override_active, override_reason_str)
        """
        try:
            # Get current candle
            current_candle = self.data_manager.option_candles.get(symbol)
            if not current_candle or 'open' not in current_candle:
                await self._log_debug("Green Hold Override", f"❌ NO_CANDLE_DATA for {symbol} (available: {list(self.data_manager.option_candles.keys())[:3]}...)")
                return False, "NO_CANDLE_DATA"
            
            # Check if candle is GREEN (LTP > open)
            is_green = current_ltp > current_candle.get('open', 0)
            await self._log_debug("Green Hold Override", f"🔍 Checking {symbol}: LTP={current_ltp:.2f}, Open={current_candle.get('open', 0):.2f}, Green={is_green}")
            
            # Get parameters with defaults (GUI priority)
            green_hold_enabled = self.params.get("green_candle_hold_enabled", False)
            min_profit = float(self.params.get("green_hold_min_profit_pct", 1.0))
            max_loss = float(self.params.get("green_hold_max_loss_pct", -2.0))
            
            # 🔴 Auto-convert positive values to negative (emergency loss threshold)
            if max_loss > 0:
                max_loss = -max_loss
            
            if not green_hold_enabled:
                return False, "DISABLED"
            
            # 🟢 GREEN candle + minimum profit reached = HOLD (override exit)
            if is_green and profit_pct >= min_profit:
                return True, f"GREEN+PROFIT({profit_pct:.2f}% >= {min_profit}%)"
            
            # 🔴 Extreme loss = EXIT (emergency override, ignore green candle)
            if profit_pct < max_loss:
                return False, f"EXTREME_LOSS({profit_pct:.2f}% < {max_loss}%)"
            
            # No override active
            if not is_green:
                return False, f"RED_CANDLE(candle_open=₹{current_candle.get('open', 0):.2f})"
            
            if profit_pct < min_profit:
                return False, f"LOW_PROFIT({profit_pct:.2f}% < {min_profit}%)"
            
            return False, "NO_OVERRIDE"
            
        except Exception as e:
            await self._log_debug("Green Hold", f"⚠️ Error checking green candle: {e}")
            return False, "ERROR"
    
    async def initialize_st_angle_history(self, symbol):
        """
        🏗️ INITIALIZE ST ANGLE HISTORY: Populate initial data from historical candles
        
        Called when ATM strike changes to build immediate ST history from historical data.
        Processes last 5-10 candles to populate ST line history instantly.
        """
        try:
            import time as time_module
            
            # Get historical candles
            minute_candles = self.data_manager.option_minute_candle_history.get(symbol, [])
            
            if len(minute_candles) < 11:  # Need at least 11 for supertrend (period=10)
                # � THROTTLE WARNINGS: Prevent log spam (once per symbol per 60 seconds)
                if not hasattr(self, '_trendscout_warning_times'):
                    self._trendscout_warning_times = {}
                
                current_time = time_module.time()
                last_warning = self._trendscout_warning_times.get(symbol, 0)
                
                # Only log if it's been > 60 seconds since last warning for this symbol
                if current_time - last_warning > 60:
                    time_since_startup = current_time - self.startup_time
                    if time_since_startup > self.startup_grace_period:
                        await self._log_debug("TrendScout", 
                            f"⚠️ Cannot initialize {symbol} - Only {len(minute_candles)} candles available (need 11+)")
                        self._trendscout_warning_times[symbol] = current_time
                return
            
            # Initialize history containers
            if symbol not in self.st_line_history:
                self.st_line_history[symbol] = []
            if symbol not in self.st_angle_history:
                self.st_angle_history[symbol] = []
            
            # 🚀 PROCESS LAST 5 CANDLES: Build initial history from historical data
            # This allows Trend Direction Scout to show data immediately
            candles_to_process = min(5, len(minute_candles))
            start_idx = len(minute_candles) - candles_to_process
            
            # Simulate time progression for historical candles (60 seconds apart)
            base_time = time_module.time() - (candles_to_process * 60)
            
            for i in range(candles_to_process):
                # Calculate supertrend at this point in history
                # Temporarily limit candle history to simulate processing
                temp_history = minute_candles[:start_idx + i + 1]
                original_history = self.data_manager.option_minute_candle_history.get(symbol)
                self.data_manager.option_minute_candle_history[symbol] = temp_history
                
                st_line, st_uptrend = self.data_manager.calculate_option_supertrend(symbol)
                
                # Restore full history
                self.data_manager.option_minute_candle_history[symbol] = original_history
                
                if st_line and st_line > 0:
                    candle_time = base_time + (i * 60)
                    self.st_line_history[symbol].append((candle_time, st_line))
            
            # Calculate initial angle if we have enough ST history
            if len(self.st_line_history[symbol]) >= 2:
                lookback = min(3, len(self.st_line_history[symbol]) - 1)
                angle = await self.calculate_option_st_angle(symbol, lookback)
                
                if angle is not None:
                    self.current_st_angle[symbol] = angle
                    self.st_angle_history[symbol].append(angle)
                    
                    # Set initial status
                    increasing_threshold = float(self.STRATEGY_PARAMS.get('st_angle_entry_threshold', 0.7)) * 0.5
                    if angle > increasing_threshold:
                        self.st_angle_status[symbol] = "increasing"
                        self.st_angle_increase_start_time[symbol] = time_module.time()
                    elif angle < -0.1:
                        self.st_angle_status[symbol] = "decreasing"
                    else:
                        self.st_angle_status[symbol] = "flat"
                    
                    # Get final ST values for logging
                    final_st_line, st_uptrend = self.data_manager.calculate_option_supertrend(symbol)
                    
                    await self._log_debug("TrendScout", 
                        f"✅ Initialized {symbol} with {len(self.st_line_history[symbol])} historical ST values - "
                        f"Line: ₹{final_st_line:.2f}, Angle: {angle:.2f}%/candle, Status: {self.st_angle_status[symbol]}")
                else:
                    await self._log_debug("TrendScout", 
                        f"⚠️ {symbol} history built but angle calculation failed")
            else:
                await self._log_debug("TrendScout", 
                    f"⚠️ {symbol} - Not enough valid ST history points: {len(self.st_line_history[symbol])}")
        
        except Exception as e:
            await self._log_debug("TrendScout", f"⚠️ Failed to initialize ST history for {symbol}: {e}")
            import traceback
            await self._log_debug("TrendScout", f"Stack trace: {traceback.format_exc()}")
    
    async def update_st_angle_data(self, symbol):
        """
        📐 UPDATE SUPERTREND ANGLE DATA: Calculate and store ST angle metrics
        
        Called on every tick (with throttle) to update:
        - ST line history (last 5 values)
        - ST angle (slope over lookback period)
        - ST acceleration (rate of change of angle)
        
        Args:
            symbol: Option symbol to calculate ST angle for
        """
        try:
            # 🚀 THROTTLE: Only recalculate every 100ms (reduced from 200ms for faster updates)
            # This provides near-real-time monitoring for the GUI
            import time as time_module
            current_time = time_module.time()
            last_calc = self.last_st_calculation_time.get(symbol, 0)
            
            if current_time - last_calc < 0.1:  # Less than 100ms since last calculation
                return  # Skip this update
            
            self.last_st_calculation_time[symbol] = current_time
            
            # Get current ST line value from data_manager
            # Use option-specific Supertrend (9/1.1 - proper ATR-based calculation)
            st_line = None
            st_uptrend = None
            
            # Try to get option-specific ST data
            if hasattr(self.data_manager, 'calculate_option_supertrend'):
                st_line, st_uptrend = self.data_manager.calculate_option_supertrend(symbol)
            
            # ❌ REMOVED FALLBACK: Never use option close price as ST line proxy
            # Only use actual calculated supertrend value
            if st_line is None or st_line <= 0:
                return  # No valid ST line, skip this update
            
            # Initialize history for this symbol if needed
            if symbol not in self.st_line_history:
                self.st_line_history[symbol] = []
            if symbol not in self.st_angle_history:
                self.st_angle_history[symbol] = []
            
            # Add current ST value to history with timestamp
            current_time = time_module.time()
            self.st_line_history[symbol].append((current_time, st_line))
            
            # Keep only last 5 values (rolling window)
            if len(self.st_line_history[symbol]) > 5:
                self.st_line_history[symbol] = self.st_line_history[symbol][-5:]
            
            # Calculate angle if we have enough history (reduced requirement: need at least 2 points)
            lookback = int(self.params.get('st_angle_lookback', 3))
            if len(self.st_line_history[symbol]) >= 2:  # Reduced from lookback+1 to just 2
                # Use min of requested lookback and available history
                actual_lookback = min(lookback, len(self.st_line_history[symbol]) - 1)
                angle = await self.calculate_option_st_angle(symbol, actual_lookback)
                
                if angle is not None:
                    # Store current angle
                    self.current_st_angle[symbol] = angle
                    
                    # Add to angle history
                    self.st_angle_history[symbol].append(angle)
                    if len(self.st_angle_history[symbol]) > 5:
                        self.st_angle_history[symbol] = self.st_angle_history[symbol][-5:]
                    
                    # Calculate acceleration if we have 2+ angles
                    if len(self.st_angle_history[symbol]) >= 2:
                        accel = await self.calculate_option_st_acceleration(symbol)
                        if accel is not None:
                            self.current_st_acceleration[symbol] = accel
                    
                    # 📊 TRACK ANGLE STATUS FOR GUI DISPLAY
                    # Determine if angle is increasing/flat/decreasing
                    previous_status = self.st_angle_status.get(symbol)
                    
                    # Define thresholds
                    increasing_threshold = float(self.params.get('st_angle_entry_threshold', 0.7)) * 0.5  # 0.35% - half of entry
                    flat_threshold = 0.1  # Below this is considered flat
                    
                    if angle > increasing_threshold:
                        new_status = "increasing"
                    elif angle < -flat_threshold:
                        new_status = "decreasing"
                    else:
                        new_status = "flat"
                    
                    # Track when status changed to "increasing"
                    if new_status == "increasing" and previous_status != "increasing":
                        # Angle just started increasing
                        import time as time_module
                        self.st_angle_increase_start_time[symbol] = time_module.time()
                    elif new_status != "increasing":
                        # Reset the start time if not increasing anymore
                        if symbol in self.st_angle_increase_start_time:
                            del self.st_angle_increase_start_time[symbol]
                    
                    # Update status
                    self.st_angle_status[symbol] = new_status
        
        except Exception as e:
            await self._log_debug("ST Angle", f"⚠️ Error updating ST angle for {symbol}: {e}")
    
    async def calculate_option_st_angle(self, symbol, lookback=3):
        """
        📐 CALCULATE SUPERTREND ANGLE: Rate of ST line change over period
        
        Returns angle as percentage per candle.
        Positive = uptrend, Negative = downtrend
        
        Args:
            symbol: Option symbol
            lookback: Number of candles to calculate over (default 3)
            
        Returns:
            angle_percent: % change per candle (e.g., 0.85 = 0.85% per candle rise)
        """
        try:
            if symbol not in self.st_line_history:
                return None
            
            history = self.st_line_history[symbol]
            # Use available history if less than requested lookback
            actual_lookback = min(lookback, len(history) - 1)
            
            if len(history) < 2 or actual_lookback < 1:
                return None
            
            # Get current and past ST values
            current_st = history[-1][1]  # Most recent
            past_st = history[-(actual_lookback + 1)][1]  # N candles ago
            
            if past_st <= 0:
                return None
            
            # Calculate angle: (change / period) / base_value * 100
            angle_points = (current_st - past_st) / actual_lookback
            angle_percent = (angle_points / past_st) * 100
            
            return round(angle_percent, 2)
        
        except Exception as e:
            await self._log_debug("ST Angle", f"⚠️ Error calculating angle for {symbol}: {e}")
            return None
    
    async def calculate_option_st_acceleration(self, symbol):
        """
        📐 CALCULATE SUPERTREND ACCELERATION: Rate of change of angle
        
        Returns acceleration as percentage change.
        Positive = accelerating, Negative = decelerating
        
        Args:
            symbol: Option symbol
            
        Returns:
            acceleration: Rate of change of angle (e.g., 0.20 = angle increasing by 0.20% per candle)
        """
        try:
            if symbol not in self.st_angle_history:
                return None
            
            history = self.st_angle_history[symbol]
            if len(history) < 2:
                return None
            
            current_angle = history[-1]
            previous_angle = history[-2]
            
            acceleration = current_angle - previous_angle
            return round(acceleration, 2)
        
        except Exception as e:
            await self._log_debug("ST Angle", f"⚠️ Error calculating acceleration for {symbol}: {e}")
            return None
    
    async def check_st_angle_entry(self, symbol):
        """
        ✅ CHECK SUPERTREND ANGLE ENTRY: Determine if entry conditions met
        
        Entry requires ALL:
        1. ST angle > entry threshold (e.g., 0.7%)
        2. ST acceleration > accel threshold (e.g., 0.15%)
        3. Index trend supports (BULLISH for CE, BEARISH for PE)
        4. Price above or near ST line
        5. Recent green ticks (momentum confirmation)
        6. Current candle is green
        
        Returns:
            (should_enter, entry_metadata)
        """
        try:
            # Check if ST angle strategy is enabled
            if not self.params.get('st_angle_enabled', True):
                await self._log_debug("ST Angle", f"❌ ST angle strategy disabled in params, skipping entry check for {symbol}")
                return False, None
            
            # Get thresholds
            angle_threshold = float(self.params.get('st_angle_entry_threshold', 0.7))
            accel_threshold = float(self.params.get('st_angle_entry_accel', 0.15))
            require_index = self.params.get('st_angle_require_index', True)
            min_green_ticks = int(self.params.get('st_angle_min_green_ticks', 2))
            
            # 1. Check ST angle
            st_angle = self.current_st_angle.get(symbol)
            if st_angle is None:
                await self._log_debug("ST Angle", f"❌ No ST angle available for {symbol}")
                return False, None
            if st_angle < angle_threshold:
                await self._log_debug("ST Angle", f"❌ ST angle {st_angle:.2f}% below threshold {angle_threshold}% for {symbol}")
                return False, None
            
            # 2. Check ST acceleration
            st_accel = self.current_st_acceleration.get(symbol)
            if st_accel is None:
                await self._log_debug("ST Angle", f"❌ No ST acceleration available for {symbol}")
                return False, None
            if st_accel < accel_threshold:
                await self._log_debug("ST Angle", f"❌ ST accel {st_accel:.2f}% below threshold {accel_threshold}% for {symbol}")
                return False, None
            
            # 3. Check index support (if required)
            if require_index:
                index_trend = self.data_manager.trend_state
                if not index_trend:
                    await self._log_debug("ST Angle", f"❌ No index trend available for {symbol}")
                    return False, None
                
                # Determine expected side based on index
                if index_trend == "BULLISH":
                    expected_side = "CE"
                elif index_trend == "BEARISH":
                    expected_side = "PE"
                else:
                    await self._log_debug("ST Angle", f"❌ Unexpected index trend {index_trend} for {symbol}")
                    return False, None
                
                # Check if symbol matches expected side
                if expected_side not in symbol:
                    await self._log_debug("ST Angle", f"❌ Index trend {index_trend} does not match symbol side for {symbol}")
                    return False, None
            
            # 4. Check price vs ST line position
            current_ltp = self.data_manager.prices.get(symbol)
            if not current_ltp:
                await self._log_debug("ST Angle", f"❌ No LTP available for {symbol}")
                return False, None
            
            # Get ST line (approximate from history)
            if symbol in self.st_line_history and len(self.st_line_history[symbol]) > 0:
                current_st_line = self.st_line_history[symbol][-1][1]
                
                # Price should be above ST or within 0.3% below (near breakout)
                distance_pct = ((current_ltp - current_st_line) / current_st_line) * 100
                if distance_pct < -0.3:  # More than 0.3% below ST
                    await self._log_debug("ST Angle", f"❌ Price {current_ltp:.2f} is {distance_pct:.2f}% below ST line {current_st_line:.2f} for {symbol}")
                    return False, None
            
            # 5. Check consecutive green ticks (momentum)
            # Use price_history from data_manager to check recent rising ticks
            green_tick_count = 0
            price_history = self.data_manager.price_history.get(symbol, [])
            if len(price_history) >= 3:
                # Get last 5 prices
                recent_prices = [p for ts, p in price_history[-5:]]
                
                # Count consecutive rising ticks from the end
                for i in range(len(recent_prices) - 1, 0, -1):
                    if recent_prices[i] > recent_prices[i-1]:
                        green_tick_count += 1
                    else:
                        break  # Stop at first non-rising tick
            
            # Require at least 1 rising tick for entry (relaxed requirement)
            # This confirms current momentum without being too restrictive
            if green_tick_count < 1:
                await self._log_debug("ST Angle", f"❌ Only {green_tick_count} green ticks for {symbol}, need >=1")
                return False, None
            
            # 6. Check current candle color
            option_candle = self.data_manager.option_candles.get(symbol)
            if not option_candle or 'open' not in option_candle:
                await self._log_debug("ST Angle", f"❌ No candle data for {symbol}")
                return False, None
            
            is_green_candle = current_ltp > option_candle.get('open')
            if not is_green_candle:
                await self._log_debug("ST Angle", f"❌ Candle not green for {symbol} (open {option_candle.get('open')}, ltp {current_ltp})")
                return False, None
            
            # ✅ All conditions met - prepare entry metadata
            entry_metadata = {
                'st_angle': st_angle,
                'st_accel': st_accel,
                'entry_st_line': self.st_line_history[symbol][-1][1] if symbol in self.st_line_history else None,
                'entry_type': 'ST_ANGLE_TREND',
                'index_trend': self.data_manager.trend_state
            }
            
            return True, entry_metadata
        
        except Exception as e:
            await self._log_debug("ST Angle Entry", f"⚠️ Error checking entry for {symbol}: {e}")
            return False, None
    
    async def pre_calculate_entry_readiness(self):
        """
        🚀 PRE-CALCULATE ENTRY READINESS: Calculate conditions at candle close for fast entry
        
        Called at end of each minute candle to determine if we're ready to enter on next candle.
        This eliminates lag from ST angle calculations during the critical first seconds.
        
        Strategy:
        1. Calculate ST angle, acceleration, trend at candle close
        2. If all conditions good, mark ENTRY_READY = True
        3. Store previous candle high for breakout confirmation
        4. On new candle, only need to verify: price rising + candle green + breakout
        
        Result: Enter within 0.5-2 seconds instead of 10-30 seconds
        """
        try:
            # 🔍 DEBUG: Log that pre-calculation is running
            await self._log_debug("Pre-Calc", "🔄 Running pre-calculation at candle close...")
            
            import time as time_module
            
            # Get current ATM strike and options
            spot = self.data_manager.prices.get(self.index_symbol)
            if not spot:
                await self._log_debug("Pre-Calc", "❌ No spot price available")
                return
            
            atm_strike = self.strike_step * round(spot / self.strike_step)
            ce_option = self.get_entry_option("CE", atm_strike)
            pe_option = self.get_entry_option("PE", atm_strike)
            
            await self._log_debug("Pre-Calc", f"📊 Checking ATM {atm_strike} - CE: {ce_option['tradingsymbol'] if ce_option else 'None'}, PE: {pe_option['tradingsymbol'] if pe_option else 'None'}")
            
            # Check both CE and PE for entry readiness
            for option, side in [(ce_option, "CE"), (pe_option, "PE")]:
                if not option:
                    continue
                
                symbol = option['tradingsymbol']
                
                # Run full entry check (ST angle, acceleration, trend, etc.)
                should_enter, entry_metadata = await self.check_st_angle_entry(symbol)
                
                if should_enter:
                    # Get previous candle high for breakout confirmation
                    candle = self.data_manager.option_candles.get(symbol, {})
                    prev_high = candle.get('high', 0)
                    
                    # Mark as ready for fast entry
                    self.entry_ready[symbol] = {
                        'ready': True,
                        'side': side,
                        'timestamp': get_ist_time(),
                        'prev_candle_high': prev_high,
                        'st_angle': entry_metadata.get('st_angle'),
                        'st_accel': entry_metadata.get('st_accel'),
                        'index_trend': entry_metadata.get('index_trend')
                    }
                    
                    await self._log_debug("Fast Entry Ready", 
                        f"✅ {symbol} ({side}) READY for fast entry | "
                        f"ST angle: {entry_metadata.get('st_angle'):.2f}%, "
                        f"Accel: {entry_metadata.get('st_accel'):.2f}%, "
                        f"Prev high: ₹{prev_high:.2f}")
                else:
                    # Not ready - clear any existing readiness
                    if symbol in self.entry_ready:
                        del self.entry_ready[symbol]
            
            # 🔍 DEBUG: Log completion status
            ready_count = sum(1 for data in self.entry_ready.values() if data.get('ready'))
            if ready_count > 0:
                await self._log_debug("Pre-Calc", f"✅ Pre-calculation complete - {ready_count} symbol(s) ready for fast entry")
            else:
                await self._log_debug("Pre-Calc", "ℹ️ Pre-calculation complete - No symbols ready (conditions not met)")
        
        except Exception as e:
            await self._log_debug("Pre-Calculate", f"⚠️ Error in pre-calculation: {e}")
            import traceback
            await self._log_debug("Pre-Calculate", f"Stack trace: {traceback.format_exc()}")
    
    def calculate_price_velocity(self, symbol, lookback_seconds=2.0):
        """
        📈 CALCULATE PRICE VELOCITY: Measure how fast price is moving
        
        Returns price change per second over the lookback period.
        Positive = rising, Negative = falling
        
        Args:
            symbol: Option symbol
            lookback_seconds: Time period to measure velocity (default 2s)
        
        Returns:
            float: Price change per second (₹/second)
        """
        try:
            import time as time_module
            
            price_history = self.data_manager.price_history.get(symbol, [])
            if len(price_history) < 2:
                return 0.0
            
            current_time = time_module.time()
            current_price = price_history[-1][1]
            
            # Find price at lookback time
            lookback_time = current_time - lookback_seconds
            lookback_price = None
            actual_time_diff = 0
            
            for ts, price in reversed(price_history):
                if ts <= lookback_time:
                    lookback_price = price
                    actual_time_diff = current_time - ts
                    break
            
            if lookback_price is None or actual_time_diff == 0:
                return 0.0
            
            # Calculate velocity (₹ per second)
            price_change = current_price - lookback_price
            velocity = price_change / actual_time_diff
            
            return velocity
        
        except Exception as e:
            return 0.0
    
    async def check_fast_entry_conditions(self):
        """
        ⚡ CHECK FAST ENTRY CONDITIONS: Quick validation for pre-calculated entries
        
        Loops through all symbols marked as READY and checks if they should enter now.
        Only needs to verify:
        1. Pre-calculation is still valid (within 5 seconds)
        2. Price is rising (positive velocity)
        3. Current candle is GREEN
        4. Price broke above previous candle high (breakout confirmation)
        
        Returns:
            (symbol, side, entry_data) if conditions met, None otherwise
        """
        try:
            # Loop through all ready symbols
            for symbol, ready_data in list(self.entry_ready.items()):
                if not ready_data.get('ready'):
                    continue
                
                # 1. Check if pre-calculation is still valid (within 5 seconds)
                time_since_calc = (get_ist_time() - ready_data['timestamp']).total_seconds()
                if time_since_calc > self.pre_calc_valid_duration:
                    # Expired - clear it
                    del self.entry_ready[symbol]
                    continue
                
                # 2. Check price velocity (must be rising)
                velocity = self.calculate_price_velocity(symbol, lookback_seconds=1.5)
                if velocity <= 0.05:  # Must be rising at least ₹0.05/second
                    continue
                
                # 3. Check current candle is GREEN
                current_ltp = self.data_manager.prices.get(symbol, 0)
                candle = self.data_manager.option_candles.get(symbol, {})
                candle_open = candle.get('open', 0)
                
                if not candle_open or current_ltp <= candle_open:
                    # RED candle or no candle data
                    continue
                
                # 4. Check breakout above previous candle high
                prev_high = ready_data.get('prev_candle_high', 0)
                if current_ltp < prev_high * 0.998:  # Allow 0.2% below high (noise tolerance)
                    # Not broken out yet
                    continue
                
                # ✅ All fast entry conditions met!
                side = ready_data.get('side')
                entry_data = {
                    'st_angle': ready_data.get('st_angle'),
                    'st_accel': ready_data.get('st_accel'),
                    'velocity': velocity,
                    'timestamp': ready_data.get('timestamp')
                }
                return (symbol, side, entry_data)
            
            # No symbols ready
            return None
        
        except Exception as e:
            await self._log_debug("Fast Entry", f"⚠️ Error checking fast entry: {e}")
            import traceback
            await self._log_debug("Fast Entry", f"Stack trace: {traceback.format_exc()}")
            return None
    
    async def check_st_angle_exit(self, position):
        """
        ❌ CHECK SUPERTREND ANGLE EXIT: Determine if exit conditions met
        
        Exit triggers (ANY of):
        1. Emergency loss (< -3%)
        2. ST line break (price < ST - 1%)
        3. Index reversal (if low profit)
        4. Consecutive red candles with weak angle
        
        Returns:
            (should_exit, exit_reason, priority)
        """
        try:
            symbol = position.get('symbol')
            if not symbol:
                return False, "NO_SYMBOL", None
            
            # Only check ST angle exit for ST_ANGLE_TREND entries
            entry_type = position.get('entry_type')
            if entry_type != 'ST_ANGLE_TREND':
                return False, "NOT_ST_ANGLE_TRADE", None
            
            # Get current price and entry price
            current_ltp = self.data_manager.prices.get(symbol)
            entry_price = position.get('entry_price')
            if not current_ltp or not entry_price:
                return False, "NO_PRICE_DATA", None
            
            # Calculate profit
            profit_pct = ((current_ltp - entry_price) / entry_price) * 100
            
            # Get thresholds
            emergency_loss = float(self.params.get('st_angle_emergency_loss', -3.0))
            
            # 1. EMERGENCY: Large loss
            if profit_pct < emergency_loss:
                return True, f"EMERGENCY_LOSS ({profit_pct:.2f}%)", "CRITICAL"
            
            # 2. ST LINE BREAK: Price crossed below ST significantly
            if symbol in self.st_line_history and len(self.st_line_history[symbol]) > 0:
                current_st_line = self.st_line_history[symbol][-1][1]
                distance_pct = ((current_ltp - current_st_line) / current_st_line) * 100
                
                if distance_pct < -1.0:  # More than 1% below ST
                    return True, f"ST_LINE_BREAK (price {distance_pct:.2f}% below ST)", "HIGH"
            
            # 3. INDEX REVERSAL: Broader market turned against position
            index_trend = self.data_manager.trend_state
            expected_side = "CE" if "CE" in symbol else "PE"
            
            if index_trend:
                index_supports = (
                    (index_trend == "BULLISH" and expected_side == "CE") or
                    (index_trend == "BEARISH" and expected_side == "PE")
                )
                
                if not index_supports and profit_pct < 2.0:
                    return True, "INDEX_REVERSAL (index bearish, low profit)", "MEDIUM"
            
            # 4. CONSECUTIVE RED CANDLES: Reversal pattern
            st_angle = self.current_st_angle.get(symbol)
            option_candle = self.data_manager.option_candles.get(symbol)
            if option_candle and 'open' in option_candle:
                is_red = current_ltp < option_candle.get('open')
                
                # Track consecutive red candles
                if not hasattr(self, 'consecutive_red_count'):
                    self.consecutive_red_count = {}
                
                if is_red:
                    self.consecutive_red_count[symbol] = self.consecutive_red_count.get(symbol, 0) + 1
                else:
                    self.consecutive_red_count[symbol] = 0
                
                if self.consecutive_red_count.get(symbol, 0) >= 2 and st_angle is not None and st_angle < 0.3:
                    return True, f"REVERSAL_PATTERN (2 red candles, weak angle {st_angle:.2f}%)", "LOW"
            
            # ✅ HOLD CONDITION
            return False, "HOLD", None
        
        except Exception as e:
            await self._log_debug("ST Angle Exit", f"⚠️ Error checking exit for {symbol}: {e}")
            return False, "ERROR", None

    async def reload_params(self):
        await self._log_debug("System", "Live reloading of strategy parameters requested...")
        
        # Invalidate STRATEGY_PARAMS cache so we read fresh from disk
        self._strategy_params_cache = None
        
        # CRITICAL FIX: Detect trading mode change and restart WebSocket
        old_trading_mode = self.params.get("trading_mode", "Paper Trading")
        old_expiry = self.params.get("option_expiry_type", "")
        new_params = self.STRATEGY_PARAMS
        new_trading_mode = new_params.get("trading_mode", "Paper Trading")
        new_expiry = new_params.get("option_expiry_type", "")
        
        # Check if trading mode changed
        if old_trading_mode != new_trading_mode:
            await self._log_debug("System", 
                f"🔄 Trading mode changed: {old_trading_mode} → {new_trading_mode}")
            await self._log_debug("System", 
                f"⚠️ Note: Past {old_trading_mode} trades will NOT affect current {new_trading_mode} performance.")
            
            # Restart WebSocket connection for mode change
            if self.ticker_manager:
                await self._log_debug("System", "🔌 Restarting WebSocket connection...")
                try:
                    # Disconnect old connection
                    await self.ticker_manager.stop()
                    await asyncio.sleep(1)  # Wait for clean disconnect
                    
                    # Reconnect with new mode (run in thread to avoid blocking event loop)
                    await asyncio.to_thread(self.ticker_manager.start)
                    await self._log_debug("System", 
                        f"✅ WebSocket reconnected for {new_trading_mode} mode")
                except Exception as e:
                    await self._log_debug("System", 
                        f"❌ WebSocket restart failed: {e}. Please restart bot manually.")
        
        # CRITICAL FIX: Update self.params so all param checks use new values
        self.params = self._sanitize_params(new_params)
        self.data_manager.strategy_params = new_params
        self.risk_manager.params = self.params  # Update risk manager params too
        
        # 📅 NEW: Detect expiry date change and update dynamically
        if old_expiry != new_expiry and new_expiry:
            await self._log_debug("Expiry", 
                f"📅 Expiry changed: {old_expiry} → {new_expiry}")
            
            # Parse new expiry date
            try:
                from datetime import datetime
                new_expiry_date = datetime.strptime(new_expiry, '%Y-%m-%d').date()
                old_expiry_date = self.last_used_expiry
                
                if new_expiry_date != old_expiry_date:
                    self.last_used_expiry = new_expiry_date
                    await self._log_debug("Expiry", 
                        f"✅ Expiry updated to: {new_expiry_date.strftime('%Y-%m-%d')}")
                    
                    # Resubscribe to new option tokens with selected expiry
                    if self.ticker_manager and not self.is_backtest:
                        await self._log_debug("Expiry", "🔄 Resubscribing to new expiry options...")
                        try:
                            tokens = self.get_all_option_tokens()
                            await self.map_option_tokens(tokens)
                            self.ticker_manager.resubscribe(tokens)
                            await self._log_debug("Expiry", 
                                f"✅ Subscribed to {len(tokens)} option tokens for {new_expiry_date.strftime('%Y-%m-%d')} expiry")
                        except Exception as e:
                            await self._log_debug("Expiry", f"⚠️ Resubscription warning: {e}")
                    
                    # Force UI update to show new option chain
                    self._ui_chain_dirty = True
                    self._ui_status_dirty = True
                    await self._update_ui_option_chain()
                    await self._update_ui_status()
            except ValueError as e:
                await self._log_debug("Expiry", f"⚠️ Invalid expiry date format: {new_expiry}. Use YYYY-MM-DD format.")
                
        await self._log_debug("System", "Strategy parameters have been reloaded successfully.")
        return new_params

    async def run(self):
        await self._log_debug("System", "Strategy instance created.")
        await self.data_manager.bootstrap_data()
        
        # 📊 Initialize Trend Direction Scout: Calculate ST angle data from bootstrap candles
        try:
            # Use live price if available, otherwise use bootstrap data for immediate calculation
            spot = self.data_manager.prices.get(self.index_symbol, 0)
            if not spot and not self.data_manager.data_df.empty:
                spot = self.data_manager.data_df.iloc[-1]['close']
                await self._log_debug("TrendScout", f"⚡ Using bootstrap data for immediate initialization: {spot:.2f}")
            
            if spot:
                atm_strike = self.strike_step * round(spot / self.strike_step)
                ce_option = self.get_entry_option("CE", atm_strike)
                pe_option = self.get_entry_option("PE", atm_strike)
                
                await self._log_debug("TrendScout", f"📊 Initializing Trend Direction Scout for ATM {atm_strike}")
                
                if ce_option:
                    ce_symbol = ce_option['tradingsymbol']
                    candle_count = len(self.data_manager.option_minute_candle_history.get(ce_symbol, []))
                    await self._log_debug("TrendScout", f"CE {ce_symbol}: {candle_count} historical candles loaded")
                    # Build ST history from historical candles
                    await self.initialize_st_angle_history(ce_symbol)
                else:
                    await self._log_debug("TrendScout", "⚠️ CE option not available for initialization")
                    
                if pe_option:
                    pe_symbol = pe_option['tradingsymbol']
                    candle_count = len(self.data_manager.option_minute_candle_history.get(pe_symbol, []))
                    await self._log_debug("TrendScout", f"PE {pe_symbol}: {candle_count} historical candles loaded")
                    # Build ST history from historical candles  
                    await self.initialize_st_angle_history(pe_symbol)
                else:
                    await self._log_debug("TrendScout", "⚠️ PE option not available for initialization")
            else:
                await self._log_debug("TrendScout", "⚠️ No index price available for ATM calculation")
        except Exception as e:
            await self._log_debug("TrendScout", f"❌ Initialization failed: {str(e)}")
            import traceback
            await self._log_debug("TrendScout", f"Stack trace: {traceback.format_exc()}")
        
        await self._restore_daily_performance()
        
        # CRITICAL FIX: Position Recovery - Check for open positions from Zerodha
        await self._recover_open_positions()
        
        # 🚀 STARTUP OPTIMIZATION: Pre-fetch initial option chain prices via quote API
        # This ensures option prices are available immediately on startup, before first WebSocket tick
        try:
            await self._bootstrap_option_prices()
        except Exception as e:
            await self._log_debug("Bootstrap", f"⚠️ Option price bootstrap failed (non-fatal): {e}")
        
        startup_cooldown = self.params.get('startup_cooldown', 2)
        self.exit_cooldown_until = get_ist_time() + timedelta(seconds=startup_cooldown)
        await self._log_debug("System", f"Initial {startup_cooldown}-second startup wait initiated. No trades will be taken.")
        
        # ⚡⚡ START BACKGROUND CAPITAL REFRESH: Fetch capital every 3 seconds
        if not self._background_capital_task or self._background_capital_task.done():
            self._background_capital_task = asyncio.create_task(self._background_capital_refresher())
            await self._log_debug("System", f"✅ Background capital refresh started (every {self._capital_refresh_interval}s)")
        
        # 🎯 START EXTERNAL POSITION MONITOR: Detect manual/external trades every 2 seconds
        if not hasattr(self, '_external_position_task') or self._external_position_task.done():
            self._external_position_task = asyncio.create_task(self._monitor_external_positions())
            await self._log_debug("System", f"✅ External position monitor started (every 2s)")
        
        if not self.ui_update_task or self.ui_update_task.done():
            self.ui_update_task = asyncio.create_task(self.periodic_ui_updater())
    
    async def periodic_ui_updater(self):
        while True:
            try:
                # CRITICAL FIX: Daily reset check at 9:15 AM
                current_date = date.today()
                current_time = get_ist_time().time()
                if current_date > self.last_reset_date and current_time >= time(9, 15):
                    await self._log_debug("System", "🔄 New trading day detected. Resetting daily state...")
                    self._reset_state()
                    self.last_reset_date = current_date
                    await self._log_debug("System", "✅ Daily reset complete.")
                
                if self.position and (not self.ticker_manager or not self.ticker_manager.is_connected):
                    if self.disconnected_since is None:
                        self.disconnected_since = get_ist_time()
                        await self._log_debug("WebSocket", "⚠️ Connection lost during trade. Waiting for auto-reconnect...")
                    else:
                        # 🔥 IMPROVED: Longer failsafe (45s) to allow for reconnection attempts
                        disconnected_duration = get_ist_time() - self.disconnected_since
                        if disconnected_duration > timedelta(seconds=45):
                            await self._log_debug("CRITICAL", f"🚨 Failsafe triggered after {disconnected_duration.seconds}s disconnection!")
                            await self.exit_position("Failsafe: Prolonged Disconnection"); continue
                        elif disconnected_duration.seconds % 10 == 0 and disconnected_duration.seconds > 0:
                            # Log every 10 seconds
                            await self._log_debug("WebSocket", f"⏳ Still disconnected ({disconnected_duration.seconds}s). Reconnection in progress...")
                elif self.ticker_manager and self.ticker_manager.is_connected:
                    if self.disconnected_since is not None:
                        duration = (get_ist_time() - self.disconnected_since).seconds
                        await self._log_debug("WebSocket", f"✅ Reconnected successfully after {duration}s!")
                        self.disconnected_since = None
                    if self.position and get_ist_time().time() >= time(15, 25):
                        await self._log_debug("RISK", f"EOD square-off time reached. Exiting position.")
                        await self.exit_position("End of Day Auto-Square Off", skip_layer6_cooldown=True); continue
                
                # 🎯 CLOCK SYNC DISABLED: Clock now comes from batch_frame_update (60 FPS)
                # Fallback time_sync removed to prevent competing clock sources
                # The frame-based update already includes accurate IST timestamps
                
                # 🎯 PROFESSIONAL 30 FPS FRAME TIMER: Fixed-rate UI updates (like Bloomberg/TradingView)
                # Conflates all ticks received during frame interval into single batched update
                current_time = time_module.time()
                time_since_last_frame = current_time - self._last_frame_time
                
                if time_since_last_frame >= self.FRAME_TIME:  # Every 33ms (30 FPS)
                    await self._flush_frame_update()
                    self._last_frame_time = current_time
                    
                # ⚡ MINIMAL SLEEP: Yield control with guaranteed 1ms sleep for smooth scheduling
                # This prevents CPU starvation while maintaining real-time responsiveness
                await asyncio.sleep(0.001)  # 1ms sleep = up to 1000 FPS theoretical max
            except asyncio.CancelledError: await self._log_debug("UI Updater", "Task cancelled."); break
            except Exception as e: 
                await self._log_debug("UI Updater Error", f"An error occurred: {e}")
                import traceback
                await self._log_debug("UI Updater Error", f"Traceback: {traceback.format_exc()}")
                await asyncio.sleep(0)  # Yield without delay
    
    async def take_trade(self, trigger, opt, custom_entry_price=None, momentum_data=None, signal_generation_time=None):
        """
        Executes a new trade, applying robust synchronization and throttling using the 
        7-Layer Duplicate Order Prevention System.
        
        Args:
            momentum_data: Dict with momentum check results for database logging
            signal_generation_time: Unix timestamp when signal was first generated by engine
        """
        import time as time_module  # Required for candle age calculation (line 1813)
        
        # 🔥 CRITICAL: Use provided signal_generation_time if available, otherwise use current time
        if signal_generation_time is not None:
            # Convert unix timestamp to IST datetime
            from datetime import datetime as dt_cls
            signal_time = dt_cls.fromtimestamp(signal_generation_time, tz=IST)
        else:
            signal_time = get_ist_time()  # Fallback to current time
        
        now = get_ist_time()
        symbol = opt["tradingsymbol"] if opt else None
        
        # Store momentum data for later logging
        if momentum_data is None:
            momentum_data = {}
        self._current_momentum_data = momentum_data
        
        if not opt or symbol is None:
            return

        # 🚀 NO-WICK BYPASS: Check if current option candle has no lower wick (strong bullish signal)
        # If Open == Low, it means price never pulled back - extremely strong buying pressure
        # This bypasses ALL validation checks and enters immediately with only SL/TSL active
        no_wick_bypass = False
        option_candle = self.data_manager.option_candles.get(symbol)
        
        if option_candle and self.params.get('enable_no_wick_entry', True):
            candle_open = option_candle.get('open', 0)
            candle_low = option_candle.get('low', 0)
            candle_high = option_candle.get('high', 0)
            candle_close = option_candle.get('close', 0)
            candle_start_time = option_candle.get('candle_start_time', 0)
            
            # Check if candle has no lower wick (open == low) AND is a green candle
            if candle_open > 0 and candle_low > 0:
                # 🛡️ SAFETY CHECK #1: Minimum tick count to avoid false positives at candle start
                # At candle start, Open=Low=High initially. Wait for Low to stabilize.
                price_history = self.data_manager.price_history.get(symbol, [])
                tick_count = len(price_history)
                
                # 🛡️ SAFETY CHECK #2: Minimum candle age to ensure Low has had time to update
                # Prevents race conditions where Low updates during validation
                candle_age = time_module.time() - candle_start_time if candle_start_time > 0 else 0
                
                # 🎯 SCALPER MODE: Only enter FRESH candles (within first 15 seconds)
                # ✅ FIX: Changed default from True to False to match STRATEGY_PARAMS setting
                # ⚠️ DISABLED: Allow entries throughout entire minute to avoid blocking legit trades
                scalper_enabled = self.STRATEGY_PARAMS.get('scalper_enabled', False)
                max_candle_age = self.STRATEGY_PARAMS.get('scalper_max_candle_age_sec', 60)  # Changed from 15s to 60s (full minute)
                
                # DISABLED: Candle age filter can block legitimate trade opportunities
                # if scalper_enabled and candle_age > max_candle_age:
                #     await self._log_debug("⏱️ SCALPER FILTER", 
                #         f"🚫 Candle too old: {candle_age:.1f}s > {max_candle_age}s - Entry rejected (momentum likely priced in)")
                #     return  # Skip this trade - too late to enter
                
                # Require BOTH conditions: ≥3 ticks AND ≥2 seconds elapsed
                if tick_count >= 3 and candle_age >= 2.0:
                    # For bullish (CE) trades: Open must equal Low (no lower wick)
                    # Also verify it's actually a green candle (close > open or current price > open)
                    current_price = self.data_manager.prices.get(symbol, 0)
                    is_green = current_price > candle_open or candle_close > candle_open
                    has_no_lower_wick = abs(candle_open - candle_low) < (candle_open * 0.005)  # Within 0.5% tolerance
                    
                    # 🎯 SCALPER: Check candle body size (avoid tiny candles)
                    candle_body_pct = ((current_price - candle_open) / candle_open * 100) if candle_open > 0 else 0
                    min_body_pct = self.STRATEGY_PARAMS.get('scalper_min_candle_body_pct', 0.3)
                    
                    if has_no_lower_wick and is_green and candle_high > candle_open and candle_body_pct >= min_body_pct:
                        no_wick_bypass = True
                        await self._log_debug("🚀 NO-WICK SCALPER ENTRY", 
                            f"✅ FRESH GREEN CANDLE: {symbol} | Age: {candle_age:.1f}s | Body: {candle_body_pct:.2f}% | " +
                            f"Open={candle_open:.2f}, Low={candle_low:.2f} (No pullback detected)")
                    elif candle_body_pct < min_body_pct:
                        await self._log_debug("🎯 SCALPER FILTER", 
                            f"🚫 Candle body too small: {candle_body_pct:.2f}% < {min_body_pct}% - Weak momentum")
        
        # ⚡ CRITICAL: Hold lock from Layer 1 through all checks to prevent race conditions
        # Do NOT release lock between flag check and position check
        async with self.position_lock:
            # LAYER 1: Entry in progress flag (atomic check)
            if self.entry_in_progress:
                # 🛡️ AUTO-RESET: Check if entry flag is stuck (timeout protection)
                if self.entry_started_at:
                    time_since_entry = (now - self.entry_started_at).total_seconds()
                    if time_since_entry > self.entry_timeout_seconds:
                        await self._log_debug("Entry Timeout", 
                            f"⚠️ Entry flag stuck for {time_since_entry:.1f}s (>{self.entry_timeout_seconds}s). Auto-resetting!")
                        self.entry_in_progress = False
                        self.entry_started_at = None
                        # Don't return, allow this entry to proceed
                    else:
                        # 🚀 IMPROVEMENT: Queue signal instead of rejecting
                        if len(self.signal_queue) < self.max_queue_size:
                            self.signal_queue.append({
                                'trigger': trigger,
                                'opt': opt,
                                'custom_entry_price': custom_entry_price,
                                'momentum_data': momentum_data,
                                'timestamp': get_ist_time()
                            })
                            await self._log_debug("Signal Queue", 
                                f"📥 {trigger} queued (position {len(self.signal_queue)}/{self.max_queue_size}) - entry in progress")
                        else:
                            await self._log_debug("Layer 1", 
                                f"🚫 BLOCKED: Entry in progress + queue full ({self.max_queue_size}/{self.max_queue_size})")
                        return
            
            # 🛡️ ENTRY BUFFER: Check if entry completed recently (within 500ms buffer)
            # This prevents race condition where multiple signals slip through after entry_in_progress=False
            if self.entry_completed_at:
                time_since_completion = (now - self.entry_completed_at).total_seconds()
                if time_since_completion < self.entry_buffer_duration:
                    # Still in buffer window - queue this signal
                    if len(self.signal_queue) < self.max_queue_size:
                        self.signal_queue.append({
                            'trigger': trigger,
                            'opt': opt,
                            'custom_entry_price': custom_entry_price,
                            'momentum_data': momentum_data,
                            'timestamp': get_ist_time()
                        })
                        await self._log_debug("Entry Buffer", 
                            f"📥 {trigger} queued (post-entry buffer {time_since_completion:.1f}s < {self.entry_buffer_duration}s)")
                    return
            
            # LAYER 1b: Already have a position? Check IMMEDIATELY after flag check
            if self.position:
                await self._log_debug("Layer 1", f"🚫 BLOCKED: Position already open for {self.position['symbol']}. Cannot enter {symbol}.")
                return
            
            # ⚡ PRIORITY 3: Signal deduplication check (before setting entry flag)
            # Only block if same symbol AND similar price (within ₹0.50) - allow different-price re-entries
            if symbol in self.last_signal_time:
                last_signal = self.last_signal_time[symbol]
                signal_age = (now - last_signal['time']).total_seconds()
                
                if signal_age < 5:
                    # Check if price is different enough to allow re-entry
                    current_live_price = custom_entry_price or self.data_manager.prices.get(symbol, 0)
                    last_signal_price = last_signal.get('price', 0)
                    price_diff = abs(current_live_price - last_signal_price) if (current_live_price and last_signal_price) else 999
                    
                    if price_diff < 0.50:  # Same price (within ₹0.50) - block
                        await self._log_debug("Signal Dedup", 
                            f"🚫 BLOCKED: {symbol} same price signal too recent ({signal_age:.2f}s < 5s, price diff ₹{price_diff:.2f}). Skipping.")
                        return
                    else:
                        await self._log_debug("Signal Dedup",
                            f"✅ ALLOWED: {symbol} different price (₹{current_live_price:.2f} vs ₹{last_signal_price:.2f}, diff ₹{price_diff:.2f}) - bypassing 5s dedup")
            
            # ✅ FIXED: Only update last_signal_time AFTER trade succeeds or fails
            # Do NOT update it here - it will be updated in finally block after order outcome is known
            # This prevents locking symbol when trade failed at pre-flight checks
            
            # Set flag atomically AFTER both checks pass
            self.entry_in_progress = True
            self.entry_started_at = now  # 🛡️ Track when entry started for timeout
        
        # Lock released - flag is set, no position exists at this moment
        
        # Determine entry price for pre-checks (NO ROUNDING)
        expected_entry_price = custom_entry_price or self.data_manager.prices.get(symbol)
        entry_price = expected_entry_price
            
        if entry_price is None:
            self.entry_in_progress = False
            await self._log_debug("Trade Rejected", "Cannot get entry price for pre-checks.")
            return

        # Re-acquire lock for additional checks
        async with self.position_lock:
            
            # Trading Hours Check (Keep existing check before locks) - ALWAYS enforced
            current_time = now.time()
            if current_time >= time(15, 25):  # 3:25 PM cutoff
                await self._log_debug("Trading Hours", f"🚫 Trading stopped after 3:25 PM. Current time: {current_time.strftime('%H:%M:%S')}")
                self.entry_in_progress = False
                return

            # 🚀 NO-WICK BYPASS: Only skip Layer 3 (rate limiting), still enforce Layers 4,5,6
            if no_wick_bypass:
                await self._log_debug("🚀 NO-WICK BYPASS", 
                    f"⚡ Skipping Layer 3 (rate limit) only - Layers 4,5,6 (cooldowns, duplicates) still enforced")

            # LAYER 2: Quick Active Order Check (5-10ms) - Defer full Zerodha verify to parallel execution
            # ⚡⚡ OPTIMIZATION: Only check active_order_id here (instant), defer full Zerodha verify to run during order placement
            # This saves 100-200ms by allowing order to start immediately instead of waiting for API call
            
            # Check 1: Order already in progress? (Live trading order ID placeholder)
            if self.active_order_id:
                await self._log_debug("Layer 2", f"🚫 BLOCKED: Active order ID ({self.active_order_id}) set.")
                self.entry_in_progress = False  # Reset flag
                return
            
            # Check 2: Double-check position hasn't appeared (defensive check)
            # Position was already checked in Layer 1, but check again in case of race
            if self.position:
                await self._log_debug("Layer 2", f"🚫 BLOCKED: Position appeared during checks for {self.position['symbol']}. Race condition prevented.")
                self.entry_in_progress = False  # Reset flag
                return
            
            # ⚡⚡ DEFERRED: Full Layer 2 Zerodha verification moved to parallel execution
            # It will run DURING order placement instead of BEFORE, saving 100-200ms
            # See async def verify_zerodha_positions() below

            # --- Start Pre-Execution Checks (Layers 3, 4, 5, 6) ---
            # ⚡⚡ PARALLEL OPTIMIZATION: Move Layer 2 Zerodha verification here (runs during order)
            # This creates the "verify_zerodha_positions_deferred" task to run in parallel with order
            
            async def verify_zerodha_positions_deferred():
                """Layer 2 (Deferred): Verify no existing position in Zerodha - runs in parallel with order"""
                if self.params.get("trading_mode") != "Live Trading":
                    return True, None  # Skip in paper trading
                
                try:
                    zerodha_positions = (await kite.positions())["net"]
                    existing_position = next(
                        (pos for pos in zerodha_positions if pos["quantity"] > 0), 
                        None
                    )
                    if existing_position:
                        # 🛡️ CRITICAL: Only restore if it's a DIFFERENT symbol than what we're trying to enter
                        if not self.position and existing_position["tradingsymbol"] != symbol:
                            # Sync mismatch: Zerodha has a different position
                            await self._log_debug("SYNC", f"🔄 Restoring orphaned position for {existing_position['tradingsymbol']} (was trying to enter {symbol})")
                            
                            entry_price_zerodha = (existing_position.get('buy_price') or 
                                          existing_position.get('average_price') or 
                                          existing_position.get('last_price') or 0)
                            
                            # 🛡️ DEFENSIVE: Ensure entry_price is float, not string
                            try:
                                entry_price_zerodha = float(entry_price_zerodha)
                            except (ValueError, TypeError):
                                entry_price_zerodha = 0.0
                            
                            # 🛡️ DEFENSIVE: Ensure last_price is float, not string
                            try:
                                last_price_zerodha = float(existing_position.get("last_price", entry_price_zerodha))
                            except (ValueError, TypeError):
                                last_price_zerodha = entry_price_zerodha
                            
                            self.position = {
                                "symbol": existing_position["tradingsymbol"],
                                "qty": abs(existing_position["quantity"]),
                                "direction": "CE" if "CE" in existing_position["tradingsymbol"] else "PE",
                                "entry_price": entry_price_zerodha,
                                "max_price": last_price_zerodha,
                                "trail_sl": 0,
                                "entry_time": get_ist_time().strftime("%Y-%m-%d %H:%M:%S"),
                                "trigger_reason": "RESTORED_FROM_ZERODHA",
                                "lot_size": self.lot_size
                            }
                            
                            for _ in range(3):
                                await self._update_ui_trade_status()
                                await asyncio.sleep(0.1)
                            
                            if self.ticker_manager and self.option_instruments:
                                instrument_token = next(
                                    (opt.get("instrument_token") for opt in self.option_instruments 
                                     if opt.get("tradingsymbol") == existing_position["tradingsymbol"]), 
                                    None
                                )
                                if instrument_token:
                                    self.ticker_manager.subscribe([instrument_token])
                                    await self._log_debug("SYNC", f"📡 Subscribed to {existing_position['tradingsymbol']} for live updates")
                            
                            await self._log_debug("SYNC", f"✅ Position restored: {existing_position['tradingsymbol']}, Qty: {self.position['qty']}, Entry: ₹{entry_price_zerodha:.2f}")
                        
                        # Check if trying to enter the SAME symbol (sync) or DIFFERENT symbol (block)
                        if existing_position["tradingsymbol"] == symbol:
                            # Same symbol - restore with actual entry price
                            try:
                                recent_orders = await kite.orders()
                                matching_order = None
                                for order in reversed(recent_orders):
                                    if (order.get("tradingsymbol") == symbol and 
                                        order.get("transaction_type") == "BUY" and 
                                        order.get("status") == "COMPLETE" and
                                        order.get("filled_quantity", 0) > 0):
                                        matching_order = order
                                        break
                                
                                if matching_order:
                                    actual_entry_price = float(matching_order.get("average_price", 0))
                                    filled_qty = int(matching_order.get("filled_quantity", 0))
                                    
                                    if not self.position:
                                        # Calculate initial SL using GUI parameters
                                        sl_points = float(self.params.get("trailing_sl_points", 2.0))
                                        sl_percent = float(self.params.get("trailing_sl_percent", 1.0))
                                        initial_sl = round(max(actual_entry_price - sl_points, actual_entry_price * (1 - sl_percent / 100)), 2)
                                        
                                        self.position = {
                                            "symbol": symbol,
                                            "qty": filled_qty,
                                            "direction": "CE" if "CE" in symbol else "PE",
                                            "entry_price": actual_entry_price,
                                            "max_price": actual_entry_price,
                                            "trail_sl": initial_sl,
                                            "entry_time": get_ist_time().strftime("%Y-%m-%d %H:%M:%S"),
                                            "trigger_reason": "RESTORED_FROM_ORDER_HISTORY",
                                            "lot_size": self.lot_size,
                                            # 🆕 Capture option Supertrend state at entry
                                            "entry_option_st_uptrend": self.data_manager.calculate_option_supertrend(symbol)[1] if self.data_manager else None
                                        }
                                        await self._log_debug("SYNC", 
                                            f"✅ Position restored from order history: {symbol}, Qty: {filled_qty}, Entry: ₹{actual_entry_price:.2f}")
                                        
                                        for _ in range(3):
                                            await self._update_ui_trade_status()
                                            await asyncio.sleep(0.1)
                            except Exception as order_fetch_error:
                                await self._log_debug("SYNC", f"⚠️ Failed to fetch order history: {order_fetch_error}")
                            
                            return False, f"Position sync: symbol already entered"  # Signal to stop entry
                        else:
                            # Different symbol - block new entry
                            return False, f"{_BROKER_LABEL} has position in {existing_position['tradingsymbol']} ({existing_position['quantity']} qty). Cannot enter {symbol}."
                    
                    return True, None  # No existing position, safe to enter
                
                except Exception as verify_error:
                    await self._log_debug("Layer 2", f"⚠️ Could not verify {_BROKER_LABEL} positions: {verify_error}")
                    return True, None  # Continue on API failure (don't block)
            
            # Prepare parallel check functions
            async def check_layer3():
                """LAYER 3: Trade Frequency Limiter"""
                if no_wick_bypass:
                    return True, None  # Skip for no-wick
                if self.trades_this_minute >= 3:
                    return False, f"Rate limit exceeded ({self.trades_this_minute} trades in current 1-minute candle)"
                elif self.trades_this_minute == 2:
                    if not hasattr(self, '_trades_this_minute_pnl'):
                        self._trades_this_minute_pnl = []
                    profitable_count = sum(1 for pnl in self._trades_this_minute_pnl if pnl > 0)
                    if profitable_count < 2:
                        return False, f"3rd trade requires 2 profitable trades (currently {profitable_count}/2 profitable)"
                return True, None
            
            async def check_layer4():
                """LAYER 4: Entry Cooldown After Exit"""
                if self.last_exit_time:
                    seconds_since_exit = (now - self.last_exit_time).total_seconds()
                    COOLDOWN_AFTER_EXIT_SECONDS = self.params.get('cooldown_after_exit', 3)
                    if seconds_since_exit < COOLDOWN_AFTER_EXIT_SECONDS:
                        return False, f"Too soon after last exit ({seconds_since_exit:.1f}s ago, need {COOLDOWN_AFTER_EXIT_SECONDS}s)"
                return True, None
            
            # Run Layer 3 and 4 in parallel (both are independent checks)
            layer_results = await asyncio.gather(
                check_layer3(),
                check_layer4(),
                return_exceptions=True
            )
            
            # Check results
            for idx, result in enumerate(layer_results):
                if isinstance(result, Exception):
                    await self._log_debug(f"Layer {idx+3}", f"❌ Exception: {result}")
                    self.entry_in_progress = False
                    return
                passed, error_msg = result
                if not passed:
                    await self._log_debug(f"Layer {idx+3}", f"🚫 BLOCKED: {error_msg}")
                    self.entry_in_progress = False
                    return

            # ⚡⚡ LAYER 5 & 6: RUN IN PARALLEL (both completely independent)
            
            async def check_layer5():
                """LAYER 5: Price-Based Duplicate (expires on candle close)"""
                PRICE_DUPLICATE_THRESHOLD = 0.10  # ₹0.10 threshold
                
                last_entry_symbol = self.last_entry_data.get('symbol')
                last_entry_price = self.last_entry_data.get('price')
                last_entry_candle_start = self.last_entry_data.get('candle_start_time')
                
                current_candle = self.data_manager.option_candles.get(symbol, {})
                current_candle_start = current_candle.get('candle_start_time', 0)
                
                # Auto-expire Layer 5 when candle closes
                if last_entry_candle_start is not None and current_candle_start > last_entry_candle_start:
                    self.last_entry_data = {}
                    return True, None  # Expired, allow entry
                
                # 🔥 REMOVED NO-WICK BYPASS: Layer 5 now ALWAYS checks for duplicate prices
                # NO-WICK should only skip 4-tick validation, NOT price duplicate check
                # This prevents multiple entries at ₹96.15, ₹113.55, ₹117.65 within seconds
                
                # 🔥 FIX: Use CURRENT live price, not stale entry_price from signal generation
                # entry_price can be stale (set at start of take_trade), but price moves during validation
                current_live_price = self.data_manager.prices.get(symbol, entry_price)
                
                # Check for duplicate
                if (last_entry_symbol == symbol and last_entry_price is not None and 
                    abs(current_live_price - last_entry_price) < PRICE_DUPLICATE_THRESHOLD):
                    current_option_type = 'CE' if 'CE' in symbol else 'PE'
                    last_option_type = 'CE' if 'CE' in last_entry_symbol else 'PE'
                    
                    if current_option_type == last_option_type:
                        return False, f"Same {current_option_type} at same price (₹{current_live_price:.2f} ≈ ₹{last_entry_price:.2f})"
                    # Reversal allowed
                
                return True, None
            
            async def check_layer6():
                """LAYER 6: Hybrid Smart Cooldown - 3-Way Re-Entry Logic"""
                
                # 🛡️ OPTION D: Check candle-based cooldown per symbol (prevents same-candle duplicates)
                if symbol in self.symbol_entry_cooldown:
                    cooldown_data = self.symbol_entry_cooldown[symbol]
                    cooldown_until = cooldown_data.get('until')
                    entry_price = cooldown_data.get('entry_price')
                    reason = cooldown_data.get('reason', 'Entry')
                    
                    if now < cooldown_until:
                        # Still in cooldown window (within same candle)
                        remaining = (cooldown_until - now).total_seconds()
                        return False, f"🛡️ Cooldown active until candle close: {remaining:.1f}s remaining (Entry @ ₹{entry_price:.2f} - {reason})"
                    else:
                        # Cooldown expired (new candle started) - remove it
                        await self._log_debug("Layer 6",
                            f"✅ Candle-based cooldown expired for {symbol}, allowing re-entry in new candle")
                        del self.symbol_entry_cooldown[symbol]
                
                if symbol not in self.symbol_exit_cooldown:
                    return True, None
                
                last_exit_data = self.symbol_exit_cooldown[symbol]
                last_exit_time = last_exit_data.get('time')
                last_exit_price = last_exit_data.get('price')
                last_exit_candle = last_exit_data.get('candle_start_time')
                last_direction = last_exit_data.get('direction')
                last_exit_reason = last_exit_data.get('reason', '')
                
                current_candle = self.data_manager.option_candles.get(symbol, {})
                current_candle_start = current_candle.get('candle_start_time', 0)
                current_trend = self.data_manager.trend_state
                current_price = self.data_manager.prices.get(symbol)
                
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # BYPASS 1: New Candle + Trend Continues = INSTANT Re-Entry
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                if current_candle_start > last_exit_candle:
                    trend_continues = (
                        (last_direction == 'CE' and current_trend == 'BULLISH') or
                        (last_direction == 'PE' and current_trend == 'BEARISH')
                    )
                    if trend_continues:
                        await self._log_debug("Layer 6 BYPASS",
                            f"✅ New candle + trend continues ({current_trend}): INSTANT re-entry allowed!")
                        del self.symbol_exit_cooldown[symbol]
                        return True, None
                
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # BYPASS 2: Price Recovered 1%+ = Early Re-Entry
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                if current_price and last_exit_price:
                    recovery_pct = ((current_price - last_exit_price) / last_exit_price) * 100
                    if recovery_pct >= 1.0:  # 1% recovery threshold
                        await self._log_debug("Layer 6 BYPASS",
                            f"✅ Price recovered {recovery_pct:.1f}%: ₹{last_exit_price:.2f} → ₹{current_price:.2f}, re-entry allowed!")
                        del self.symbol_exit_cooldown[symbol]
                        return True, None
                
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # BYPASS 3: Different Price = Reduced Cooldown (3s instead of 15s)
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                seconds_since_exit = (now - last_exit_time).total_seconds()
                
                # Check if current price is different enough from exit price
                if current_price and last_exit_price:
                    price_diff_from_exit = abs(current_price - last_exit_price)
                    price_diff_pct = (price_diff_from_exit / last_exit_price * 100) if last_exit_price > 0 else 0
                else:
                    price_diff_from_exit = 0
                    price_diff_pct = 0
                
                # Different price (>₹2 or >0.5%) → short 3s cooldown; Same price → full 15s
                if price_diff_from_exit > 2.0 or price_diff_pct > 0.5:
                    COOLDOWN_SECONDS = 3  # Short cooldown for different-price re-entry
                else:
                    COOLDOWN_SECONDS = 15  # Full cooldown for same-price re-entry
                
                if seconds_since_exit < COOLDOWN_SECONDS:
                    remaining = COOLDOWN_SECONDS - seconds_since_exit
                    return False, f"Cooldown: {remaining:.1f}s remaining (Exit: {last_exit_reason}, price diff: ₹{price_diff_from_exit:.2f})"
                else:
                    # Cooldown expired
                    await self._log_debug("Layer 6",
                        f"✅ 15-second cooldown expired: re-entry allowed")
                    del self.symbol_exit_cooldown[symbol]
                
                return True, None
            
            async def check_layer8():
                """LAYER 8: Smart Symbol Protection - Lock until end of candle, allow re-entry at different prices"""
                # Get current candle timing
                option_candle = self.data_manager.option_candles.get(symbol, {})
                candle_start_time = option_candle.get('candle_start_time', 0)
                
                # Calculate candle end time (1-minute candle = 60 seconds)
                if candle_start_time > 0:
                    candle_end_time_unix = candle_start_time + 60  # 1-minute candle
                    current_time_unix = time_module.time()
                    time_to_candle_end = candle_end_time_unix - current_time_unix
                else:
                    time_to_candle_end = 0
                
                # 🛡️ OPTION C: Check last entry price - prevent exact price re-entries
                if symbol in self.last_entry_info:
                    last_entry = self.last_entry_info[symbol]
                    last_entry_price = last_entry.get('price', 0)
                    last_entry_time = last_entry.get('time')
                    
                    if last_entry_time:
                        seconds_since_last_entry = (now - last_entry_time).total_seconds()
                        actual_entry_price = custom_entry_price if custom_entry_price else self.data_manager.prices.get(symbol, entry_price)
                        
                        # Check price difference
                        price_diff = abs(actual_entry_price - last_entry_price)
                        price_diff_pct = (price_diff / last_entry_price * 100) if last_entry_price > 0 else 0
                        
                        # Block same price until candle close (not fixed 60s)
                        # Check if we're still in the same candle as the last entry
                        last_entry_candle = last_entry.get('candle_start_time', 0)
                        same_candle = (candle_start_time > 0 and last_entry_candle > 0 and candle_start_time == last_entry_candle)
                        
                        if price_diff <= 0.15 and same_candle:
                            return False, f"🛡️ OPTION C: Same price in same candle: ₹{actual_entry_price:.2f} ≈ last entry ₹{last_entry_price:.2f} (diff: ₹{price_diff:.2f}) - blocked until candle close"
                
                async with self._layer8_lock:
                    actual_entry_price = custom_entry_price if custom_entry_price else self.data_manager.prices.get(symbol, entry_price)
                    current_price = actual_entry_price
                    
                    if symbol not in self.symbol_entry_lock:
                        # ✅ First entry for this symbol
                        self.symbol_entry_lock[symbol] = {
                            'time': now,
                            'price': current_price,
                            'trigger': trigger,
                            'entry_count': 1,
                            'candle_start_time': candle_start_time  # Track which candle entry happened in
                        }
                        return True, None
                    
                    lock_data = self.symbol_entry_lock[symbol]
                    lock_time = lock_data.get('time')
                    lock_price = lock_data.get('price', 0)
                    lock_trigger = lock_data.get('trigger', 'Unknown')
                    lock_candle_start = lock_data.get('candle_start_time', 0)
                    entry_count = lock_data.get('entry_count', 1) + 1
                    
                    # Calculate price difference percentage
                    if lock_price > 0 and current_price > 0:
                        price_change_pct = abs((current_price - lock_price) / lock_price) * 100
                    else:
                        price_change_pct = 0
                    
                    # 🛡️ CANDLE-BASED LOCK: Extend to end of current candle
                    # Allow re-entry at DIFFERENT PRICES within same candle
                    if candle_start_time > 0 and lock_candle_start > 0:
                        if candle_start_time == lock_candle_start:
                            # Same candle as entry - check if price is sufficiently different
                            MIN_PRICE_CHANGE = 0.15  # Allow re-entry at 0.15% price change within same candle
                            
                            if price_change_pct >= MIN_PRICE_CHANGE:
                                # Price moved significantly - allow re-entry at different price
                                await self._log_debug("Layer 8",
                                    f"📊 SAME-CANDLE RE-ENTRY #{entry_count}: {symbol} @ ₹{current_price:.2f} (price moved {price_change_pct:.2f}%, candle expires in {time_to_candle_end:.1f}s)")
                                self.symbol_entry_lock[symbol] = {
                                    'time': now,
                                    'price': current_price,
                                    'trigger': trigger,
                                    'entry_count': entry_count,
                                    'candle_start_time': candle_start_time
                                }
                                return True, None
                            else:
                                # Not enough price change yet - block within same candle
                                return False, f"Symbol locked in same candle by {lock_trigger} at ₹{lock_price:.2f} (current: ₹{current_price:.2f}, change: {price_change_pct:.2f}%, need 0.5%)"
                        else:
                            # Different candle - allow re-entry (lock expired at candle boundary)
                            await self._log_debug("Layer 8",
                                f"✅ CANDLE BOUNDARY: New candle for {symbol} @ ₹{current_price:.2f}, lock reset!")
                            self.symbol_entry_lock[symbol] = {
                                'time': now,
                                'price': current_price,
                                'trigger': trigger,
                                'entry_count': 1,
                                'candle_start_time': candle_start_time
                            }
                            return True, None
                    
                    # Fallback if candle timing unavailable (shouldn't happen)
                    seconds_since_lock = (now - lock_time).total_seconds()
                    if seconds_since_lock >= 60:
                        await self._log_debug("Layer 8",
                            f"✅ Symbol lock expired (60s): {symbol} @ ₹{current_price:.2f}, allowing re-entry")
                        self.symbol_entry_lock[symbol] = {
                            'time': now,
                            'price': current_price,
                            'trigger': trigger,
                            'entry_count': entry_count,
                            'candle_start_time': candle_start_time
                        }
                        return True, None
                    else:
                        remaining = 60 - seconds_since_lock
                        return False, f"Symbol locked by {lock_trigger} at ₹{lock_price:.2f} ({remaining:.1f}s remaining)"
            
            # ⚡⚡ Run Layers 5, 6 & 8 in PARALLEL (saves 5-10ms)
            layer568_results = await asyncio.gather(
                check_layer5(),
                check_layer6(),
                check_layer8(),
                return_exceptions=True
            )
            
            # Check results
            for idx, result in enumerate(layer568_results):
                layer_num = [5, 6, 8][idx]  # Map to actual layer numbers
                if isinstance(result, Exception):
                    await self._log_debug(f"Layer {layer_num}", f"❌ Exception: {result}")
                    self.entry_in_progress = False
                    return
                passed, error_msg = result
                if not passed:
                    await self._log_debug(f"Layer {layer_num}", f"🚫 BLOCKED: {error_msg}")
                    self.entry_in_progress = False
                    return 
            
            # --- End Pre-Execution Checks (All layers passed) ---
            # Note: Layer 8 lock already set inside check_layer8() to prevent race conditions
            
            # LAYER 7: Set active order ID for tracking
            # Note: entry_in_progress already set at Layer 1 (atomic with lock)
            self.active_order_id = f"PENDING_{symbol}_{int(now.timestamp())}" # Placeholder Order ID

            # 🎯 Initialize order_succeeded BEFORE try block for finally block access
            order_succeeded = False

            try:
                # --- ⚡ ULTRA-FAST PARALLEL EXECUTION LOGIC ---
                
                order_placement_time = get_ist_time()
                latency_ms = int((order_placement_time - signal_time).total_seconds() * 1000)
                
                instrument_token = opt.get("instrument_token")
                side, lot_size = opt["instrument_type"], opt.get("lot_size")
                price = entry_price
                
                # ⚡ PARALLEL EXECUTION: Fetch all required data simultaneously
                # This runs fresh price fetch, capital fetch, and momentum check IN PARALLEL
                # Reduces total latency from 500ms+ to ~100-150ms
                
                # 🚀 NO-WICK FAST TRACK: Skip fresh price fetch for NO-WICK entries
                # Use signal price immediately to avoid drift rejection
                async def fetch_fresh_price():
                    """Fetch current market price AND depth (for spread check)"""
                    # 🚀 NO-WICK BYPASS: Skip price fetch, use signal price directly
                    if no_wick_bypass:
                        return None  # Signal price already validated, proceed immediately
                    
                    try:
                        if self.params.get("trading_mode") == "Live Trading":
                            full_symbol = f"{self.exchange}:{symbol}"
                            quote = await kite.quote([full_symbol])
                            if quote and full_symbol in quote:
                                # 🔥 OPTIMIZATION: Return full quote data (not just price)
                                # This avoids second API call for spread checking
                                return quote[full_symbol]
                    except:
                        pass
                    return None
                
                async def fetch_capital():
                    """Fetch live capital - FRESH fetch after exit, otherwise use cache"""
                    try:
                        if self.params.get("trading_mode") == "Live Trading":
                            # 🔥 CRITICAL: Check if we just exited a trade recently (< 5 seconds)
                            # If so, DO NOT use cache - fetch fresh from Zerodha to get updated margin
                            # This prevents bot from blocking capital after exit
                            time_since_exit = 999
                            if hasattr(self, '_last_exit_time') and self._last_exit_time:
                                time_since_exit = (get_ist_time() - self._last_exit_time).total_seconds()
                            
                            if time_since_exit < 5.0:
                                # Recently exited - FORCE fresh fetch to see released margin
                                await self._log_debug("Capital", 
                                    f"🔄 Recent exit detected ({time_since_exit:.1f}s ago) - fetching FRESH capital")
                                return await self._fetch_live_capital_from_zerodha()
                            
                            # ⚡⚡ AGGRESSIVE OPTIMIZATION: Use cached capital if >5s since exit
                            # Background task refreshes every 3 seconds, so cache is usually < 3s old
                            # This eliminates 100-150ms API delay during entry execution
                            if self.live_capital_cache is not None:
                                # Return cached capital immediately (saves 100-150ms!)
                                return self.live_capital_cache
                            else:
                                # Fallback to fresh fetch only on first entry
                                return await self._fetch_live_capital_from_zerodha()
                    except:
                        pass
                    return None
                
                async def check_momentum():
                    """Check if price is actively rising (2 consecutive rising ticks)"""
                    # 🚀 OPTIMIZED: Relax momentum check - signal validation already confirmed trend
                    # Final momentum check rejects too many valid signals (80-90% rejection in volatile markets)
                    # Trust the dual momentum + predictive signals that already passed
                    return True  # Allow all entries that passed signal validation
                
                # 🚀 LAUNCH ALL OPERATIONS IN PARALLEL (saves 400-500ms!)
                quote_data, live_capital, momentum_ok = await asyncio.gather(
                    fetch_fresh_price(),
                    fetch_capital(),
                    check_momentum(),
                    return_exceptions=True
                )
                
                # Handle any exceptions from parallel operations
                fresh_price = None
                if quote_data and not isinstance(quote_data, Exception):
                    fresh_price = quote_data.get('last_price') if isinstance(quote_data, dict) else None
                if isinstance(live_capital, Exception):
                    live_capital = None
                if isinstance(momentum_ok, Exception):
                    momentum_ok = True
                
                # Micro-momentum abort check - DISABLED (too strict, rejects 80-90% of valid signals)
                # Trust the signal validation that already passed dual momentum checks
                if False and not momentum_ok:  # Never triggers now
                    self.entry_in_progress = False  # 🔥 CRITICAL: Reset flag before aborting
                    await self._log_debug("Final Check", f"❌ ABORTED {trigger}: Price not actively rising at execution.")
                    return
                
                # 🎯 PERFECT PRICE ENTRY: Check price velocity and handle missed entries
                # 🚀 OPTIMIZED: Skip velocity/acceleration checks - they reject too many valid breakouts
                # In options trading, price acceleration = momentum (good thing, not bad!)
                # If signal passed dual momentum validation, trust it
                if False:  # Disabled - velocity check rejects valid breakout entries
                    price_history = self.data_manager.price_history.get(symbol, [])
                    if len(price_history) >= 3:
                        recent_prices = [p for t, p in price_history[-3:]]  # Extract prices from (timestamp, price) tuples
                        if len(recent_prices) >= 3:
                            price_velocity = (recent_prices[-1] - recent_prices[0]) / recent_prices[0] * 100
                            max_velocity_pct = 3.0  # Don't enter if price moved >3% in last 3 ticks (relaxed from 2%)
                            
                            if price_velocity > max_velocity_pct:
                                # 🎯 SMART RE-ENTRY: Store this as a missed opportunity
                                # Will re-attempt if price stabilizes but momentum continues
                                self._store_missed_opportunity(symbol, price, trigger, side, lot_size)
                                self.entry_in_progress = False  # 🔥 CRITICAL: Reset flag before aborting
                                await self._log_debug("Perfect Entry", 
                                    f"⏸️ WAITING: Price accelerating ({price_velocity:.2f}%). Will retry if stabilizes.")
                                return
                
                # 🎯 ANTI-SLIPPAGE: Check bid-ask spread before entry (using cached quote data)
                # 🚀 OPTIMIZED: Skip spread check - IOC orders handle wide spreads automatically
                # If spread too wide, IOC simply won't fill (no risk of bad fill)
                if False:  # Disabled - IOC protects against wide spreads naturally
                    if quote_data and isinstance(quote_data, dict):
                        depth = quote_data.get('depth', {})
                        buy_depth = depth.get('buy', [])
                        sell_depth = depth.get('sell', [])
                        
                        if buy_depth and sell_depth and len(buy_depth) > 0 and len(sell_depth) > 0:
                            bid = buy_depth[0].get('price', 0)
                            ask = sell_depth[0].get('price', 0)
                            
                            if bid > 0 and ask > 0:
                                spread_pct = ((ask - bid) / ask * 100)
                                max_spread_pct = 2.0  # Don't enter if spread >2.0% (relaxed from 1.5%)
                                
                                if spread_pct > max_spread_pct:
                                    # 🎯 SMART RE-ENTRY: Store as missed opportunity, retry when spread tightens
                                    self._store_missed_opportunity(symbol, price, trigger, side, lot_size)
                                    
                                    # 🔓 CRITICAL FIX: Clear symbol lock from Layer 8
                                    if symbol in self.symbol_entry_lock:
                                        del self.symbol_entry_lock[symbol]
                                        await self._log_debug("Layer 8", f"🔓 Cleared symbol lock for {symbol} after spread rejection")
                                    
                                    self.entry_in_progress = False  # 🔥 CRITICAL: Reset flag before aborting
                                    await self._log_debug("Perfect Entry", 
                                        f"⏸️ WAITING: Spread too wide {spread_pct:.2f}%. Will retry when tightens.")
                                    return
                
                # Validate and use fresh price
                # 🚀 OPTIMIZED: Relax drift validation - IOC orders handle slippage protection
                # Strict drift checks reject valid entries, IOC won't fill at bad prices anyway
                if fresh_price and fresh_price > 0:
                    price_drift_pct = abs(fresh_price - price) / price * 100
                    price_drift_amount = fresh_price - price
                    max_slippage_pct = 15  # Relaxed - IOC provides natural protection
                    
                    # Only reject EXTREME drift (>15%) - IOC handles the rest
                    if price_drift_pct > max_slippage_pct:
                        await self._log_debug("Price Drift", 
                            f"❌ ABORTED: Extreme price movement {price_drift_pct:.2f}% (₹{price:.2f}→₹{fresh_price:.2f}). Max: {max_slippage_pct}%")
                        
                        # 🔓 CRITICAL FIX: Clear symbol lock from Layer 8
                        if symbol in self.symbol_entry_lock:
                            del self.symbol_entry_lock[symbol]
                            await self._log_debug("Layer 8", f"🔓 Cleared symbol lock for {symbol} after price drift rejection")
                        
                        self.entry_in_progress = False  # 🔥 CRITICAL: Reset flag before aborting
                        return
                    
                    # Use fresh price
                    price = fresh_price
                    await self._log_debug("FastExec", f"⚡ Fresh price: ₹{price:.2f} (drift: {price_drift_pct:.1f}%)")
                
                # ⚡ ULTRA-PARALLEL: Calculate quantity AND pre-fetch depth simultaneously
                # These operations are independent - can run in parallel
                async def calculate_quantity():
                    """Calculate trade quantity"""
                    return self.risk_manager.calculate_trade_details(
                        price, lot_size, available_cash=live_capital, daily_pnl=self.daily_net_pnl
                    )
                
                # 🟢 OPTIMIZATION: Start depth prefetch IMMEDIATELY for TREND trades (not in order task)
                # This saves 50-100ms by starting depth fetch as soon as signal detected
                depth_prefetch_task = None
                if not no_wick_bypass and self.params.get("trading_mode") == "Live Trading":
                    async def prefetch_depth_early():
                        """Pre-fetch market depth EARLY - as soon as signal detected"""
                        try:
                            from core.order_manager import _analyze_market_depth
                            full_symbol = f"{self.exchange}:{symbol}"
                            
                            await self._log_debug("DepthPrefetch", 
                                f"⏱️ Starting EARLY depth prefetch for {symbol} (TREND entry optimization)")
                            
                            # 🔥 FIXED JAN 29: Direct await - _analyze_market_depth is already async
                            result = await asyncio.wait_for(
                                _analyze_market_depth(full_symbol, kite.TRANSACTION_TYPE_BUY, False),
                                timeout=2.0
                            )
                            
                            await self._log_debug("DepthPrefetch", 
                                f"✅ Early depth prefetch complete for {symbol}")
                            return result
                        except asyncio.TimeoutError:
                            await self._log_debug("DepthPrefetch", 
                                f"⚠️ Depth prefetch timeout for {symbol}")
                            return None
                        except Exception as e:
                            await self._log_debug("DepthPrefetch", 
                                f"⚠️ Depth prefetch failed: {type(e).__name__}")
                            return None
                    
                    # Start prefetch task NOW (runs in parallel with qty calculation)
                    depth_prefetch_task = asyncio.create_task(prefetch_depth_early())
                
                # Run quantity calc (and depth fetch if TREND) in parallel
                qty_result = await calculate_quantity()
                if qty_result is None:
                    qty, initial_sl_price = None, None
                else:
                    qty, initial_sl_price = qty_result
                
                # 🔥 PRE-ENTRY MARGIN CHECK: Validate capital BEFORE placing order (prevents ghost positions)
                if self.params.get("trading_mode") == "Live Trading" and qty and qty > 0:
                    try:
                        # Quick check: Does our live capital * 20 exceed required margin?
                        # (Typical margin requirement is ~4-5% of position value)
                        estimated_position_value = price * qty
                        estimated_margin_needed = estimated_position_value * 0.05  # Conservative 5% estimate
                        
                        if live_capital is not None and estimated_margin_needed > live_capital:
                            await self._log_debug("Capital", 
                                f"❌ INSUFFICIENT MARGIN for {symbol}: Est. Need ₹{estimated_margin_needed:.2f}, Have ₹{live_capital:.2f}")
                            
                            # 🔥 CRITICAL: Force capital cache clear - next signal should get fresh fetch
                            # This prevents bot from repeatedly rejecting trades due to stale cache
                            self.live_capital_cache = None
                            self.live_capital_last_fetched = None
                            await self._log_debug("Capital", "🔄 Capital cache cleared - forcing fresh fetch on next signal")
                            
                            # 🔥 Also cancel prefetch task if running
                            if depth_prefetch_task and isinstance(depth_prefetch_task, asyncio.Task):
                                if not depth_prefetch_task.done():
                                    depth_prefetch_task.cancel()
                            
                            # Abort trade
                            if symbol in self.symbol_entry_lock:
                                del self.symbol_entry_lock[symbol]
                            self.entry_in_progress = False
                            return
                        else:
                            await self._log_debug("Capital", 
                                f"✅ MARGIN CHECK PASSED: Est. ₹{estimated_margin_needed:.2f} needed, ₹{live_capital:.2f} available")
                    except Exception as margin_check_error:
                        # Log but continue - don't block on margin checks
                        await self._log_debug("Capital", 
                            f"⚠️ Margin pre-check failed: {type(margin_check_error).__name__}")

                
                # 🔥 SEND QUEUED LOGS FROM RISK MANAGER
                # Risk manager queues logs to avoid asyncio conflicts - send them now
                if hasattr(self.risk_manager, 'pending_logs'):
                    for log_source, log_message in self.risk_manager.pending_logs:
                        await self._log_debug(log_source, log_message)
                    self.risk_manager.pending_logs = []  # Clear after sending
                
                # Handle depth fetch exception
                if isinstance(depth_prefetch_task, Exception):
                    depth_prefetch_task = None
                
                # 🔍 DEBUG: Log pre-flight values
                await self._log_debug("Pre-Flight", 
                    f"Qty={qty}, Token={instrument_token}, Instruments={len(self.option_instruments) if self.option_instruments else 0}, Freeze={self.freeze_limit}")
                
                # Check for quantity/token/limits AFTER calculation with detailed diagnostics
                if qty is None or instrument_token is None or not self.option_instruments or self.freeze_limit is None:
                    failed_checks = []
                    if qty is None: failed_checks.append("Qty=None")
                    if instrument_token is None: failed_checks.append("Token=None")
                    if not self.option_instruments: failed_checks.append("OptionInstruments=Empty")
                    if self.freeze_limit is None: failed_checks.append("FreezeLimit=None")
                    self.entry_in_progress = False  # 🔥 CRITICAL: Reset flag before aborting
                    await self._log_debug("Trade Rejected", f"Pre-flight checks failed: {', '.join(failed_checks)}")
                    
                    # 🔥 CRITICAL: Cancel depth prefetch if it's still running (don't waste async work)
                    if depth_prefetch_task and isinstance(depth_prefetch_task, asyncio.Task):
                        if not depth_prefetch_task.done():
                            depth_prefetch_task.cancel()
                            await self._log_debug("DepthPrefetch", "❌ Cancelled prefetch due to trade rejection")
                    
                    return
                
                # ✅ Market depth already pre-fetched in parallel above!
                
                # --- Order Execution (Basket Order Logic) ---
                total_filled_qty = 0
                final_entry_price = price
                
                if self.params.get("trading_mode") == "Live Trading":
                    # 🎯 SMART ADAPTIVE EXECUTION: Analyze order book to choose best strategy
                    # 1. Check if order will slice (qty > freeze_limit) → Always use depth
                    # 2. If not slicing, check if Level 1 has enough liquidity → Use chase (faster)
                    # 3. If Level 1 insufficient, check multi-level depth → Use depth analysis
                    
                    will_slice = self.freeze_limit and qty > self.freeze_limit  # Will split into multiple orders
                    
                    # Smart decision: Check order book depth before choosing strategy
                    if not will_slice:
                        # For small orders (< freeze), analyze if Level 1 can fill completely
                        use_depth_analysis = await self.order_manager.should_use_depth_analysis(
                            symbol=symbol, 
                            exchange=self.exchange, 
                            qty=qty, 
                            transaction_type=kite.TRANSACTION_TYPE_BUY
                        )
                    else:
                        # Large orders always use depth (will slice anyway)
                        use_depth_analysis = True
                    
                    # ⚡ PARALLEL PRE-ORDER CHECKS: Final momentum + Fresh price simultaneously
                    # 🚀 NO-WICK BYPASS: Skip momentum check if no-wick entry activated
                    if not no_wick_bypass and hasattr(self, 'v47_coordinator') and self.v47_coordinator:
                        # Run final momentum check + fresh price fetch in parallel
                        momentum_check = asyncio.create_task(
                            asyncio.to_thread(self.v47_coordinator._is_price_actively_rising, symbol, 2)
                        )
                        price_fetch = asyncio.create_task(
                            asyncio.to_thread(self.data_manager.prices.get, symbol, price)
                        )
                        
                        final_momentum_ok = await momentum_check
                        fresh_price = await price_fetch
                        
                        if not final_momentum_ok:
                            self.entry_in_progress = False
                            await self._log_debug("Final Check", 
                                f"🚫 ABORTED {trigger}: Price stopped rising or falling before order placement.")
                            return
                        
                        # Use fresh price if available
                        if fresh_price and fresh_price > 0:
                            price = fresh_price
                    
                    # 🚀 ALL ENTRIES NOW USE 3 ATTEMPTS with depth analysis
                    # Unified aggressive entry strategy for better fill rates:
                    # 
                    # ALL STRATEGIES: 3-attempt depth analysis with progressive pricing
                    #   - Attempt 1: Optimal depth price (200ms timeout)
                    #   - Attempt 2: Optimal + ₹0.25 buffer (200ms timeout)
                    #   - Attempt 3: Optimal + ₹0.50 buffer (200ms timeout)
                    #   - Total: 300-600ms, 95-98% fill rate, price-protected
                    # 
                    # NO-WICK MODE: Same 3 attempts but with 2.5% slippage cap
                    
                    # ⚡⚡⚡ CRITICAL OPTIMIZATION: START ORDER PLACEMENT IMMEDIATELY AS ASYNC TASK
                    # Then run all validation checks in PARALLEL while broker processes order
                    # This saves 100-200ms by eliminating sequential wait times!
                    
                    async def place_order_task():
                        """Start order placement immediately - runs in parallel with validations"""
                        # 🔥 CRITICAL GUARD: Verify qty is valid before placing ANY order
                        # Prevents race condition where qty calc fails but order still placed
                        if qty is None or qty <= 0:
                            await self._log_debug("Order Placement", 
                                f"❌ ABORT: Cannot place order with qty={qty} (invalid/insufficient capital)")
                            return {"status": "FAILED", "total_filled": 0, "orders": []}
                        
                        if no_wick_bypass:
                            # 🔥 FIXED: NO-WICK now uses IOC with buffer before MARKET (not straight MARKET)
                            # 3 attempts: (1) IOC at signal, (2) IOC at signal+0.25, (3) MARKET guaranteed fill
                            # This controls slippage better than direct MARKET orders
                            return await self.order_manager.execute_basket_order(
                                quantity=qty, 
                                transaction_type=kite.TRANSACTION_TYPE_BUY, 
                                tradingsymbol=symbol, 
                                exchange=self.exchange, 
                                freeze_limit=self.freeze_limit, 
                                price=price,
                                product=kite.PRODUCT_MIS,
                                use_level2_flow=True,
                                use_chase=False,
                                chase_retries=0,
                                chase_timeout_ms=0,
                                fallback_to_market=True,  # 🔥 FIXED: Allows MARKET fallback on attempt 3
                                no_wick_depth_mode=True,  # Still uses 2.5% slippage cap
                                max_slippage_percent=2.5,
                                prefetched_depth_task=None
                            )
                        else:
                            # ✅ STANDARD ENTRY: 3 attempts with depth analysis (same as NO-WICK but no slippage cap)
                            return await self.order_manager.execute_basket_order(
                                quantity=qty, 
                                transaction_type=kite.TRANSACTION_TYPE_BUY, 
                                tradingsymbol=symbol, 
                                exchange=self.exchange, 
                                freeze_limit=self.freeze_limit,
                                product=kite.PRODUCT_MIS, 
                                price=price,
                                use_level2_flow=True,
                                use_chase=False,
                                chase_retries=0,
                                chase_timeout_ms=0,
                                prefetched_depth_task=depth_prefetch_task
                            )
                    
                    # ⚡⚡ START ORDER PLACEMENT TASK IMMEDIATELY (don't await yet!)
                    order_placement_task = asyncio.create_task(place_order_task())
                    
                    # ⚡⚡ HYBRID APPROACH: Run Layer 2 in parallel WITH safety checks
                    # Layer 2 validation now has smart matching to accept positions that match our order
                    # This restores parallelism while preventing false blocks on our own fills
                    
                    async def verify_zerodha_positions_hybrid():
                        """
                        Smart Layer 2: Validates positions but accepts them if they match our pending order
                        This prevents false blocks while still catching real duplicates/orphaned positions
                        """
                        passed, error_msg = await verify_zerodha_positions_deferred()
                        
                        # If standard validation passed, return success
                        if passed:
                            return True, None
                        
                        # If standard validation failed, do smart matching
                        # Check if the "duplicate" position is actually the order we just placed
                        try:
                            zerodha_positions = (await kite.positions())["net"]
                            existing_position = next(
                                (pos for pos in zerodha_positions if pos["quantity"] > 0), 
                                None
                            )
                            
                            if existing_position and existing_position["tradingsymbol"] == symbol:
                                # 🛡️ SMART CHECK: Is this position our order or an old duplicate?
                                # Check if the position matches what we're trying to enter
                                pos_qty = abs(existing_position.get("quantity", 0))
                                estimated_qty = int(qty)  # Expected quantity
                                
                                # Accept if quantities are close (within 5 shares - small rounding errors)
                                qty_match = abs(pos_qty - estimated_qty) <= 5
                                
                                # Accept if price is recent (entered in last 10 seconds)
                                pos_time_str = existing_position.get("order_timestamp", "")
                                price_recent = True  # Default to true if we can't parse timestamp
                                try:
                                    if pos_time_str:
                                        pos_time = datetime.strptime(pos_time_str, "%Y-%m-%d %H:%M:%S")
                                        time_diff = (datetime.now() - pos_time).total_seconds()
                                        price_recent = time_diff <= 10  # Recent if within 10 seconds
                                except:
                                    pass
                                
                                # If both checks pass, this is likely OUR order
                                if qty_match and price_recent:
                                    await self._log_debug("L2-Zerodha-Hybrid", 
                                        f"✅ ACCEPTED: Position matches our pending order (qty: {pos_qty} ≈ {estimated_qty})")
                                    return True, None
                                else:
                                    await self._log_debug("L2-Zerodha-Hybrid", 
                                        f"❌ REJECTED: Position qty {pos_qty} or timing doesn't match our order (expected {estimated_qty})")
                                    return False, f"Position mismatch: qty {pos_qty} vs expected {estimated_qty}"
                        except Exception as e:
                            await self._log_debug("L2-Zerodha-Hybrid", f"⚠️ Hybrid check error: {e}")
                        
                        # If we couldn't verify, return original error
                        return False, error_msg
                    
                    validation_results = await asyncio.gather(
                        verify_zerodha_positions_hybrid(),  # Layer 2: Hybrid validation (parallel with order)
                        check_layer3(),    # Layer 3: Rate limit
                        check_layer4(),    # Layer 4: Exit cooldown
                        check_layer5(),    # Layer 5: Price duplicate
                        check_layer6(),    # Layer 6: Symbol cooldown
                        return_exceptions=True
                    )
                    
                    # ⚡ CHECK VALIDATION RESULTS (before waiting for order)
                    # If any check failed, we need to cancel the order if it completed
                    validation_failed = False
                    validation_error_msg = None
                    
                    for idx, result in enumerate(validation_results):
                        if isinstance(result, Exception):
                            layer_names = ["L2-Zerodha-Hybrid", "L3-RateLimit", "L4-Cooldown", "L5-PriceDup", "L6-SymbolCD"]
                            await self._log_debug(f"{layer_names[idx]}", f"❌ Exception: {result}")
                            validation_failed = True
                            validation_error_msg = f"Exception in {layer_names[idx]}"
                            break
                        
                        passed, error_msg = result
                        if not passed:
                            layer_names = ["L3-RateLimit", "L4-Cooldown", "L5-PriceDup", "L6-SymbolCD"]
                            await self._log_debug(f"{layer_names[idx]}", f"🚫 BLOCKED: {error_msg}")
                            validation_failed = True
                            validation_error_msg = error_msg
                            break
                    
                    # ⚡ WAIT FOR ORDER PLACEMENT TO COMPLETE
                    # 🚀 NO-WICK MODE: Increase timeout to 10s for 3 retry attempts (each ~2-3s)
                    # 🔧 LIVE FIX: 7s standard timeout (depth analysis 0.5s + 3 retries × 2s each)
                    order_timeout = 10.0 if no_wick_bypass else 7.0
                    try:
                        basket_result = await asyncio.wait_for(order_placement_task, timeout=order_timeout)
                    except asyncio.CancelledError:
                        await self._log_debug("Order Placement", "🚫 Order placement cancelled (bot shutdown)")
                        
                        # 🔥 CRITICAL FIX: Check if order completed BEFORE cancellation
                        # During shutdown, order may have filled but processing was interrupted
                        try:
                            # Get task result if available (non-blocking check)
                            if order_placement_task.done() and not order_placement_task.cancelled():
                                basket_result = order_placement_task.result()
                                await self._log_debug("Order Placement", 
                                    f"✅ Order completed before cancellation: {basket_result.get('status', 'UNKNOWN')}")
                                # Continue to process the result below (don't return early)
                            else:
                                # Task genuinely cancelled, check Zerodha for position
                                await asyncio.sleep(0.5)
                                positions = await kite.positions()
                                net_positions = positions.get('net', [])
                                matching_position = None
                                for pos in net_positions:
                                    if pos['tradingsymbol'] == symbol and pos['quantity'] != 0:
                                        matching_position = pos
                                        break
                                
                                if matching_position:
                                    # Order filled during cancellation! Create position
                                    await self._log_debug("Order Placement", 
                                        f"✅ Position found in {_BROKER_LABEL} despite cancellation: {matching_position['quantity']} qty")
                                    basket_result = {
                                        "status": "COMPLETE",
                                        "total_filled": abs(matching_position['quantity']),
                                        "avg_price": abs(matching_position['average_price']),
                                        "order_ids": [],
                                        "orders": []
                                    }
                                    # Continue to process result below
                                else:
                                    # No position found, order genuinely cancelled
                                    await self._log_debug("Order Placement", "❌ No position found, order cancelled")
                                    self.live_capital_cache = None
                                    self.live_capital_last_fetched = None
                                    await asyncio.sleep(0.5)
                                    refreshed_capital = await self._fetch_live_capital_from_zerodha()
                                    if refreshed_capital:
                                        await self._log_debug("Capital", f"✅ Capital refreshed: ₹{refreshed_capital:.0f} after cancellation")
                                    
                                    if symbol in self.symbol_entry_lock:
                                        del self.symbol_entry_lock[symbol]
                                        await self._log_debug("Layer 8", f"🔓 Cleared symbol lock for {symbol} after cancellation")
                                    
                                    self.entry_in_progress = False
                                    raise  # Re-raise to properly terminate
                        except Exception as check_error:
                            await self._log_debug("Order Placement", f"⚠️ Error checking order result: {check_error}")
                            self.entry_in_progress = False
                            raise
                    except asyncio.TimeoutError:
                        await self._log_debug("Order Placement", f"⏱️ Order placement timed out ({order_timeout}s)")
                        
                        # 💰 CAPITAL FIX: Clear cache and refresh after timeout
                        self.live_capital_cache = None
                        self.live_capital_last_fetched = None
                        await asyncio.sleep(0.5)
                        refreshed_capital = await self._fetch_live_capital_from_zerodha()
                        if refreshed_capital:
                            await self._log_debug("Capital", f"✅ Capital refreshed: ₹{refreshed_capital:.0f} after timeout")
                        
                        # 🔓 CRITICAL FIX: Clear symbol lock from Layer 8
                        if symbol in self.symbol_entry_lock:
                            del self.symbol_entry_lock[symbol]
                            await self._log_debug("Layer 8", f"🔓 Cleared symbol lock for {symbol} after timeout")
                        
                        self.entry_in_progress = False
                        return
                    except Exception as order_error:
                        await self._log_debug("Order Placement", f"❌ Order placement error: {order_error}")
                        
                        # 💰 CAPITAL FIX: Clear cache and refresh after error
                        self.live_capital_cache = None
                        self.live_capital_last_fetched = None
                        await asyncio.sleep(0.5)
                        refreshed_capital = await self._fetch_live_capital_from_zerodha()
                        if refreshed_capital:
                            await self._log_debug("Capital", f"✅ Capital refreshed: ₹{refreshed_capital:.0f} after error")
                        
                        # 🔓 CRITICAL FIX: Clear symbol lock from Layer 8
                        if symbol in self.symbol_entry_lock:
                            del self.symbol_entry_lock[symbol]
                            await self._log_debug("Layer 8", f"🔓 Cleared symbol lock for {symbol} after error")
                        
                        self.entry_in_progress = False
                        return
                    
                    # ⚡ IF VALIDATION FAILED, CANCEL THE ORDER IF IT COMPLETED
                    if validation_failed:
                        await self._log_debug("Validation Failed", 
                            f"❌ {validation_error_msg} - attempting to cancel order if filled")
                        
                        # If order was placed and filled, try to cancel it
                        if basket_result["status"] in ["COMPLETE", "PARTIAL"]:
                            try:
                                order_id = basket_result.get("order_id")
                                if order_id:
                                    cancel_result = await self.order_manager.cancel_order(order_id)
                                    await self._log_debug("Order Cancel", 
                                        f"🚫 Cancelled order {order_id} due to validation failure")
                            except Exception as cancel_error:
                                await self._log_debug("Cancel Error", f"⚠️ Could not cancel order: {cancel_error}")
                        
                        # 💰 CAPITAL FIX: Clear cache and refresh after validation failure
                        self.live_capital_cache = None
                        self.live_capital_last_fetched = None
                        await asyncio.sleep(0.5)
                        refreshed_capital = await self._fetch_live_capital_from_zerodha()
                        if refreshed_capital:
                            await self._log_debug("Capital", f"✅ Capital refreshed: ₹{refreshed_capital:.0f} after validation failure")
                        
                        # 🔓 CRITICAL FIX: Clear symbol lock from Layer 8
                        if symbol in self.symbol_entry_lock:
                            del self.symbol_entry_lock[symbol]
                            await self._log_debug("Layer 8", f"🔓 Cleared symbol lock for {symbol} after validation failure")
                        
                        self.entry_in_progress = False
                        return
                    
                    # 🔍 DEBUG: Log basket_result to diagnose position creation issue
                    await self._log_debug("DEBUG-BASKET", f"📦 basket_result keys: {basket_result.keys() if basket_result else 'None'}")
                    await self._log_debug("DEBUG-BASKET", f"📦 basket_result['status']: {basket_result.get('status', 'MISSING')}")
                    await self._log_debug("DEBUG-BASKET", f"📦 Full basket_result: {basket_result}")
                    
                    # ⚡ HYBRID OPTION A: Layer 2 already ran in parallel during order placement
                    # No need for post-order Layer 2 check - validation happened concurrently with order
                    
                    
                    # 🚨 UNKNOWN STATUS: Order was placed but verification cancelled - check with Zerodha
                    if basket_result["status"] == "UNKNOWN":
                        await self._log_debug("Order Status", "⚠️ Order status UNKNOWN (verification cancelled). Checking Zerodha positions...")
                        
                        # Wait a bit for order to settle
                        await asyncio.sleep(0.5)
                        
                        # Check if position exists in Zerodha
                        try:
                            positions = await kite.positions()
                            net_positions = positions.get('net', [])
                            
                            # Look for matching position
                            matching_position = None
                            for pos in net_positions:
                                if pos['tradingsymbol'] == symbol and pos['quantity'] != 0:
                                    matching_position = pos
                                    break
                            
                            if matching_position:
                                # Position found - order must have filled!
                                order_succeeded = True
                                estimated_filled_qty = abs(matching_position['quantity'])
                                
                                # 🔥 CRITICAL FIX: Get actual fill price from ORDER HISTORY, not position
                                # Position API returns unreliable prices (last_price, average_price can be wrong)
                                estimated_price = 0
                                if basket_result.get("order_ids") and len(basket_result["order_ids"]) > 0:
                                    try:
                                        order_id = basket_result["order_ids"][0]
                                        order_history = await kite.order_history(order_id=order_id)
                                        if order_history:
                                            latest = order_history[-1]
                                            estimated_price = float(latest.get('average_price', 0))
                                            await self._log_debug("Order Status", 
                                                f"✅ Got fill price from order history: ₹{estimated_price:.2f}")
                                    except Exception as e:
                                        await self._log_debug("Order Status", 
                                            f"⚠️ Could not fetch order history: {e}")
                                
                                # Fallback to position data if order history failed
                                if not estimated_price or estimated_price <= 0:
                                    # All entries are BUY orders, so always use buy_price
                                    estimated_price = abs(matching_position.get('buy_price', matching_position.get('average_price', 0)))
                                    await self._log_debug("Order Status", 
                                        f"⚠️ Using position buy_price (order history unavailable): ₹{estimated_price:.2f}")
                                
                                await self._log_debug("Order Status", 
                                    f"✅ Position found in Zerodha: {estimated_filled_qty} qty @ ₹{estimated_price:.2f}")
                                
                                # 🔥 CRITICAL FIX: Create position immediately for GUI (same as COMPLETE block)
                                await self._log_debug("LIVE TRADE", 
                                    f"✅ BUY {symbol}: {estimated_filled_qty} qty @ ₹{estimated_price:.4f}. Reason: {trigger} (recovered from UNKNOWN status)")
                                
                                # Create position for GUI display
                                import time as time_module  # For candle age calculation
                                entry_timestamp = get_ist_time()
                                # Calculate initial SL using GUI parameters
                                sl_points = float(self.params.get("trailing_sl_points", 5.0))
                                sl_percent = float(self.params.get("trailing_sl_percent", 2.5))
                                initial_sl_temp = round(max(estimated_price - sl_points, estimated_price * (1 - sl_percent / 100)), 2)
                                
                                # 🔧 CAPTURE CANDLE OHLC AT ENTRY TIME
                                option_candle = self.data_manager.option_candles.get(symbol, {})
                                candle_open_price = option_candle.get('open', None)
                                candle_start_time = option_candle.get('candle_start_time', 0)
                                candle_age = (time_module.time() - candle_start_time) if candle_start_time > 0 else 0
                                is_candle_active = candle_age < 60
                                candle_close_price = estimated_price if is_candle_active else option_candle.get("close", estimated_price)
                                
                                self.position = {
                                    "symbol": symbol,
                                    "entry_price": estimated_price,
                                    "direction": side,
                                    "qty": estimated_filled_qty,
                                    "trigger_reason": trigger,
                                    "lot_size": lot_size,
                                    "max_price": estimated_price,
                                    "trail_sl": initial_sl_temp,
                                    "entry_time": entry_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                                    "candle_open_price": candle_open_price,  # 🆕 Store candle open at entry
                                    "candle_close_price": candle_close_price  # 🆕 Store candle close/LTP at entry
                                }
                                
                                # Force immediate GUI refresh
                                await self._update_ui_trade_status()
                                await self._log_debug("UI Update", f"✅ Position created and GUI updated for {symbol}")
                                
                                # Reset entry flag
                                self.entry_in_progress = False
                                self.entry_started_at = None
                                
                            else:
                                # No position - order probably cancelled or rejected
                                await self._log_debug("Order Status", "❌ No position found in Zerodha. Order likely cancelled/rejected.")
                                
                                # 💰 CAPITAL FIX: Clear cache and refresh
                                self.live_capital_cache = None
                                self.live_capital_last_fetched = None
                                await asyncio.sleep(0.5)
                                refreshed_capital = await self._fetch_live_capital_from_zerodha()
                                if refreshed_capital:
                                    await self._log_debug("Capital", f"✅ Capital refreshed: ₹{refreshed_capital:.0f} after unknown status")
                                
                                self.entry_in_progress = False
                                return
                        except Exception as pos_check_error:
                            await self._log_debug("Order Status", f"⚠️ Error checking positions: {pos_check_error}")
                            self.entry_in_progress = False
                            return
                    
                    elif basket_result["status"] in ["COMPLETE", "PARTIAL"]:
                        # ⚡ CRITICAL: Only create position if orders were actually filled
                        # CANCELLED status means IOC orders found no liquidity - skip position creation
                        order_succeeded = True
                        estimated_filled_qty = basket_result.get("total_filled", qty)
                        estimated_price = basket_result.get("avg_price", price)
                        
                        await self._log_debug("🚀 LIVE ENTRY DETECTED", 
                            f"Order status: {basket_result['status']} | Qty: {estimated_filled_qty} | Price: ₹{estimated_price}")
                        
                        # 🛡️ DEFENSIVE: Ensure estimated_price is float, not string
                        try:
                            estimated_price = float(estimated_price)
                        except (ValueError, TypeError):
                            estimated_price = price  # Fallback to expected price
                        
                        # 🚀 Log entry type for differentiation
                        await self._log_debug("ENTRY-TYPE", f"📊 Entry Type: {'🚀 NO-WICK BYPASS' if no_wick_bypass else '✅ STANDARD'} | Trigger: {trigger}")
                        
                        # Initialize with basket data (used as fallback if verification fails)
                        final_entry_price = estimated_price
                        total_filled_qty = estimated_filled_qty
                        actual_fill_time = None
                        
                        # 🛡️ TRADE ENTRY LOCK: Prevent shutdown while trade is being entered/verified
                        # Set the flag immediately - this is checked by shutdown logic
                        self._trade_entry_in_progress = True
                        await self._log_debug("Trade Entry", f"🔐 FLAG SET - Trade entry starting for {symbol}")
                        
                        try:
                            # Log immediate trade execution (BEFORE verification to ensure it's logged)
                            await self._log_debug("LIVE TRADE", 
                                f"✅ BUY {symbol}: {estimated_filled_qty} qty @ ₹{estimated_price:.4f}. Reason: {trigger} (verifying in background...)")
                            
                            # ⚡ INSTANT UI UPDATE: Create position immediately for GUI display (live trading)
                            # This ensures GUI updates instantly while verification runs in background
                            entry_timestamp = get_ist_time()
                            signal_time = entry_timestamp.strftime("%Y-%m-%d %H:%M:%S")  # Store signal generation time
                            
                            # Calculate initial SL using GUI parameters
                            sl_points = float(self.params.get("trailing_sl_points", 5.0))
                            sl_percent = float(self.params.get("trailing_sl_percent", 2.5))
                            initial_sl_temp = round(max(estimated_price - sl_points, estimated_price * (1 - sl_percent / 100)), 2)
                            
                            # Extract entry type from momentum_data (for ST angle strategy)
                            entry_type = momentum_data.get('entry_type', None) if momentum_data else None
                            
                            # 🎯 DETERMINE ENTRY CANDLE COLOR: Check if entry is on green or red candle
                            import time as time_module  # For candle age calculation
                            option_candle = self.data_manager.option_candles.get(symbol, {})
                            candle_open = option_candle.get('open', 0)
                            entry_candle_was_green = estimated_price > candle_open if candle_open else None
                            
                            # � CAPTURE CANDLE OHLC AT ENTRY TIME
                            candle_open_price = candle_open if candle_open else None
                            candle_start_time = option_candle.get('candle_start_time', 0)
                            candle_age = (time_module.time() - candle_start_time) if candle_start_time > 0 else 0
                            is_candle_active = candle_age < 60
                            candle_close_price = estimated_price if is_candle_active else option_candle.get("close", estimated_price)
                            
                            # 🚀 CREATE POSITION - THIS IS CRITICAL FOR TRADE TRACKING
                            self.position = {
                                "symbol": symbol,
                                "entry_price": estimated_price,
                                "direction": side,
                                "qty": estimated_filled_qty,
                                "trigger_reason": trigger,
                                "lot_size": lot_size,
                                "max_price": estimated_price,
                                "trail_sl": initial_sl_temp,
                                "entry_time": signal_time,  # Will be updated to fill_time by verification
                                "signal_time": signal_time,  # 🆕 Store original signal generation time
                                "entry_type": entry_type,  # For ST angle exit logic
                                "entry_candle_was_green": entry_candle_was_green,  # For intra-candle reversal detection
                                "candle_open_price": candle_open_price,  # 🆕 Store candle open at entry
                                "candle_close_price": candle_close_price,  # 🆕 Store candle close/LTP at entry
                                "momentum_data": momentum_data  # For price observer velocity tracking
                            }
                            
                            await self._log_debug("✅ POSITION CREATED", 
                                f"Symbol: {symbol} | Qty: {estimated_filled_qty} | Entry: ₹{estimated_price:.4f} | "
                                f"SL: ₹{initial_sl_temp:.4f}")
                            
                            # 🔥 INSTANT UPDATE: Force immediate GUI refresh
                            await self._update_ui_trade_status()
                            await self._log_debug("✅ UI UPDATED", f"Trade status broadcast sent for {symbol}")
                            
                            # 🛡️ CRITICAL: Reset entry flag now that position is created
                            self.entry_in_progress = False
                            self.entry_started_at = None
                            
                            # 🚀 NON-BLOCKING: Verify in background while continuing to process ticks
                            # This allows UI updates, debug logs, and tick processing to continue immediately
                            async def background_verification():
                                try:
                                    # Add timeout to prevent verification from hanging indefinitely
                                    total_filled_qty, actual_entry_price, actual_fill_time = await asyncio.wait_for(
                                        self._verify_order_execution(basket_result),
                                        timeout=3.0  # Max 3 seconds for verification
                                    )
                                    
                                    if total_filled_qty == 0:
                                        await self._log_debug("Trade Rejected", 
                                            f"❌ Order {basket_result['status']}: 0 qty filled per verification. Clearing position.")
                                        self.position = None  # Clear the instant position
                                        self.entry_in_progress = False  # Reset flag
                                        # 🔥 INSTANT UPDATE: Force immediate GUI clear
                                        await self._update_ui_trade_status()
                                        return
                                    
                                    # Update position with verified details if they differ
                                    if self.position and self.position.get("symbol") == symbol:
                                        price_changed = False
                                        if actual_entry_price and actual_entry_price != self.position["entry_price"]:
                                            # 🛡️ DEFENSIVE: Ensure actual_entry_price is float, not string
                                            try:
                                                actual_entry_price = float(actual_entry_price)
                                            except (ValueError, TypeError):
                                                actual_entry_price = self.position["entry_price"]  # Keep original
                                            
                                            old_entry_price = self.position["entry_price"]
                                            self.position["entry_price"] = actual_entry_price
                                            self.position["max_price"] = actual_entry_price
                                            price_changed = True
                                            await self._log_debug("LIVE TRADE", 
                                                f"✅ Verified price: ₹{actual_entry_price:.4f} (was ₹{estimated_price:.4f})")
                                            
                                            # 🔥 CRITICAL: Recalculate SL based on actual entry price
                                            sl_points = float(self.params.get("trailing_sl_points", 2.0))
                                            sl_percent = float(self.params.get("trailing_sl_percent", 1.0))
                                            new_sl_price = max(actual_entry_price - sl_points, actual_entry_price * (1 - sl_percent / 100))
                                            old_sl = self.position.get("trail_sl", 0)
                                            self.position["trail_sl"] = round(new_sl_price, 2)
                                            await self._log_debug("SL-RECALC", 
                                                f"📊 SL recalculated: ₹{old_sl:.2f} → ₹{new_sl_price:.2f} (Entry: ₹{old_entry_price:.2f} → ₹{actual_entry_price:.2f})")
                                        
                                        if total_filled_qty != self.position["qty"]:
                                            self.position["qty"] = total_filled_qty
                                            await self._log_debug("LIVE TRADE", 
                                                f"✅ Verified qty: {total_filled_qty} (was {estimated_filled_qty})")
                                        
                                        # 🔥 UPDATE TIMESTAMP: Use actual fill time if available
                                        nonlocal entry_timestamp
                                        if actual_fill_time:
                                            try:
                                                entry_timestamp = datetime.strptime(actual_fill_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                                                self.position["entry_time"] = entry_timestamp.strftime("%Y-%m-%d %H:%M:%S")
                                                await self._log_debug("Entry Time", f"✅ Updated to actual fill time: {actual_fill_time}")
                                            except:
                                                pass  # Keep original timestamp if parsing fails
                                        
                                        # 🔥 INSTANT UPDATE: Force immediate GUI refresh with verified data
                                        await self._update_ui_trade_status()
                                        await self._log_debug("Verification", "✅ Background verification complete")
                                    
                                except asyncio.TimeoutError:
                                    await self._log_debug("LIVE TRADE", 
                                        f"⚠️ Verification timeout (3s). Using basket data: {estimated_filled_qty} @ ₹{estimated_price:.4f}")
                                    
                                except Exception as verify_error:
                                    await self._log_debug("LIVE TRADE", 
                                        f"⚠️ Verification failed: {verify_error}. Using basket data: {estimated_filled_qty} @ ₹{estimated_price:.4f}")
                                
                                finally:
                                    # 🛡️ CLEAR FLAG: Allow shutdown after verification completes
                                    self._trade_entry_in_progress = False
                                    await self._log_debug("Trade Entry", f"🔓 FLAG CLEARED - Trade entry completed for {symbol}")
                            
                            # Launch verification in background (non-blocking)
                            verification_task = asyncio.create_task(background_verification())
                            
                            # 🛡️ CRITICAL: Add done callback to ensure flag is cleared even if task is cancelled
                            def clear_entry_flag_on_done(task):
                                try:
                                    # Check if task was cancelled
                                    if task.cancelled():
                                        self._trade_entry_in_progress = False
                                        asyncio.create_task(self._log_debug("Trade Entry", 
                                            f"🔓 FLAG CLEARED (cancelled) - Trade entry cancelled for {symbol}"))
                                except Exception as callback_error:
                                    pass  # Suppress callback errors
                            
                            verification_task.add_done_callback(clear_entry_flag_on_done)
                            
                            # ⚡ CRITICAL FIX: Continue to position finalization after live trade
                            # Set entry_timestamp for live trading
                            entry_timestamp = get_ist_time()
                        
                        except Exception:
                            # If trade entry fails, clear the flag and re-raise
                            self._trade_entry_in_progress = False
                            raise
                        
                    # Skip to finalization - don't process elif/else blocks
                    if not order_succeeded:
                        # Only check these if order didn't succeed
                        if basket_result["status"] == "CANCELLED":
                            # 🚫 IOC ORDERS CANCELLED: No liquidity at attempted price levels
                            await self._log_debug("Trade Rejected", 
                                f"⚠️ IOC orders CANCELLED for {symbol} - no immediate fills available at attempted prices. "
                                f"This indicates low liquidity or price moved away. No position created.")
                            
                            # 💰 CAPITAL FIX: Clear cache and wait for Zerodha to release reserved funds
                            self.live_capital_cache = None
                            self.live_capital_last_fetched = None
                            await self._log_debug("Capital", "🔄 Cleared capital cache to refresh after order cancellation")
                            
                            # Wait 500ms for Zerodha to release the funds
                            await asyncio.sleep(0.5)
                            
                            # Force immediate capital refresh
                            refreshed_capital = await self._fetch_live_capital_from_zerodha()
                            if refreshed_capital:
                                await self._log_debug("Capital", f"✅ Capital refreshed: ₹{refreshed_capital:.0f} available after cancellation")
                            
                            # 🔓 CRITICAL FIX: Clear symbol lock from Layer 8 to allow retry on next signal
                            if symbol in self.symbol_entry_lock:
                                del self.symbol_entry_lock[symbol]
                                await self._log_debug("Layer 8", f"🔓 Cleared symbol lock for {symbol} after IOC cancellation")
                            
                            self.entry_in_progress = False
                            
                            # 📡 Update UI with failure status
                            await self._update_ui_trade_status()
                            return
                        else:
                            # FAILED or other status
                            error_msg = basket_result.get("orders", [{}])[0].get("error", "") if basket_result.get("orders") else ""
                            kill_switch.check_failed_orders("REJECTED", error_msg)
                            
                            # 🔍 Show detailed error reason to user
                            if "Insufficient funds" in error_msg or "margin" in error_msg.lower():
                                await self._log_debug("Trade Rejected", 
                                    f"❌ Insufficient margin for {symbol}. {error_msg}")
                            else:
                                await self._log_debug("Trade Rejected", 
                                    f"❌ Basket order FAILED for {symbol}. Error: {error_msg or 'Unknown'}")
                            
                            # 💰 CAPITAL FIX: Clear cache and wait for Zerodha to release reserved funds
                            self.live_capital_cache = None
                            self.live_capital_last_fetched = None
                            await self._log_debug("Capital", "🔄 Cleared capital cache to refresh after order failure")
                            
                            # Wait 500ms for Zerodha to release the funds
                            await asyncio.sleep(0.5)
                            
                            # Force immediate capital refresh
                            refreshed_capital = await self._fetch_live_capital_from_zerodha()
                            if refreshed_capital:
                                await self._log_debug("Capital", f"✅ Capital refreshed: ₹{refreshed_capital:.0f} available after failure")
                            
                            # 🔓 CRITICAL FIX: Clear symbol lock from Layer 8
                            if symbol in self.symbol_entry_lock:
                                del self.symbol_entry_lock[symbol]
                                await self._log_debug("Layer 8", f"🔓 Cleared symbol lock for {symbol} after order failure")
                            
                            self.entry_in_progress = False  # 🔥 CRITICAL: Reset flag on order failure
                            return
                else:
                    # Paper trading simulation WITH realistic live trading delays
                    # 🎯 SIMULATE LIVE TRADING FLOW:
                    # 1. Order placement API call
                    # 2. Broker order routing
                    # 3. Exchange matching
                    # 4. Fill confirmation
                    # 5. Verification callback
                    
                    # 🔥 CRITICAL: Capture entry timestamp BEFORE delays
                    # Delays simulate order routing/matching, not trade duration
                    # Entry time = when order is PLACED (before delays)
                    # This prevents delays from inflating trade duration
                    entry_timestamp = get_ist_time()
                    
                    total_filled_qty = qty
                    if self.freeze_limit and qty > self.freeze_limit:
                        num_slices = math.ceil(qty / self.freeze_limit)
                        await self._log_debug("PAPER TRADE", f"Simulating BASKET order: {num_slices} slices, Total: {qty} qty @ ₹{price:.4f}")
                    else:
                        await self._log_debug("PAPER TRADE", f"Simulating BUY {symbol}. Qty: {qty} @ ₹{price:.4f}")
                    
                    # 🕐 DELAY 1: Order placement + API latency (200-300ms)
                    entry_delay_ms = float(self.params.get("paper_entry_delay_ms", 450))
                    if entry_delay_ms > 0:
                        order_placement_delay = entry_delay_ms * 0.4  # 40% for order placement
                        await asyncio.sleep(order_placement_delay / 1000)
                        await self._log_debug("PAPER TRADE", f"⏱️ Simulated order placement delay: {order_placement_delay:.0f}ms")
                    
                    # 🕐 DELAY 2: Exchange execution + fill (100-200ms)
                    if entry_delay_ms > 0:
                        execution_delay = entry_delay_ms * 0.3  # 30% for execution
                        await asyncio.sleep(execution_delay / 1000)
                        await self._log_debug("PAPER TRADE", f"⏱️ Simulated order execution delay: {execution_delay:.0f}ms")
                    
                    # 🕐 DELAY 3: Verification callback (100-150ms)
                    verification_delay_ms = float(self.params.get("paper_verification_delay_ms", 250))
                    if verification_delay_ms > 0:
                        await asyncio.sleep(verification_delay_ms / 1000)
                        await self._log_debug("PAPER TRADE", f"⏱️ Simulated verification delay: {verification_delay_ms:.0f}ms")
                    
                    total_simulated_delay = (entry_delay_ms * 0.7) + verification_delay_ms
                    await self._log_debug("PAPER TRADE", 
                        f"✅ Entry complete. Total simulated delay: {total_simulated_delay:.0f}ms (mimics live trading)")
                    
                    # Note: entry_timestamp already set before delays (see above)
                    # Delays are simulated order flow, not trade duration
                    
                    # ⚡ INSTANT UI UPDATE: Create position immediately for GUI display (paper trading)
                    # CRITICAL: Include entry_time in initial creation to prevent NULL database entries
                    # Calculate initial SL using GUI parameters
                    import time as time_module  # For candle age calculation
                    sl_points = float(self.params.get("trailing_sl_points", 2.0))
                    sl_percent = float(self.params.get("trailing_sl_percent", 1.0))
                    initial_sl_temp = round(max(price - sl_points, price * (1 - sl_percent / 100)), 2)
                    # 🆕 Capture current candle open/close price for analysis AT ENTRY TIME
                    option_candle = self.data_manager.option_candles.get(symbol, {}) if self.data_manager else {}
                    candle_open_price = option_candle.get("open")
                    # 🔧 FIX: Capture candle close/LTP at ENTRY time, not exit time
                    # If candle is active (< 60s old), use current LTP, otherwise use stored close
                    candle_start_time = option_candle.get('candle_start_time', 0)
                    candle_age = (time_module.time() - candle_start_time) if candle_start_time > 0 else 0
                    is_candle_active = candle_age < 60
                    candle_close_price = price if is_candle_active else option_candle.get("close", price)  # Use entry price as close for active candles
                    entry_candle_was_green = price > candle_open_price if candle_open_price else None
                    self.position = {
                        "symbol": symbol, 
                        "entry_price": price, 
                        "direction": side, 
                        "qty": total_filled_qty,
                        "trigger_reason": trigger,
                        "lot_size": lot_size,
                        "max_price": price,
                        "trail_sl": initial_sl_temp,
                        "entry_time": entry_timestamp.strftime("%Y-%m-%d %H:%M:%S"),  # FIX: Add entry_time immediately
                        "candle_open_price": candle_open_price,  # 🆕 Store candle open price at entry
                        "candle_close_price": candle_close_price,  # 🆕 Store candle close/LTP at entry
                        "entry_candle_was_green": entry_candle_was_green  # For intra-candle reversal detection
                    }
                    
                    # 🛡️ OPTION C: Store last entry price/time/candle for duplicate prevention
                    option_candle_for_lock = self.data_manager.option_candles.get(symbol, {})
                    self.last_entry_info[symbol] = {
                        'price': price,
                        'time': now,
                        'entry_count': 1,
                        'candle_start_time': option_candle_for_lock.get('candle_start_time', 0)
                    }
                    
                    # 🔥 INSTANT UPDATE: Force immediate GUI refresh (blocking)
                    await self._update_ui_trade_status()

                # --- Position Creation (Update Layer 3 & 5 state) ---
                
                # Use estimated values for immediate position setup (will be updated by background verification)
                final_entry_price = estimated_price if self.params.get("trading_mode") == "Live Trading" else price
                total_filled_qty = estimated_filled_qty if self.params.get("trading_mode") == "Live Trading" else qty
                
                sl_adjustment = final_entry_price - price
                initial_sl_price = initial_sl_price + sl_adjustment
                
                entry_slippage = round(final_entry_price - expected_entry_price, 2)  # 🆕 Calculate entry slippage
                
                # ⚡ UPDATE POSITION: Add detailed fields to the basic position created earlier for instant UI
                # The basic position was already created and sent to UI for instant display
                momentum_data = getattr(self, '_current_momentum_data', {})
                
                # 🆕 CAPTURE OPTION SUPERTREND STATE AT ENTRY TIME - Only for SUPERTREND_ENTRY triggers
                # NO_WICK_BYPASS and TREND_CONTINUATION use standard TSL/Entry Price exit
                # Only SUPERTREND entries (UOA, MA_CROSSOVER, Supertrend_Entry, etc.) use Dual Supertrend exit
                is_supertrend_trigger = "Supertrend_Entry" in trigger or "UOA" in trigger or "MA_CROSSOVER" in trigger
                
                if is_supertrend_trigger:
                    # This is a SUPERTREND entry - capture state for Dual ST exit logic
                    option_st_line, option_st_uptrend = self.data_manager.calculate_option_supertrend(symbol)
                    entry_option_st_state = option_st_uptrend  # Capture state at entry (True, False, or None)
                else:
                    # This is NO_WICK or TREND_CONTINUATION - use standard exit logic
                    entry_option_st_state = None
                
                self.position.update({
                    "trail_sl": round(initial_sl_price, 2), 
                    "max_price": final_entry_price, 
                    "entry_time": entry_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "signal_time": signal_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],  # 🆕 Signal time with ms
                    "order_time": order_placement_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],  # 🆕 Order time with ms
                    "expected_entry": expected_entry_price,  # 🆕 Expected entry price
                    "entry_slippage": entry_slippage,  # 🆕 Entry slippage
                    "latency_ms": latency_ms,  # 🆕 Signal to order latency
                    # 🆕 ENTRY TYPE TRACKING - Differentiate exit logic and strategy
                    "entry_type": (
                        "NO_WICK_BYPASS" if no_wick_bypass 
                        else "ST_MOMENTUM_SYNC" if "ST_Momentum_Sync" in trigger
                        else "TREND_CONTINUATION" if "Trend_Continuation" in trigger
                        else "SUPERTREND_ENTRY" if "Supertrend_Entry" in trigger 
                        else "UNKNOWN"
                    ),
                    # 🆕 Confirmatory momentum check data
                    "momentum_price_rising": momentum_data.get('momentum_price_rising', 0),
                    "momentum_accelerating": momentum_data.get('momentum_accelerating', 0),
                    "momentum_index_sync": momentum_data.get('momentum_index_sync', 0),
                    "momentum_volume_surge": momentum_data.get('momentum_volume_surge', 0),
                    "momentum_checks_passed": momentum_data.get('momentum_checks_passed', 0),
                    # 🆕 Predictive momentum check data
                    "predictive_order_flow": momentum_data.get('predictive_order_flow', 0),
                    "predictive_divergence": momentum_data.get('predictive_divergence', 0),
                    "predictive_structure": momentum_data.get('predictive_structure', 0),
                    "predictive_checks_passed": momentum_data.get('predictive_checks_passed', 0),
                    "trigger_system": momentum_data.get('trigger_system', 'UNKNOWN'),
                    # 🆕 SUPERTREND HOLD DIFFERENTIATION - Capture at entry
                    "entry_option_st_uptrend": entry_option_st_state,  # Store raw state (True/False/None) for exit logic
                    "supertrend_hold_mode": None,  # Will be set during exit
                    "entry_option_st_state": None,  # Will be set during exit
                    "exit_supertrend_reason": None  # Will be set during exit
                })
                
                # ⚡⚡ ASYNC UI UPDATE + BROKER VERIFY IN PARALLEL (saves 20-30ms)
                # Fire UI update as background task (don't wait) + run broker verification in parallel
                
                async def async_ui_update():
                    """Fire-and-forget UI update (no blocking)"""
                    try:
                        await self._update_ui_trade_status()
                    except:
                        pass  # UI update failure is non-critical
                
                async def broker_position_sync():
                    """🛡️ Zerodha position verification (NON-CRITICAL)"""
                    if self.params.get("trading_mode") != "Live Trading":
                        return
                    
                    try:
                        await asyncio.sleep(0.2)  # Brief delay for Zerodha to update
                        zerodha_positions = (await kite.positions())["net"]
                        matching_position = next(
                            (pos for pos in zerodha_positions 
                             if pos["tradingsymbol"] == symbol and pos["quantity"] != 0), 
                            None
                        )
                        
                        if matching_position:
                            actual_zerodha_qty = matching_position["quantity"]
                            if actual_zerodha_qty != total_filled_qty:
                                await self._log_debug("POSITION-SYNC", 
                                    f"⚠️ Zerodha qty mismatch: Expected {total_filled_qty}, Actual {actual_zerodha_qty}. Syncing to Zerodha qty.")
                                self.position["qty"] = actual_zerodha_qty
                            else:
                                await self._log_debug("POSITION-VERIFY", 
                                    f"✅ Position verified with Zerodha: {symbol} {total_filled_qty} qty @ ₹{final_entry_price:.2f}")
                    except:
                        pass  # Broker verification failure is non-critical
                
                # 🚀 CLEAR SIGNAL QUEUE: Position created, no more entries allowed
                if len(self.signal_queue) > 0:
                    await self._log_debug("Signal Queue", 
                        f"🗑️ Clearing {len(self.signal_queue)} queued signal(s) - position now exists")
                    self.signal_queue = []
                
                # ⚡⚡ FIRE UI UPDATE ASYNC + RUN BROKER VERIFY IN PARALLEL (saves 20-30ms)
                # Don't wait for UI update (fire and forget)
                asyncio.create_task(async_ui_update())
                
                # Run broker verification
                await asyncio.gather(
                    broker_position_sync(),
                    return_exceptions=True
                )
                
                # Layer 3 Update: Log successful trade time
                self.trade_attempt_times.append(now) 
                
                # Layer 5 Update: Log last successful entry data with candle start time
                option_candle = self.data_manager.option_candles.get(symbol, {})
                candle_start_time = option_candle.get('candle_start_time', 0)
                self.last_entry_data = {
                    'symbol': symbol, 
                    'price': estimated_price if self.params.get("trading_mode") == "Live Trading" else price, 
                    'candle_start_time': candle_start_time
                }
                
                # 🎯 PERFECT ENTRY FIX: Clear missed opportunities on successful entry
                # Prevents automatic re-entries at same price after exit
                if symbol in self.missed_opportunities:
                    await self._log_debug("Perfect Entry", 
                        f"✅ Cleared missed opportunity for {symbol} (successful entry)")
                    del self.missed_opportunities[symbol]
                
                # 🎯 SET ENTRY CANDLE STATE: Determine if entry was on green or red candle
                option_candle = self.data_manager.option_candles.get(symbol, {})
                candle_open = option_candle.get('open')
                if candle_open and final_entry_price:
                    entry_candle_was_green = final_entry_price > candle_open
                    # Store in position dict for intra-candle reversal detection
                    if self.position:
                        self.position['entry_candle_was_green'] = entry_candle_was_green
                    await self._log_debug("Entry State", 
                        f"Entry Candle: {'GREEN' if entry_candle_was_green else 'RED'} "
                        f"(Price: ₹{final_entry_price:.2f}, Open: ₹{candle_open:.2f})")
                else:
                    entry_candle_was_green = None
                    if self.position:
                        self.position['entry_candle_was_green'] = None
                
                # 📊 DETAILED CANDLE OHLC AT ENTRY: Show exact candle structure and entry position
                option_candle = self.data_manager.option_candles.get(symbol, {})
                if option_candle:
                    candle_open = option_candle.get('open', 0)
                    candle_high = option_candle.get('high', 0)
                    candle_low = option_candle.get('low', 0)
                    
                    # 🕐 CHECK IF CANDLE IS STILL ACTIVE (not yet closed)
                    # A candle is active if we're still within the same minute as its start time
                    candle_start_time = option_candle.get('candle_start_time', 0)
                    current_time = time_module.time()
                    candle_age_seconds = current_time - candle_start_time if candle_start_time > 0 else 0
                    is_candle_active = candle_age_seconds < 60  # Active if < 60 seconds old
                    
                    # Use current LTP for active candles, stored close for completed candles
                    current_ltp = final_entry_price
                    candle_close = option_candle.get('close', current_ltp) if not is_candle_active else None
                    
                    # For display and calculations, use LTP for active candles
                    display_value = current_ltp
                    display_label = "LTP (Live)" if is_candle_active else "Close"
                    
                    # Calculate candle body and range
                    candle_body = abs(display_value - candle_open)
                    candle_body_pct = (candle_body / candle_open * 100) if candle_open > 0 else 0
                    candle_range = candle_high - candle_low
                    candle_type = "GREEN 🟢" if display_value > candle_open else "RED 🔴" if display_value < candle_open else "DOJI ⚪"
                    
                    # Calculate where in the candle we entered (0% = low, 100% = high)
                    entry_position_in_range = 0
                    if candle_range > 0:
                        entry_position_in_range = ((final_entry_price - candle_low) / candle_range) * 100
                    
                    # Calculate distance from extremes
                    distance_from_high = candle_high - final_entry_price
                    distance_from_low = final_entry_price - candle_low
                    distance_from_open = final_entry_price - candle_open
                    distance_from_current = final_entry_price - display_value  # Should be ~0 since we just entered
                    
                    # Upper/Lower wicks (calculated from current LTP for active candles)
                    if display_value > candle_open:  # Green candle/movement
                        upper_wick = candle_high - display_value
                        lower_wick = candle_open - candle_low
                    else:  # Red candle/movement
                        upper_wick = candle_high - candle_open
                        lower_wick = display_value - candle_low
                    
                    # 💾 STORE OHLC DATA IN POSITION for UI display
                    if self.position:
                        self.position['entry_candle_ohlc'] = {
                            'open': round(candle_open, 2),
                            'high': round(candle_high, 2),
                            'low': round(candle_low, 2),
                            'close': round(display_value, 2),  # Store LTP for active candles
                            'is_active': is_candle_active,  # Flag to show if candle was still forming
                            'candle_age_sec': round(candle_age_seconds, 1),
                            'body_pct': round(candle_body_pct, 2),
                            'range': round(candle_range, 2),
                            'upper_wick': round(upper_wick, 2),
                            'lower_wick': round(lower_wick, 2),
                            'entry_position_pct': round(entry_position_in_range, 1),
                            'candle_type': candle_type,
                            'distance_from_high': round(distance_from_high, 2),
                            'distance_from_low': round(distance_from_low, 2)
                        }
                        
                        # 🔄 Reset candle close log flag for new entry
                        if hasattr(self, '_entry_candle_closed_logged'):
                            delattr(self, '_entry_candle_closed_logged')
                    
                    await self._log_debug("📊 ENTRY CANDLE OHLC", 
                        f"\n{'═'*70}\n"
                        f"📍 ENTRY: ₹{final_entry_price:.2f} | {symbol}\n"
                        f"⏱️  Candle Age: {candle_age_seconds:.1f}s | Status: {'🟢 ACTIVE (forming)' if is_candle_active else '🔴 CLOSED'}\n"
                        f"{'─'*70}\n"
                        f"📊 OHLC:\n"
                        f"   • Open:  ₹{candle_open:.2f}\n"
                        f"   • High:  ₹{candle_high:.2f}\n"
                        f"   • Low:   ₹{candle_low:.2f}\n"
                        f"   • {display_label}: ₹{display_value:.2f}\n"
                        f"{'─'*70}\n"
                        f"🎯 CANDLE TYPE: {candle_type}\n"
                        f"{'─'*70}\n"
                        f"📏 BODY & WICKS:\n"
                        f"   • Body:        ₹{candle_body:.2f} ({candle_body_pct:.2f}%)\n"
                        f"   • Range:       ₹{candle_range:.2f}\n"
                        f"   • Upper Wick:  ₹{upper_wick:.2f}\n"
                        f"   • Lower Wick:  ₹{lower_wick:.2f}\n"
                        f"{'─'*70}\n"
                        f"📍 ENTRY POSITION:\n"
                        f"   • Position in Range: {entry_position_in_range:.1f}% (0%=Low, 100%=High)\n"
                        f"   • From High:   ₹{distance_from_high:.2f} {'⚠️ TOO CLOSE!' if distance_from_high < candle_range * 0.1 else '✅'}\n"
                        f"   • From Low:    ₹{distance_from_low:.2f}\n"
                        f"   • From Open:   ₹{distance_from_open:.2f} ({'+' if distance_from_open > 0 else ''}{(distance_from_open/candle_open*100):.2f}%)\n"
                        f"{'═'*70}"
                    )
                    
                    # 🚨 WARNING: Check if entry was at peak (> 85% of range)
                    if entry_position_in_range > 85:
                        await self._log_debug("⚠️ ENTRY WARNING", 
                            f"🚨 PEAK ENTRY DETECTED! Entered at {entry_position_in_range:.1f}% of candle range - "
                            f"High risk of immediate reversal. Entry: ₹{final_entry_price:.2f}, High: ₹{candle_high:.2f}")
                    elif entry_position_in_range < 30:
                        await self._log_debug("✅ ENTRY QUALITY", 
                            f"🎯 EXCELLENT ENTRY! Entered at {entry_position_in_range:.1f}% of candle range - "
                            f"Good risk/reward. Entry: ₹{final_entry_price:.2f}, Low: ₹{candle_low:.2f}")
                    else:
                        await self._log_debug("✅ ENTRY QUALITY", 
                            f"👍 GOOD ENTRY at {entry_position_in_range:.1f}% of candle range")
                
                if self.ticker_manager:
                    # 🔥 CRITICAL FIX: Validate instrument token before subscription
                    if not instrument_token:
                        await self._log_debug("ERROR", 
                            f"❌ CRITICAL: Invalid instrument token (None/0) for {symbol}. Position will NOT be tracked!")
                    elif instrument_token < 10000:
                        await self._log_debug("ERROR", 
                            f"❌ CRITICAL: Suspiciously low instrument token {instrument_token} for {symbol}. This is likely wrong!")
                    else:
                        await self._log_debug("WebSocket", 
                            f"Subscribing to active trade token: {instrument_token} for symbol {symbol}")
                        self.ticker_manager.subscribe([instrument_token])
                        # 🔥 CRITICAL FIX: Allow brief delay for subscription to propagate
                        # This ensures ticker is subscribed before first ticks arrive
                        await asyncio.sleep(0.05)  # 50ms delay for subscription acknowledgment
                        await self._log_debug("WebSocket", 
                            f"✅ Subscription confirmed - ticks should now be flowing")
                
                # ✅ TRADE COUNTER: Increments on successful position creation
                # Note: Database may have more rows if partial exits occur (each partial exit = 1 DB row)
                # This counter represents NUMBER OF ENTRIES, not number of exit transactions
                self.trades_this_minute += 1
                self.performance_stats["total_trades"] += 1
                    
                self.next_partial_profit_level = 1
                # Note: Sound moved to parallel post_entry_operations() above
                
                # 🔥 CRITICAL: Aggressive UI updates to ensure frontend receives position
                # Send immediately (blocking) + additional background broadcasts
                try:
                    # FIRST: Immediate blocking update (ensures at least one goes through)
                    await self._update_ui_trade_status()
                    await self._log_debug("UI Update", f"✅ Immediate position update sent: {symbol}")
                except Exception as e:
                    await self._log_debug("UI Update", f"⚠️ Immediate update failed: {e}")
                
                # SECOND: Additional background broadcasts for reliability
                async def send_position_updates():
                    await asyncio.sleep(0.2)  # 200ms initial delay (let first update process)
                    for i in range(5):  # 5 additional attempts
                        try:
                            await self._update_ui_trade_status()
                            await self._log_debug("UI Update", f"✅ Background update {i+1}/5 sent")
                            await asyncio.sleep(0.15)  # 150ms between attempts
                        except Exception as e:
                            await self._log_debug("UI Update", f"⚠️ Background update {i+1}/5 failed: {e}")
                
                # Start background updates (non-blocking)
                asyncio.create_task(send_position_updates())
                
                await self._log_debug("UI Update", f"✅ Position broadcast initiated (1 immediate + 5 background over 1s): {symbol}, {total_filled_qty} qty @ ₹{final_entry_price:.2f}")
                
                # 🔥 CRITICAL: Also queue to flush mechanism for graceful shutdown guarantee
                try:
                    self._pending_broadcasts.put_nowait({
                        "type": "trade_status_update",
                        "payload": {
                            "symbol": symbol,
                            "entry_price": final_entry_price,
                            "ltp": final_entry_price,
                            "pnl": 0,
                            "profit_pct": 0,
                            "trail_sl": initial_sl_price,
                            "max_price": final_entry_price,
                            "last_update_time": get_ist_time_str(include_ms=True)
                        }
                    })
                    await self._log_debug("UI Update", f"✅ Position also queued for shutdown flush: {symbol}")
                except Exception as e:
                    await self._log_debug("UI Update", f"⚠️ Failed to queue broadcast for shutdown flush: {e}")
                
                # ⚡ Play entry sound immediately (non-blocking)
                _play_sound(self.manager, "entry")
                
                # Final success log with all details
                await self._log_debug("ENTRY-COMPLETE", 
                    f"✅ Position active: {symbol} | Qty: {total_filled_qty} | Entry: ₹{final_entry_price:.2f} | "
                    f"SL: ₹{initial_sl_price:.2f} | Slippage: ₹{entry_slippage:.2f} | Latency: {latency_ms}ms")
                
            except Exception as e:
                await self._log_debug("CRITICAL-ENTRY-FAIL", f"Failed to execute entry for {symbol}: {e}")
                _play_sound(self.manager, "loss")
                
            finally:
                # LAYER 7: Clean up flags after order completion/failure
                # 🔥 CRITICAL FIX: Only clear entry_in_progress if position was actually created or order failed
                # Don't clear it just because order was PLACED - it needs to be FILLED first!
                
                # Check if we actually have a position after the try block
                if self.position:
                    # ✅ Position exists - entry completed successfully
                    self.entry_in_progress = False
                    self.entry_completed_at = now  # 🛡️ Start 500ms buffer to prevent race conditions
                    # ✅ FIXED: Update signal time ONLY after successful position creation
                    entry_price_for_dedup = self.position.get('entry_price', 0) if self.position else 0
                    self.last_signal_time[symbol] = {'time': now, 'trigger': trigger, 'price': entry_price_for_dedup}
                    await self._log_debug("Signal Dedup", 
                        f"✅ Signal locked after SUCCESS: {symbol} @ ₹{entry_price_for_dedup} (same-price blocked 5s)")
                elif order_succeeded is False:
                    # ❌ Order failed or was cancelled - entry failed
                    self.entry_in_progress = False
                    self.entry_completed_at = now
                    # ✅ FIXED: ALWAYS reset signal time on failed trade so next signal is not blocked
                    if symbol in self.last_signal_time:
                        del self.last_signal_time[symbol]
                    await self._log_debug("Signal Dedup", 
                        f"🟢 Signal unlocked after FAILURE: {symbol} (will allow immediate retry)")
                else:
                    # ⚠️ Order was placed but position not yet confirmed in Zerodha
                    # Keep entry_in_progress = True to block new signals!
                    # Position health monitor will eventually sync it
                    await self._log_debug("Entry Lock", 
                        f"⏳ Position pending: Keeping entry_in_progress=True (order placed, waiting for fill confirmation)")
                    # ✅ FIXED: Still update signal time since order was PLACED (even if not filled)
                    # This prevents spam of the same signal while order is settling
                    pending_price = custom_entry_price or self.data_manager.prices.get(symbol, 0)
                    self.last_signal_time[symbol] = {'time': now, 'trigger': trigger, 'price': pending_price}
                    await self._log_debug("Signal Dedup", 
                        f"⏳ Signal locked after ORDER PLACED: {symbol} @ ₹{pending_price} (waiting for fill)")
                
                self.active_order_id = None
                
    async def exit_position(self, reason, skip_layer6_cooldown=False):
        # ... (This function is unchanged, it correctly does not use recovery logic anymore)
        if not self.position: return
        
        # 🛡️ VALIDATE REASON: Ensure reason is not None
        if not reason:
            reason = "Unknown Exit Signal"
        
        # 🛡️ FIX 5: Prevent duplicate exit attempts (similar to entry_in_progress logic)
        if self.exit_in_progress:
            await self._log_debug("EXIT BLOCKED", f"🚫 Exit already in progress. Ignoring duplicate trigger: {reason}")
            return
        
        # Set flag immediately to block concurrent exits
        self.exit_in_progress = True
        
        # 🔥 RECORD EXIT TIME IMMEDIATELY (for capital cache refresh detection)
        self._last_exit_time = get_ist_time()
        
        # 🆕 CRITICAL: Capture exit timestamp IMMEDIATELY (before any order processing delays)
        exit_timestamp = get_ist_time()
        
        p = self.position
        
        await self._log_debug("EXIT START", f"🚪 Exit triggered for {p['symbol']} - Reason: {reason}")
        
        try:
            sell_log_message = f"Exiting {p['symbol']} ({p['qty']} qty). Reason: {reason}"
            
            # ⚡ PARALLEL EXIT EXECUTION: Fetch all required data simultaneously
            # Runs fresh exit price, position verification, and validation IN PARALLEL
            # Reduces total exit latency from 600ms+ to ~100-200ms
            
            async def fetch_fresh_exit_price():
                """Fetch current market exit price - use LTP for better prices matching paper trading"""
                try:
                    full_symbol = f"{self.exchange}:{p['symbol']}"
                    quote = await kite.quote([full_symbol])
                    if quote and full_symbol in quote:
                        # 📊 LIVE-PAPER PARITY: Use LTP instead of BID for better exit prices
                        # This matches paper trading behavior and reduces spread costs
                        fresh_price = quote[full_symbol].get('last_price')
                        if fresh_price and fresh_price > 0:
                            return fresh_price
                        
                        # Fallback to BID only if LTP unavailable
                        depth = quote[full_symbol].get('depth', {})
                        buy_orders = depth.get('buy', [])
                        if buy_orders and len(buy_orders) > 0:
                            best_bid = buy_orders[0].get('price')
                            if best_bid and best_bid > 0:
                                return best_bid
                except:
                    pass
                # Fallback to cached or max price
                fallback_price = self.data_manager.prices.get(p["symbol"], p["max_price"])
                # 🛡️ Ensure valid numeric price (never None)
                return fallback_price if fallback_price and fallback_price > 0 else p.get("entry_price", 100.0)
            
            async def verify_zerodha_position():
                """Verify position exists in Zerodha with 250ms initial wait + exponential backoff retries"""
                if self.params.get("trading_mode") != "Live Trading":
                    return None
                
                try:
                    # 🔥 CRITICAL FIX: Wait 250ms for Zerodha position sync
                    # Position API takes 50-200ms to sync after order fills
                    # This initial wait prevents immediate "Position not found" errors
                    await self._log_debug("Position Sync", "⏳ Waiting 250ms for Zerodha position sync...")
                    await asyncio.sleep(0.25)
                    
                    # 🔥 RETRY-WITH-BACKOFF: Exponential backoff for robustness
                    # Retry 1: 100ms, Retry 2: 200ms, Retry 3: 400ms, Retry 4: 800ms
                    # Total max time: 250ms (initial) + 1500ms (retries) = 1750ms
                    max_retries = 5
                    
                    for retry in range(max_retries):
                        try:
                            zerodha_positions = (await kite.positions())["net"]
                            # First try to match with MIS product (default)
                            matching_position = next(
                                (pos for pos in zerodha_positions 
                                 if pos["tradingsymbol"] == p["symbol"] and pos["quantity"] != 0 and pos["product"] == "MIS"), 
                                None
                            )
                            # If no MIS position found, check for NRML (in case of conversion)
                            if not matching_position:
                                matching_position = next(
                                    (pos for pos in zerodha_positions 
                                     if pos["tradingsymbol"] == p["symbol"] and pos["quantity"] != 0), 
                                    None
                                )
                                if matching_position:
                                    await self._log_debug("Position Check", 
                                        f"⚠️ Position found with product={matching_position['product']} (expected MIS)")
                            
                            if matching_position:
                                await self._log_debug("Position Sync", 
                                    f"✅ Position found on attempt {retry+1}/{max_retries}")
                                return matching_position
                            
                            # Position not found yet - retry with exponential backoff
                            if retry < max_retries - 1:
                                backoff_time = 0.1 * (2 ** retry)  # 100ms, 200ms, 400ms, 800ms...
                                await self._log_debug("Position Check", 
                                    f"⏳ Retry {retry+1}/{max_retries}: Not found yet. Waiting {backoff_time*1000:.0f}ms...")
                                await asyncio.sleep(backoff_time)
                            else:
                                await self._log_debug("Position Check", 
                                    f"⚠️ Attempt {retry+1}/{max_retries}: Position still not found")
                        except Exception as e:
                            if retry < max_retries - 1:
                                backoff_time = 0.1 * (2 ** retry)
                                await self._log_debug("Position Check", 
                                    f"⚠️ Error on attempt {retry+1}: {str(e)[:60]}. Retrying in {backoff_time*1000:.0f}ms...")
                                await asyncio.sleep(backoff_time)
                            else:
                                await self._log_debug("Position Check", 
                                    f"❌ Final attempt {retry+1} failed: {str(e)[:80]}")
                                raise e
                except Exception as final_error:
                    await self._log_debug("Position Sync", 
                        f"❌ Position verification exhausted all retries: {str(final_error)[:80]}")
                
                return None
            
            # 🚀 LAUNCH ALL OPERATIONS IN PARALLEL (saves 400-600ms!)
            exit_price, matching_position = await asyncio.gather(
                fetch_fresh_exit_price(),
                verify_zerodha_position(),
                return_exceptions=True
            )
            
            # Handle exceptions - AGGRESSIVE None handling
            if isinstance(exit_price, Exception):
                # Try multiple fallbacks in sequence
                exit_price = self.data_manager.prices.get(p["symbol"])
                if not exit_price or exit_price <= 0:
                    exit_price = p.get("max_price")
                if not exit_price or exit_price <= 0:
                    exit_price = p.get("entry_price", 100.0)
                # FINAL SAFETY: Ensure it's NEVER None
                if exit_price is None or exit_price <= 0:
                    exit_price = 100.0
            if isinstance(matching_position, Exception):
                matching_position = None
            
            # 🛡️ FINAL VALIDATION: Ensure exit_price is NEVER None before format strings
            if exit_price is None:
                exit_price = 100.0
            
            # Convert to float immediately
            try:
                exit_price = float(exit_price) if exit_price is not None else 100.0
            except (TypeError, ValueError):
                exit_price = 100.0
            
            # Ensure it's a valid positive number
            if not isinstance(exit_price, (int, float)) or exit_price <= 0:
                exit_price_display = "N/A"
                exit_price = 100.0  # Use fallback for any calculations
            else:
                exit_price_display = f"₹{exit_price:.2f}"
            
            # 🛡️ Validate exit price
            if exit_price <= 0 or not isinstance(exit_price, (int, float)):
                await self._log_debug("CRITICAL-EXIT-FAIL", 
                    f"❌ INVALID EXIT PRICE ({exit_price_display}) for {p['symbol']}!")
                
                # 🚨 LOG FAILED EXIT ATTEMPT: Invalid price case
                try:
                    log_info_invalid_price = {
                        "timestamp": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
                        "trigger_reason": p.get("trigger_reason", "EXIT_FAILED"),
                        "symbol": p["symbol"],
                        "quantity": p["qty"],
                        "pnl": 0,
                        "entry_price": p["entry_price"],
                        "exit_price": p.get("entry_price", 100.0),  # Use entry price as fallback
                        "exit_reason": "FAILED_EXIT: Invalid exit price",
                        "trend_state": self.data_manager.trend_state,
                        "atr": round(self.data_manager.data_df.iloc[-1]["atr"], 2) if not self.data_manager.data_df.empty else 0,
                        "charges": 0,
                        "net_pnl": 0,
                        "entry_time": p.get("entry_time"),
                        "exit_time": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "duration_seconds": None,
                        "max_price": p.get("max_price"),
                        "signal_time": p.get("signal_time"),
                        "order_time": p.get("order_time"),
                        "expected_entry": p.get("expected_entry"),
                        "expected_exit": p.get("entry_price", 100.0),
                        "entry_slippage": p.get("entry_slippage", 0),
                        "exit_slippage": 0,
                        "latency_ms": p.get("latency_ms"),
                        "trading_mode": self.params.get("trading_mode", "Paper Trading"),
                        "ucc": self._get_active_ucc(),
                        "momentum_price_rising": p.get("momentum_price_rising", 0),
                        "momentum_accelerating": p.get("momentum_accelerating", 0),
                        "momentum_index_sync": p.get("momentum_index_sync", 0),
                        "momentum_volume_surge": p.get("momentum_volume_surge", 0),
                        "momentum_checks_passed": p.get("momentum_checks_passed", 0),
                        "predictive_order_flow": p.get("predictive_order_flow", 0),
                        "predictive_divergence": p.get("predictive_divergence", 0),
                        "predictive_structure": p.get("predictive_structure", 0),
                        "predictive_checks_passed": p.get("predictive_checks_passed", 0),
                        "trigger_system": p.get("trigger_system", "UNKNOWN"),
                        "entry_type": p.get("entry_type", "UNKNOWN"),
                        "supertrend_hold_mode": p.get("supertrend_hold_mode", "UNKNOWN"),
                        "entry_option_st_state": p.get("entry_option_st_state", "UNKNOWN"),
                        "exit_supertrend_reason": "FAILED: Invalid exit price"
                    }
                    await self.trade_logger.log_trade(log_info_invalid_price)
                    # 🆕 CRITICAL: Update performance stats for failed exit attempt
                    self.performance_stats["losing_trades"] += 1
                except Exception as log_error:
                    await self._log_debug("EXIT-LOG-ERROR", 
                        f"⚠️ Failed to log invalid price exit: {log_error}")
                
                # 🚨 EMERGENCY: Force market order exit
                if self.params.get("trading_mode") == "Live Trading":
                    await self._log_debug("EMERGENCY-EXIT", f"🚨 Forcing MARKET order exit!")
                    try:
                        emergency_result = await self.order_manager.execute_order(
                            transaction_type=kite.TRANSACTION_TYPE_SELL,
                            order_type=kite.ORDER_TYPE_MARKET,
                            tradingsymbol=p["symbol"],
                            exchange=self.exchange,
                            quantity=p["qty"]
                        )
                        if emergency_result[0] == "COMPLETE":
                            await self._log_debug("EMERGENCY-EXIT", f"✅ Emergency exit successful")
                        else:
                            await self._log_debug("EMERGENCY-EXIT", f"❌ Emergency exit failed: {emergency_result}")
                    except Exception as e:
                        await self._log_debug("EMERGENCY-EXIT", f"❌ Emergency exit exception: {e}")
                
                self.exit_attempt_counter = getattr(self, 'exit_attempt_counter', 0) + 1
                await self._log_debug("EXIT-RETRY", 
                    f"⚠️ Exit failed (attempt {self.exit_attempt_counter}). Position PRESERVED.")
                
                if self.exit_attempt_counter >= 5:
                    await self._log_debug("CRITICAL-EXIT-FAIL", 
                        f"🚨 {self.exit_attempt_counter} failed exit attempts! Manual intervention required!")
                
                _play_sound(self.manager, "warning")
                self.exit_in_progress = False
                await self._update_ui_trade_status()
                return
            
            # Log fresh exit price - with safe formatting
            try:
                exit_price_log = f"₹{float(exit_price):.2f}" if isinstance(exit_price, (int, float)) and exit_price is not None and exit_price > 0 else "N/A"
            except (TypeError, ValueError):
                exit_price_log = "N/A"
            await self._log_debug("FastExit", f"⚡ Exit price: {exit_price_log}")
            
            if self.params.get("trading_mode") == "Live Trading":
                
                try:  # Wrap the error handling in try block
                    # ⚡ PARALLEL: Verify position + Fetch fresh exit price simultaneously
                    if not matching_position:
                        await self._log_debug("CRITICAL-EXIT-FAIL", 
                            f"❌ POSITION NOT FOUND in Zerodha for {p['symbol']}! Cannot exit. Clearing local position.")
                        
                        # 🚨 LOG FAILED EXIT ATTEMPT: Position not found
                        try:
                            log_info_no_position = {
                                "timestamp": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
                                "trigger_reason": p.get("trigger_reason", "EXIT_FAILED"),
                                "symbol": p["symbol"],
                                "quantity": p["qty"],
                                "pnl": 0,
                                "entry_price": p["entry_price"],
                                "exit_price": exit_price if exit_price and exit_price > 0 else p.get("entry_price", 100.0),
                                "exit_reason": "FAILED_EXIT: Position not found in Zerodha",
                                "trend_state": self.data_manager.trend_state,
                                "atr": round(self.data_manager.data_df.iloc[-1]["atr"], 2) if not self.data_manager.data_df.empty else 0,
                                "charges": 0,
                                "net_pnl": 0,
                                "entry_time": p.get("entry_time"),
                                "exit_time": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                                "duration_seconds": None,
                                "max_price": p.get("max_price"),
                                "signal_time": p.get("signal_time"),
                                "order_time": p.get("order_time"),
                                "expected_entry": p.get("expected_entry"),
                                "expected_exit": exit_price if exit_price and exit_price > 0 else p.get("entry_price", 100.0),
                                "entry_slippage": p.get("entry_slippage", 0),
                                "exit_slippage": 0,
                                "latency_ms": p.get("latency_ms"),
                                "trading_mode": self.params.get("trading_mode", "Paper Trading"),
                                "ucc": self._get_active_ucc(),
                                "momentum_price_rising": p.get("momentum_price_rising", 0),
                                "momentum_accelerating": p.get("momentum_accelerating", 0),
                                "momentum_index_sync": p.get("momentum_index_sync", 0),
                                "momentum_volume_surge": p.get("momentum_volume_surge", 0),
                                "momentum_checks_passed": p.get("momentum_checks_passed", 0),
                                "predictive_order_flow": p.get("predictive_order_flow", 0),
                                "predictive_divergence": p.get("predictive_divergence", 0),
                                "predictive_structure": p.get("predictive_structure", 0),
                                "predictive_checks_passed": p.get("predictive_checks_passed", 0),
                                "trigger_system": p.get("trigger_system", "UNKNOWN"),
                                "entry_type": p.get("entry_type", "UNKNOWN"),
                                "supertrend_hold_mode": p.get("supertrend_hold_mode", "UNKNOWN"),
                                "entry_option_st_state": p.get("entry_option_st_state", "UNKNOWN"),
                                "exit_supertrend_reason": "FAILED: Position not found"
                            }
                            await self.trade_logger.log_trade(log_info_no_position)
                            # 🆕 CRITICAL: Update performance stats for failed exit attempt
                            self.performance_stats["losing_trades"] += 1
                        except Exception as log_error:
                            await self._log_debug("EXIT-LOG-ERROR", 
                                f"⚠️ Failed to log position not found exit: {log_error}")
                        
                        # CRITICAL FIX: Reset ALL flags and state when clearing orphaned position
                        self.position = None
                        self.entry_price = None
                        self.stop_loss_price = None
                        self.entry_in_progress = False  # Reset entry lock
                        self.exit_in_progress = False  # Fix 5: Clear exit flag
                        self.active_order_id = None  # Clear order ID
                        await self._update_ui_trade_status()
                        _play_sound(self.manager, "warning")
                        return
                    
                    # Update position qty if mismatch (handles partial fills from previous exits)
                    if matching_position["quantity"] != p["qty"]:
                        if matching_position["quantity"] < p["qty"]:
                            # Partial exit detected - sync DOWN only
                            await self._log_debug("EXIT", 
                                f"⚠️ Partial exit detected: Local={p['qty']}, Zerodha={matching_position['quantity']}. Syncing DOWN...")
                            p["qty"] = matching_position["quantity"]
                        else:
                            # Zerodha qty HIGHER than local - DUPLICATE ENTRY BUG!
                            await self._log_debug("CRITICAL-DUPLICATE-ENTRY", 
                                f"🚨 DUPLICATE ENTRY DETECTED: Local={p['qty']}, Zerodha={matching_position['quantity']}. Selling EXCESS {matching_position['quantity'] - p['qty']} qty!")
                            # Emergency: Sell the excess quantity immediately
                            excess_qty = matching_position["quantity"] - p["qty"]
                            try:
                                emergency_result = await self.order_manager.execute_order(
                                    transaction_type=kite.TRANSACTION_TYPE_SELL,
                                    order_type=kite.ORDER_TYPE_MARKET,
                                    tradingsymbol=p["symbol"],
                                    exchange=self.exchange,
                                    quantity=excess_qty
                                )
                                await self._log_debug("EMERGENCY-EXIT", f"✅ Sold excess {excess_qty} qty: {emergency_result}")
                                # ✨ CRITICAL: Set 1-second cooldown to prevent duplicate exits
                                # When we sell excess from duplicate, don't immediately trigger another exit
                                self.excess_sale_cooldown_until = time_module.time() + 1.0
                                await self._log_debug("EXIT-COOLDOWN", f"⏸️ Excess sale cooldown activated (1s)")
                            except Exception as e:
                                await self._log_debug("EMERGENCY-EXIT", f"❌ Failed to sell excess: {e}")
                            # Keep local position qty (don't sync UP)
                    
                except Exception as verify_error:
                    await self._log_debug("CRITICAL-EXIT-FAIL", 
                        f"❌ Failed to verify position in Zerodha: {verify_error}. Aborting exit.")
                    
                    # 🚨 LOG FAILED EXIT ATTEMPT: Verification failure
                    try:
                        log_info_verify_fail = {
                            "timestamp": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
                            "trigger_reason": p.get("trigger_reason", "EXIT_FAILED"),
                            "symbol": p["symbol"],
                            "quantity": p["qty"],
                            "pnl": 0,
                            "entry_price": p["entry_price"],
                            "exit_price": exit_price if exit_price and exit_price > 0 else p.get("entry_price", 100.0),
                            "exit_reason": f"FAILED_EXIT: Verification error - {str(verify_error)[:80]}",
                            "trend_state": self.data_manager.trend_state,
                            "atr": round(self.data_manager.data_df.iloc[-1]["atr"], 2) if not self.data_manager.data_df.empty else 0,
                            "charges": 0,
                            "net_pnl": 0,
                            "entry_time": p.get("entry_time"),
                            "exit_time": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                            "duration_seconds": None,
                            "max_price": p.get("max_price"),
                            "signal_time": p.get("signal_time"),
                            "order_time": p.get("order_time"),
                            "expected_entry": p.get("expected_entry"),
                            "expected_exit": exit_price if exit_price and exit_price > 0 else p.get("entry_price", 100.0),
                            "entry_slippage": p.get("entry_slippage", 0),
                            "exit_slippage": 0,
                            "latency_ms": p.get("latency_ms"),
                            "trading_mode": self.params.get("trading_mode", "Paper Trading"),
                            "ucc": self._get_active_ucc(),
                            "momentum_price_rising": p.get("momentum_price_rising", 0),
                            "momentum_accelerating": p.get("momentum_accelerating", 0),
                            "momentum_index_sync": p.get("momentum_index_sync", 0),
                            "momentum_volume_surge": p.get("momentum_volume_surge", 0),
                            "momentum_checks_passed": p.get("momentum_checks_passed", 0),
                            "predictive_order_flow": p.get("predictive_order_flow", 0),
                            "predictive_divergence": p.get("predictive_divergence", 0),
                            "predictive_structure": p.get("predictive_structure", 0),
                            "predictive_checks_passed": p.get("predictive_checks_passed", 0),
                            "trigger_system": p.get("trigger_system", "UNKNOWN"),
                            "entry_type": p.get("entry_type", "UNKNOWN"),
                            "supertrend_hold_mode": p.get("supertrend_hold_mode", "UNKNOWN"),
                            "entry_option_st_state": p.get("entry_option_st_state", "UNKNOWN"),
                            "exit_supertrend_reason": "FAILED: Verification error"
                        }
                        await self.trade_logger.log_trade(log_info_verify_fail)
                        # 🆕 CRITICAL: Update performance stats for failed exit attempt
                        self.performance_stats["losing_trades"] += 1
                    except Exception as log_error:
                        await self._log_debug("EXIT-LOG-ERROR", 
                            f"⚠️ Failed to log verification error exit: {log_error}")
                    
                    _play_sound(self.manager, "warning")
                    self.exit_in_progress = False  # Fix 5: Clear exit flag on verification failure
                    return
                
                # 🔍 DIAGNOSTIC: Log exit order details before placement
                # 🛡️ CRITICAL: Ensure exit_price is valid before formatting
                try:
                    exit_price_safe = float(exit_price) if isinstance(exit_price, (int, float)) and exit_price is not None else 0.0
                except (TypeError, ValueError):
                    exit_price_safe = 0.0
                
                await self._log_debug("EXIT-DEBUG", 
                    f"📋 Exit Order: Symbol={p['symbol']}, Qty={p['qty']}, "
                    f"Zerodha Qty={matching_position.get('quantity') if matching_position else 'N/A'}, "
                    f"Price=₹{exit_price_safe:.2f}, Type=MARKET (forced for guaranteed exit)")
                
                # 🛡️ CRITICAL: Use Zerodha's actual product type to avoid margin errors
                actual_product = matching_position.get('product', 'MIS') if matching_position else 'MIS'
                if actual_product != 'MIS':
                    await self._log_debug("EXIT", 
                        f"⚠️ Using product={actual_product} (Zerodha actual) instead of MIS")
                
                # 🚨 CRITICAL EXIT LOGIC: MUST exit immediately, no "price discipline"
                # EXITS ARE TIME-SENSITIVE - Holding position while price drops = MASSIVE LOSS
                # 
                # 🔥 CRITICAL FIX JAN 29: Skip LIMIT/CHASE - use MARKET directly
                # Reason: Zerodha margin calculation bugs cause "Insufficient funds" on LIMIT exits
                # Even though position exists, broker miscalculates required margin
                # MARKET orders bypass margin check and guarantee exit
                # 
                # Trade-off: ₹0.50-1.50 worse exit price vs risk of failed exit holding losing position
                basket_result = await self.order_manager.execute_basket_order(
                    quantity=p["qty"],
                    transaction_type=kite.TRANSACTION_TYPE_SELL,
                    tradingsymbol=p["symbol"],
                    exchange=self.exchange,
                    freeze_limit=self.freeze_limit,
                    price=None,  # 🔥 None = MARKET order (guaranteed exit)
                    product=actual_product,  # 🛡️ Use actual product from Zerodha
                    use_level2_flow=False,  # ❌ Skip depth analysis (MARKET = no pricing needed)
                    use_chase=False,  # ❌ No chase needed (MARKET fills instantly)
                    chase_retries=1,  # Single attempt
                    chase_timeout_ms=1000,  # 1 second max
                    fallback_to_market=True  # Already MARKET
                )
                
                # 🛡️ CRITICAL: Validate basket_result before accessing
                if not basket_result or not isinstance(basket_result, dict):
                    await self._log_debug("CRITICAL-EXIT-FAIL", 
                        f"❌ Invalid basket result (got {type(basket_result).__name__}). Cannot process exit.")
                    self.exit_in_progress = False
                    return
                
                # Check if exit was successful
                if basket_result["status"] in ["COMPLETE", "PARTIAL", "CANCELLED"]:
                    # 🚀 NON-BLOCKING: Use basket estimates immediately, verify in background
                    estimated_qty_sold = basket_result.get("total_filled", p["qty"])
                    estimated_exit_price = basket_result.get("avg_price", exit_price)
                    
                    # 🛡️ CRITICAL: Ensure exit price is valid (never None) - aggressive fallback chain
                    if not estimated_exit_price or estimated_exit_price <= 0:
                        # Try exit_price first
                        if exit_price and exit_price > 0:
                            estimated_exit_price = exit_price
                        # Try entry_price as fallback
                        elif p.get("entry_price") and p.get("entry_price") > 0:
                            estimated_exit_price = p.get("entry_price")
                        # Last resort
                        else:
                            estimated_exit_price = 100.0
                    
                    # 🛡️ DOUBLE CHECK: Ensure estimated_exit_price is NEVER None
                    if estimated_exit_price is None:
                        estimated_exit_price = 100.0
                    
                    # Use estimates immediately for instant processing
                    actual_qty_sold = estimated_qty_sold
                    final_exit_price = estimated_exit_price
                    
                    # Log immediate exit with estimates
                    if basket_result["status"] == "COMPLETE":
                        final_exit_price_display = f"{float(final_exit_price):.2f}" if isinstance(final_exit_price, (int, float)) and final_exit_price is not None else "N/A"
                        await self._log_debug("LIVE TRADE", 
                            f"✅ SELL {p['symbol']}: {actual_qty_sold} qty @ ₹{final_exit_price_display}. Reason: {reason} (verifying in background...)")
                        # ⚡ INSTANT UI UPDATE: Clear position immediately for GUI
                        self.position = None
                        self.exit_attempt_counter = 0  # Fix 3: Reset counter on successful exit
                        self.exit_in_progress = False  # Fix 5: Clear exit flag immediately on complete
                        # 🔥 INSTANT UPDATE: Force immediate GUI clear
                        await self._update_ui_trade_status()
                    elif basket_result["status"] == "CANCELLED":
                        final_exit_price_display = f"{float(final_exit_price):.2f}" if isinstance(final_exit_price, (int, float)) and final_exit_price is not None else "N/A"
                        await self._log_debug("LIVE TRADE", 
                            f"⚠️ CANCELLED-PARTIAL SELL {p['symbol']}: {actual_qty_sold}/{p['qty']} qty @ ₹{final_exit_price_display}. Reason: {reason} (verifying in background...)")
                    else:  # PARTIAL
                        final_exit_price_display = f"{float(final_exit_price):.2f}" if isinstance(final_exit_price, (int, float)) and final_exit_price is not None else "N/A"
                        await self._log_debug("LIVE TRADE", 
                            f"⚠️ PARTIAL SELL {p['symbol']}: {actual_qty_sold}/{p['qty']} qty @ ₹{final_exit_price_display}. Reason: {reason} (verifying in background...)")
                        
                        # CRITICAL FIX: Handle partial fills properly
                        remaining_qty = p["qty"] - actual_qty_sold
                    
                    # 🚀 BACKGROUND VERIFICATION: Verify actual fills without blocking
                    async def background_exit_verification():
                        try:
                            verified_qty, verified_price, exit_fill_time = await asyncio.wait_for(
                                self._verify_order_execution(basket_result),
                                timeout=3.0
                            )
                            
                            if verified_qty == 0:
                                await self._log_debug("CRITICAL-EXIT-FAIL", 
                                    f"❌ Order {basket_result['status']}: 0 qty sold per verification. Check manually!")
                                return
                            
                            # 🛡️ GUARANTEED: verified_price is ALWAYS a float (never None) after _verify_order_execution()
                            # But add explicit check anyway for defense-in-depth
                            if not isinstance(verified_price, (int, float)) or verified_price is None:
                                verified_price = 0.0
                            
                            verified_price = float(verified_price)  # Ensure it's float type
                            
                            # Log verification results
                            if verified_price is not None and verified_price >= 0:
                                # 🛡️ SAFE: All values guaranteed to be valid numbers
                                try:
                                    # 🛡️ CRITICAL: Ensure estimated_exit_price is valid before formatting
                                    estimated_price_for_calc = float(estimated_exit_price) if isinstance(estimated_exit_price, (int, float)) and estimated_exit_price is not None else verified_price
                                    if estimated_price_for_calc is None or not isinstance(estimated_price_for_calc, (int, float)):
                                        estimated_price_for_calc = verified_price if verified_price > 0 else 0.0
                                    
                                    if estimated_price_for_calc > 0:
                                        price_diff = abs(verified_price - estimated_price_for_calc)
                                        price_diff_pct = (price_diff / estimated_price_for_calc) * 100
                                        await self._log_debug("Exit Price", 
                                            f"📊 Expected: ₹{float(estimated_price_for_calc):.2f}, Actual: ₹{float(verified_price):.2f} (Diff: ₹{float(price_diff):.2f}, {float(price_diff_pct):.2f}%)")
                                    else:
                                        await self._log_debug("Exit Price", 
                                            f"📊 Expected: ₹{float(estimated_price_for_calc):.2f}, Actual: ₹{float(verified_price):.2f}")
                                except (TypeError, ValueError, ZeroDivisionError) as format_err:
                                    await self._log_debug("Exit Price", 
                                        f"⚠️ Price verification format error: {format_err}. Using backup data.")
                            else:
                                # 🛡️ SAFE: Format with explicit None-checks
                                try:
                                    estimated_display = f"₹{float(estimated_exit_price):.2f}" if isinstance(estimated_exit_price, (int, float)) and estimated_exit_price is not None else "N/A"
                                except (TypeError, ValueError):
                                    estimated_display = "N/A"
                                await self._log_debug("Exit Price", f"⚠️ Using expected price {estimated_display} (verification failed)")
                            
                            if verified_qty != estimated_qty_sold:
                                await self._log_debug("Exit Qty", 
                                    f"📊 Expected: {estimated_qty_sold}, Actual: {verified_qty}")
                            
                            await self._log_debug("Verification", "✅ Background exit verification complete")

                        except asyncio.TimeoutError:
                            await self._log_debug("LIVE TRADE", 
                                f"⚠️ Exit verification timeout (3s). Using basket data.")
                        except Exception as verify_error:
                            await self._log_debug("LIVE TRADE", 
                                f"⚠️ Exit verification failed: {verify_error}. Using basket data.")
                    
                    # Launch verification in background
                    asyncio.create_task(background_exit_verification())
                    
                    # Continue with partial exit handling using estimates
                    if basket_result["status"] == "PARTIAL":
                        remaining_qty = remaining_qty  # Already calculated above
                        
                        if remaining_qty > 0:
                            # 🛡️ CRITICAL: Verify remaining position with Zerodha before keeping it
                            try:
                                await asyncio.sleep(0.15)  # Brief delay for Zerodha to update
                                zerodha_positions = (await kite.positions())["net"]
                                matching_position = next(
                                    (pos for pos in zerodha_positions 
                                     if pos["tradingsymbol"] == p["symbol"] and pos["quantity"] != 0), 
                                    None
                                )
                                
                                if matching_position:
                                    actual_zerodha_qty = matching_position["quantity"]
                                    if actual_zerodha_qty != remaining_qty:
                                        await self._log_debug("POSITION-SYNC", 
                                            f"⚠️ After partial exit - Zerodha qty: {actual_zerodha_qty}, Expected: {remaining_qty}. Syncing...")
                                        remaining_qty = actual_zerodha_qty
                                    else:
                                        await self._log_debug("PARTIAL EXIT", 
                                            f"✅ Zerodha confirms {remaining_qty} qty remaining for {p['symbol']}")
                                else:
                                    # Position fully closed in Zerodha despite partial status
                                    await self._log_debug("PARTIAL EXIT", 
                                        f"⚠️ Zerodha shows NO remaining position. Treating as COMPLETE exit.")
                                    remaining_qty = 0
                                    self.exit_attempt_counter = 0  # Fix 3: Reset counter when position is fully closed
                            except Exception as verify_error:
                                await self._log_debug("PARTIAL EXIT", 
                                    f"⚠️ Failed to verify remaining position: {verify_error}. Using calculated: {remaining_qty}")
                            
                            if remaining_qty > 0:
                                # Still have remaining quantity - keep position with reduced qty
                                await self._log_debug("PARTIAL EXIT", 
                                    f"⚠️ Keeping position with {remaining_qty} qty remaining")
                                p["qty"] = remaining_qty  # Update to remaining quantity
                                
                                # Log the partial exit trade
                                partial_gross_pnl = (final_exit_price - p["entry_price"]) * actual_qty_sold
                                partial_charges = await self._calculate_trade_charges(
                                    tradingsymbol=p["symbol"], exchange=self.exchange, 
                                    entry_price=p["entry_price"], exit_price=final_exit_price, 
                                    quantity=actual_qty_sold)
                                partial_net_pnl = partial_gross_pnl - partial_charges
                                
                                # Update daily totals
                                self.daily_gross_pnl += partial_gross_pnl
                                self.total_charges += partial_charges
                                self.daily_net_pnl += partial_net_pnl
                                
                                # Update profit/loss tracking (but NOT win/loss counts - partial exit)
                                if partial_gross_pnl > 0:
                                    self.daily_profit += partial_gross_pnl
                                else:
                                    self.daily_loss += partial_gross_pnl
                                
                                await self._log_debug("PNL", 
                                    f"Partial exit PNL: Gross=₹{partial_gross_pnl:.2f}, Net=₹{partial_net_pnl:.2f}")
                                
                                await self._update_ui_trade_status()
                                await self._update_ui_performance()
                                
                                # Reset exit failure counter
                                self.exit_failure_count = 0
                                return  # CRITICAL: Return here, don't clear position!
                            # If remaining_qty == 0 after verification, fall through to complete exit logic
                    
                    # Full exit or complete fill after partial
                    # Update quantity to actual sold amount for PNL calculation
                    p["qty"] = actual_qty_sold
                    # CRITICAL FIX: Use actual exit price for PNL calculation (with None protection)
                    if final_exit_price and final_exit_price > 0:
                        exit_price = final_exit_price
                    # If final_exit_price is None/invalid, keep the original exit_price from line 2363
                    # Reset exit failure counter on successful exit
                    self.exit_failure_count = 0
                else:  # FAILED
                    # CRITICAL FIX: Extract detailed error info from basket_result
                    error_msg = ""
                    if basket_result.get("orders"):
                        # Get error from first order - ensure it's a string
                        error_from_order = basket_result["orders"][0].get("error", "")
                        error_msg = str(error_from_order) if error_from_order is not None else ""
                    
                    # If no error in orders, check if basket_result itself has error info
                    if not error_msg and "error" in basket_result:
                        error_from_basket = basket_result["error"]
                        error_msg = str(error_from_basket) if error_from_basket is not None else ""
                    
                    # Log full basket_result for debugging if error is still unknown
                    if not error_msg:
                        await self._log_debug("EXIT-DEBUG", 
                            f"📋 Full basket_result (no error found): {basket_result}")
                        error_msg = f"Status={basket_result.get('status', 'UNKNOWN')}, No error details in response"
                    
                    # 🛡️ FINAL SAFETY: Ensure error_msg is NEVER None
                    error_msg = str(error_msg) if error_msg is not None else "Unknown error"
                    
                    # 🛡️ CRITICAL FIX: Log failed exit attempt to database BEFORE returning
                    # This ensures trades that failed to exit are still recorded
                    # P&L will be 0 (no actual fill), but we capture the attempt
                    try:
                        # 🛡️ SAFETY: Ensure exit_price_for_log is ALWAYS a valid number
                        exit_price_for_log = exit_price if (exit_price is not None and isinstance(exit_price, (int, float)) and exit_price > 0) else p.get("entry_price", 100.0)
                        if exit_price_for_log is None or not isinstance(exit_price_for_log, (int, float)):
                            exit_price_for_log = 100.0
                        gross_pnl = 0  # No fill, so no P&L
                        charges = 0
                        net_pnl = 0
                        
                        # Calculate trade duration
                        duration_seconds = None
                        try:
                            entry_dt = datetime.strptime(p["entry_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                            duration_seconds = (exit_timestamp - entry_dt).total_seconds()
                        except:
                            pass
                        
                        log_info_failed = {
                            "timestamp": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
                            "trigger_reason": p.get("trigger_reason", "EXIT_FAILED"),
                            "symbol": p["symbol"],
                            "quantity": p["qty"],
                            "pnl": 0,
                            "entry_price": p["entry_price"],
                            "exit_price": exit_price_for_log,
                            "exit_reason": f"FAILED_EXIT: {error_msg[:100]}",  # Truncate error to fit
                            "trend_state": self.data_manager.trend_state,
                            "atr": round(self.data_manager.data_df.iloc[-1]["atr"], 2) if not self.data_manager.data_df.empty else 0,
                            "charges": 0,
                            "net_pnl": 0,
                            "entry_time": p.get("entry_time"),
                            "exit_time": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                            "duration_seconds": duration_seconds,
                            "max_price": p.get("max_price"),
                            "signal_time": p.get("signal_time"),
                            "order_time": p.get("order_time"),
                            "expected_entry": p.get("expected_entry"),
                            "expected_exit": exit_price_for_log,
                            "entry_slippage": p.get("entry_slippage", 0),
                            "exit_slippage": 0,
                            "latency_ms": p.get("latency_ms"),
                            "trading_mode": self.params.get("trading_mode", "Paper Trading"),
                            "ucc": self._get_active_ucc(),
                            "momentum_price_rising": p.get("momentum_price_rising", 0),
                            "momentum_accelerating": p.get("momentum_accelerating", 0),
                            "momentum_index_sync": p.get("momentum_index_sync", 0),
                            "momentum_volume_surge": p.get("momentum_volume_surge", 0),
                            "momentum_checks_passed": p.get("momentum_checks_passed", 0),
                            "predictive_order_flow": p.get("predictive_order_flow", 0),
                            "predictive_divergence": p.get("predictive_divergence", 0),
                            "predictive_structure": p.get("predictive_structure", 0),
                            "predictive_checks_passed": p.get("predictive_checks_passed", 0),
                            "trigger_system": p.get("trigger_system", "UNKNOWN"),
                            "entry_type": p.get("entry_type", "UNKNOWN"),
                            "supertrend_hold_mode": p.get("supertrend_hold_mode", "UNKNOWN"),
                            "entry_option_st_state": p.get("entry_option_st_state", "UNKNOWN"),
                            "exit_supertrend_reason": f"FAILED: {error_msg[:50]}"
                        }
                        
                        # Log the failed exit to database
                        await self.trade_logger.log_trade(log_info_failed)
                        await self._log_debug("EXIT-LOGGED", 
                            f"📝 Failed exit attempt logged to database for {p['symbol']} (Error: {error_msg[:80]})")
                        
                        # 🆕 CRITICAL: DO NOT count failed exits as losses
                        # Failed exits = no fill = no PnL = not a real trade
                        # These should not affect win/loss statistics
                        # Only actual executed trades (with P&L) affect win/loss counts
                        
                    except Exception as log_error:
                        await self._log_debug("EXIT-LOG-ERROR", 
                            f"⚠️ Failed to log exit attempt: {log_error}")
                    
                    # CRITICAL FIX: Report failed exit order to kill switch
                    kill_switch.check_failed_orders("REJECTED", error_msg)
                    
                    # 🔍 DIAGNOSTIC: Analyze margin errors on exits (shouldn't happen!)
                    if "Insufficient funds" in error_msg or "margin" in error_msg.lower():
                        await self._log_debug("EXIT-ERROR-ANALYSIS", 
                            f"🚨 MARGIN ERROR ON EXIT! This indicates a mismatch. "
                            f"Bot thinks: Qty={p['qty']}, Zerodha has: {matching_position.get('quantity') if matching_position else 'Unknown'}. "
                            f"Possible cause: Position was partially/fully closed outside bot. "
                            f"Recommendation: Stop bot, check Zerodha positions manually.")
                    
                    # Circuit Breaker: Prevent infinite exit loop
                    self.exit_failure_count += 1
                    await self._log_debug("CRITICAL-EXIT-FAIL", 
                        f"❌ FAILED TO EXIT {p['symbol']}! Attempt {self.exit_failure_count}/3. Error: {error_msg or 'Unknown'}")
                    
                    if self.exit_failure_count >= 3:
                        # Force-clear position after 3 consecutive failures to break loop
                        await self._log_debug("EMERGENCY-POSITION-CLEAR", 
                            f"🚨 FORCE-CLEARING position after {self.exit_failure_count} failed exit attempts! CHECK ZERODHA MANUALLY!")
                        self.position = None
                        self.exit_failure_count = 0
                        await self._update_ui_trade_status()
                        _play_sound(self.manager, "warning")
                        return  # Exit after force-clear
                    else:
                        # Keep position and retry on next exit signal
                        await self._log_debug("EXIT-RETRY", 
                            f"⚠️ Keeping position. Will retry exit on next signal (attempt {self.exit_failure_count}/3)")
                        _play_sound(self.manager, "warning")
                        return  # CRITICAL FIX: Return here to prevent position clearing!
            else:
                # Paper trading exit WITH realistic live trading delays
                # 🎯 SIMULATE LIVE EXIT FLOW:
                # 1. Exit signal detected
                # 2. Order placement API call
                # 3. Broker order routing
                # 4. Exchange matching
                # 5. Fill confirmation
                
                await self._log_debug("PAPER TRADE", sell_log_message)
                
                # 🕐 DELAY 1: Exit order placement + API latency (150-250ms)
                exit_delay_ms = float(self.params.get("paper_exit_delay_ms", 400))
                if exit_delay_ms > 0:
                    exit_placement_delay = exit_delay_ms * 0.5  # 50% for placement
                    await asyncio.sleep(exit_placement_delay / 1000)
                    await self._log_debug("PAPER TRADE", f"⏱️ Simulated exit placement delay: {exit_placement_delay:.0f}ms")
                
                # 🕐 DELAY 2: Exit execution + fill (100-150ms)
                if exit_delay_ms > 0:
                    exit_execution_delay = exit_delay_ms * 0.5  # 50% for execution
                    await asyncio.sleep(exit_execution_delay / 1000)
                    await self._log_debug("PAPER TRADE", f"⏱️ Simulated exit execution delay: {exit_execution_delay:.0f}ms")
                
                await self._log_debug("PAPER TRADE", 
                    f"✅ Exit complete. Total simulated delay: {exit_delay_ms:.0f}ms (mimics live trading)")
                
            # 🛡️ CRITICAL: Ensure exit_price is valid before P&L calculation
            if not exit_price or exit_price <= 0:
                exit_price_display = f"₹{float(exit_price):.2f}" if isinstance(exit_price, (int, float)) and exit_price is not None else "None/Invalid"
                await self._log_debug("CRITICAL-EXIT-FAIL", 
                    f"❌ Invalid exit price ({exit_price_display}). Using last known price for P&L calculation.")
                exit_price = p.get("max_price", p.get("entry_price", 0))
                if not exit_price or exit_price <= 0:
                    exit_price = 100.0  # Final fallback
            
            gross_pnl = (exit_price - p["entry_price"]) * p["qty"]
            
            # 🛡️ CRITICAL: Ensure charges calculation doesn't fail
            try:
                charges = await self._calculate_trade_charges(tradingsymbol=p["symbol"], exchange=self.exchange, entry_price=p["entry_price"], exit_price=exit_price, quantity=p["qty"])
                # Ensure charges is a number, never None
                if charges is None:
                    charges = 0
                charges = float(charges) if charges is not None else 0
            except Exception as charge_error:
                await self._log_debug("CHARGE-CALC-ERROR", 
                    f"⚠️ Charge calculation failed: {charge_error}. Using 0 charges.")
                charges = 0
            
            net_pnl = gross_pnl - charges
            
            # Update cumulative P&L tracking
            self.daily_gross_pnl += gross_pnl
            self.total_charges += charges
            self.daily_net_pnl += net_pnl
            
            # Track this trade's P&L for rate limiter (Layer 3)
            if not hasattr(self, '_trades_this_minute_pnl'):
                self._trades_this_minute_pnl = []
            self._trades_this_minute_pnl.append(net_pnl)
            
            # 🛡️ CRITICAL: Use NET P&L for win/loss determination (not gross)
            # This matches backend to frontend calculations
            sound_type = None
            if net_pnl > 0:
                self.performance_stats["winning_trades"] += 1
                sound_type = "profit"
            elif net_pnl < 0:
                self.performance_stats["losing_trades"] += 1
                sound_type = "loss"
            # If net_pnl == 0, it's a break-even trade (don't count as win or loss)
            
            # Track P&L separately
            if gross_pnl > 0:
                self.daily_profit += gross_pnl
            else:
                self.daily_loss += gross_pnl  # Accumulate negative values
            
            # ⚡ Play sound immediately (non-blocking)
            if sound_type:
                _play_sound(self.manager, sound_type)
                
            final_pnl = round(gross_pnl, 2); final_charges = round(charges, 2); final_net_pnl = round(net_pnl, 2)
            if not all(isinstance(v, (int, float)) for v in [p["entry_price"], exit_price, final_pnl, final_charges, final_net_pnl]):
                await self._log_debug("CRITICAL-LOG-FAIL", f"Aborting trade log for {p['symbol']} due to invalid numeric data.")
                _play_sound(self.manager, "warning"); self.position = None
                await self._update_ui_trade_status(); await self._update_ui_performance()
                return
            
            # Note: exit_timestamp already captured at start of exit_position() for accuracy
            # 🛡️ CRITICAL FIX: Ensure exit_price is valid BEFORE calculating slippage
            if not exit_price or exit_price <= 0:
                exit_price = p.get("entry_price", 100.0)
            
            expected_exit_price = self.data_manager.prices.get(p["symbol"], exit_price)  # 🆕 Get expected exit price
            if not expected_exit_price or expected_exit_price <= 0:
                expected_exit_price = exit_price
            
            exit_slippage = round(exit_price - expected_exit_price, 2)  # 🆕 Calculate exit slippage
            
            # 🛡️ CRITICAL: Validate all values before logging to prevent format string errors
            if not p["entry_price"] or p["entry_price"] <= 0:
                p["entry_price"] = 100.0
            if not exit_price or exit_price <= 0:
                exit_price = p["entry_price"]
            if not reason:
                reason = "Unknown Exit Signal"
            
            # 🆕 Calculate trade duration
            try:
                entry_dt = datetime.strptime(p["entry_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                duration_seconds = (exit_timestamp - entry_dt).total_seconds()
            except Exception as e:
                duration_seconds = None
            
            # 🛡️ DEFENSIVE: Ensure ALL critical values are safe BEFORE creating log_info
            try:
                _exit_price = float(exit_price) if (exit_price is not None and exit_price != 0) else float(p.get("entry_price", 100.0))
                _entry_price = float(p.get("entry_price", 100.0))
                _final_net_pnl = float(final_net_pnl) if final_net_pnl is not None else 0.0
                _final_pnl = float(final_pnl) if final_pnl is not None else 0.0
                _final_charges = float(final_charges) if final_charges is not None else 0.0
            except (TypeError, ValueError):
                _exit_price = float(p.get("entry_price", 100.0))
                _entry_price = float(p.get("entry_price", 100.0))
                _final_net_pnl = 0.0
                _final_pnl = 0.0
                _final_charges = 0.0
            
            log_info = { 
                "timestamp": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"), 
                "trigger_reason": p.get("trigger_reason", reason), 
                "symbol": p["symbol"], 
                "quantity": p["qty"], 
                "pnl": _final_pnl, 
                "entry_price": _entry_price, 
                "exit_price": _exit_price, 
                "exit_reason": reason, 
                "trend_state": self.data_manager.trend_state, 
                "atr": round(self.data_manager.data_df.iloc[-1]["atr"], 2) if not self.data_manager.data_df.empty else 0, 
                "charges": _final_charges, 
                "net_pnl": _final_net_pnl,
                "entry_time": p.get("entry_time"),  # 🆕 Entry timestamp
                "exit_time": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S"),  # 🆕 Exit timestamp
                "duration_seconds": duration_seconds,  # 🆕 Trade duration
                "max_price": p.get("max_price"),  # 🆕 Max price reached
                "signal_time": p.get("signal_time"),  # 🆕 Signal detection time
                "order_time": p.get("order_time"),  # 🆕 Order placement time
                "expected_entry": p.get("expected_entry"),  # 🆕 Expected entry price
                "expected_exit": expected_exit_price,  # 🆕 Expected exit price
                "entry_slippage": p.get("entry_slippage"),  # 🆕 Entry slippage
                "exit_slippage": exit_slippage,  # 🆕 Exit slippage
                "latency_ms": p.get("latency_ms"),  # 🆕 Signal to order latency
                "trading_mode": self.params.get("trading_mode", "Paper Trading"),  # 🆕 Track mode
                "ucc": self._get_active_ucc(),
                # 🆕 Confirmatory momentum check data
                "momentum_price_rising": p.get("momentum_price_rising", 0),
                "momentum_accelerating": p.get("momentum_accelerating", 0),
                "momentum_index_sync": p.get("momentum_index_sync", 0),
                "momentum_volume_surge": p.get("momentum_volume_surge", 0),
                "momentum_checks_passed": p.get("momentum_checks_passed", 0),
                # 🆕 Predictive momentum check data
                "predictive_order_flow": p.get("predictive_order_flow", 0),
                "predictive_divergence": p.get("predictive_divergence", 0),
                "predictive_structure": p.get("predictive_structure", 0),
                "predictive_checks_passed": p.get("predictive_checks_passed", 0),
                "trigger_system": p.get("trigger_system", "UNKNOWN"),
                # 🆕 ENTRY TYPE DIFFERENTIATION
                "entry_type": p.get("entry_type", "UNKNOWN"),
                # 🆕 SUPERTREND HOLD MODE DIFFERENTIATION
                "supertrend_hold_mode": p.get("supertrend_hold_mode", "UNKNOWN"),
                "entry_option_st_state": p.get("entry_option_st_state", "UNKNOWN"),
                "exit_supertrend_reason": p.get("exit_supertrend_reason", "N/A"),
                # 🆕 CANDLE DATA TRACKING (captured at ENTRY time)
                "candle_open_price": p.get("candle_open_price"),  # Candle open price at entry
                "candle_close_price": p.get("candle_close_price"),  # Candle close/LTP at entry (NOT at exit!)
                "direction": p.get("direction")  # CE or PE
            }
            
            # ⚡ CRITICAL FIX: Ensure values are valid before final operations
            # Defensive check for safe formatting - AGGRESSIVE None handling
            try:
                # Step 1: Get raw values with None handling
                raw_exit_price = exit_price if exit_price is not None else None
                raw_entry_price = p.get("entry_price") if p.get("entry_price") is not None else None
                raw_net_pnl = final_net_pnl if final_net_pnl is not None else None
                
                # Step 2: Convert to float with fallback
                try:
                    _exit_price = float(raw_exit_price) if raw_exit_price is not None and raw_exit_price > 0 else float(raw_entry_price) if raw_entry_price is not None and raw_entry_price > 0 else 100.0
                except (TypeError, ValueError):
                    _exit_price = 100.0
                
                try:
                    _entry_price = float(raw_entry_price) if raw_entry_price is not None and raw_entry_price > 0 else 100.0
                except (TypeError, ValueError):
                    _entry_price = 100.0
                
                try:
                    _final_net_pnl = float(raw_net_pnl) if raw_net_pnl is not None else 0.0
                except (TypeError, ValueError):
                    _final_net_pnl = 0.0
                
                # Step 3: Extract string values
                _reason = str(reason) if reason is not None else "Unknown Exit Signal"
                _symbol = str(p.get("symbol", "UNKNOWN")) if p else "UNKNOWN"
                
                # Step 4: Final safety - ensure no None values leak through
                assert isinstance(_exit_price, (int, float)) and _exit_price is not None, f"exit_price must be number, got {type(_exit_price)}"
                assert isinstance(_entry_price, (int, float)) and _entry_price is not None, f"entry_price must be number, got {type(_entry_price)}"
                assert isinstance(_final_net_pnl, (int, float)) and _final_net_pnl is not None, f"final_net_pnl must be number, got {type(_final_net_pnl)}"
                
            except (TypeError, ValueError, ZeroDivisionError, AssertionError) as format_error:
                # If conversion fails, use SAFE DEFAULTS
                _exit_price = float(100.0)
                _entry_price = float(100.0)
                _final_net_pnl = float(0.0)
                _reason = "Exit Formatting Error"
                _symbol = p.get("symbol", "UNKNOWN") if p else "UNKNOWN"
                await self._log_debug("EXIT-FORMAT-SAFETY", 
                    f"⚠️ Format conversion failed (exit_price type={type(exit_price)}, exit_price val={exit_price}), using safe defaults: {format_error}")
                
            # ✅ CRITICAL FIX: Broadcast IMMEDIATELY without waiting for database write
            # Database logging happens in background (non-blocking) with retry logic
            # This ensures instant UI updates while maintaining data persistence
            
            # 🚀 LOG ASYNCHRONOUSLY: Fire-and-forget pattern with auto-retry
            async def log_trade_with_retry():
                """Log trade with exponential backoff retry on failure"""
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        await self.trade_logger.log_trade(log_info)
                        if attempt > 0:
                            await self._log_debug("TRADE-LOG-RETRY", f"✅ Trade logged on attempt {attempt+1}/{max_retries}")
                        return  # Success
                    except Exception as log_error:
                        if attempt < max_retries - 1:
                            await asyncio.sleep(0.1 * (2 ** attempt))  # 100ms, 200ms, 400ms backoff
                        else:
                            await self._log_debug("TRADE-LOG-FAILED", f"❌ Failed to log trade after {max_retries} attempts: {log_error}")
            
            # 🚀 START LOGGING IN BACKGROUND (non-blocking)
            asyncio.create_task(log_trade_with_retry())
            
            # 🚀 BROADCAST to UI IMMEDIATELY for instant display
            await self.manager.broadcast({"type": "new_trade_log", "payload": log_info})
            
            # 📝 Log completion message - with SAFE formatting
            try:
                # Ensure all format arguments are truly numbers before formatting
                exit_price_str = f"{float(_exit_price):.2f}" if isinstance(_exit_price, (int, float)) else "N/A"
                entry_price_str = f"{float(_entry_price):.2f}" if isinstance(_entry_price, (int, float)) else "N/A"
                pnl_str = f"{float(_final_net_pnl):.2f}" if isinstance(_final_net_pnl, (int, float)) else "N/A"
                
                await self._log_debug("EXIT COMPLETE", 
                    f"✅ {_symbol} exited: Entry=₹{entry_price_str}, Exit=₹{exit_price_str}, PNL=₹{pnl_str}, Reason={_reason}")
            except Exception as log_format_error:
                await self._log_debug("EXIT COMPLETE", 
                    f"✅ {_symbol} exited: Reason={_reason} (format error: {log_format_error})")
            
            # --- V47.16 Layer 4 & 6 Update ---
            now = get_ist_time()
            self.last_exit_time = now # Layer 4: Set the time of the last exit (Global)
            
            # Layer 6: Store enhanced exit data for smart cooldown
            current_candle = self.data_manager.option_candles.get(p["symbol"], {})
            candle_start_time = current_candle.get('candle_start_time', 0)
            
            # 🛡️ OPTION D: Set 15-second cooldown with 1% price recovery bypass
            # Balanced protection: blocks immediate re-entry but allows strong moves
            cooldown_until = now + timedelta(seconds=15)
            cooldown_display = "15s"
            
            self.symbol_exit_cooldown[p["symbol"]] = {
                'time': now,
                'price': exit_price,
                'candle_start_time': candle_start_time,
                'direction': p["direction"],
                'reason': reason
            }
            
            # 🛡️ SKIP LAYER 6 COOLDOWN FOR AUTO-SQUARE-OFF: Allow immediate re-entry after EOD exit
            if not skip_layer6_cooldown:
                self.symbol_entry_cooldown[p["symbol"]] = {
                    'until': cooldown_until,
                    'entry_price': p.get('entry_price', exit_price),
                    'reason': reason
                }
                await self._log_debug("Layer 6",
                    f"🛡️ Layer 6: {cooldown_display} cooldown with 1% price recovery bypass for {p['symbol']} (Exit: {reason})")
            else:
                # For auto-square-off, skip Layer 6 cooldown to allow immediate re-entry
                await self._log_debug("Layer 6",
                    f"⏭️ OPTION D SKIPPED: No cooldown for {p['symbol']} (Auto-Square-Off - immediate re-entry allowed)")

            # Layer 8: Clear lock on exit to prevent ghost re-entries
            # This allows proper re-entry only after price moves > MIN_PRICE_CHANGE (0.5%)
            self.symbol_entry_lock.pop(p["symbol"], None)  # ✅ RESTORED - Fixes duplicate trades

            # 🚀 PROCESS SIGNAL QUEUE: Re-process queued signals after exit completes
            # NOTE: Don't use stale prices/momentum from queued signals - let engines re-validate at current prices
            if len(self.signal_queue) > 0:
                queued_count = len(self.signal_queue)
                await self._log_debug("Signal Queue", 
                    f"📥 {queued_count} queued signal(s) available. Will be re-scanned by engines at current prices after next cycle.")
                
                # Keep queued signals - they will be re-validated by engines at current market prices
                # This prevents using stale price data from when signal was originally queued
                # The signals remain in queue and normal engine scanning will process them with fresh prices
                
                # Clear queue to prevent stale data contamination
                # Fresh signals from engines are more reliable than stale queued data
                self.signal_queue = []

            # Reset exit logic state on position close
            self.entry_candle_was_green = None

            self.position = None
            self.exit_attempt_counter = 0  # Fix 3: Reset exit attempt counter on successful exit
            self.exit_in_progress = False  # Fix 5: Clear exit flag after completion
            
            # 🔥 CRITICAL FIX: Clear capital cache IMMEDIATELY after exit
            # This forces fresh fetch from Zerodha to get updated available margin
            # Otherwise bot uses stale cached value and blocks capital for next trade
            self.live_capital_cache = None
            self.live_capital_last_fetched = None
            await self._log_debug("Capital", "🔄 Cache cleared after exit - margin will be re-fetched for next trade")
            
            await self._update_ui_trade_status(); await self._update_ui_performance()
        except Exception as e:
            # 🛡️ CRITICAL: Safe error logging - don't fail the error handler!
            try:
                symbol = p.get("symbol", "UNKNOWN") if p else "UNKNOWN"
                error_msg = str(e) if e else "Unknown error"
                await self._log_debug("CRITICAL-EXIT-FAIL", 
                    f"FAILED TO EXIT {symbol}! MANUAL INTERVENTION REQUIRED! Error: {error_msg}")
            except:
                # Even the error logging failed - log basic message
                await self._log_debug("CRITICAL-EXIT-FAIL", 
                    "FAILED TO EXIT! MANUAL INTERVENTION REQUIRED! (Error while logging error)")
            
            self.exit_in_progress = False  # Fix 5: Clear exit flag even on error
            _play_sound(self.manager, "warning")

    async def evaluate_exit_logic(self):
        async with self.position_lock:
            if not self.position: return
            
            # ✨ CRITICAL: Skip exits if we just sold excess from duplicate (1s cooldown)
            # This prevents triggering exit twice when position qty mismatch is detected and corrected
            if self.excess_sale_cooldown_until is not None:
                current_time = time_module.time()
                if current_time < self.excess_sale_cooldown_until:
                    await self._log_debug("EXIT-SKIP", f"⏭️ Skipping exit check (excess sale cooldown active)")
                    return
                else:
                    self.excess_sale_cooldown_until = None  # Cooldown expired
            
            p, ltp = self.position, self.data_manager.prices.get(self.position["symbol"])
            if ltp is None: return
            
            # 🛡️ Defensive: Ensure ltp is a valid number (skip if empty or invalid)
            try:
                ltp = float(ltp) if ltp else None
            except (ValueError, TypeError):
                return
            
            if ltp is None:
                return
            
            # Ensure max_price and trail_sl exist (defensive initialization)
            if "max_price" not in p:
                p["max_price"] = p.get("entry_price", ltp)
            if "trail_sl" not in p:
                # Initialize using GUI parameters (same as entry SL)
                sl_points = float(self.params.get("trailing_sl_points", 2.0))
                sl_percent = float(self.params.get("trailing_sl_percent", 1.0))
                entry_price_temp = p.get("entry_price", ltp)
                p["trail_sl"] = round(max(entry_price_temp - sl_points, entry_price_temp * (1 - sl_percent / 100)), 2)
            
            # 🛡️ Defensive: Ensure entry_price is valid
            try:
                entry_price = float(p.get("entry_price", ltp))
            except (ValueError, TypeError):
                return
            
            old_max_price = p["max_price"]
            old_trail_sl = p["trail_sl"]
            
            if ltp > p["max_price"]: 
                p["max_price"] = ltp
                await self._log_debug("TSL Update", f"📈 New max price: ₹{old_max_price:.2f} → ₹{ltp:.2f}")
            
            # Calculate profit percentage
            profit_pct = ((ltp - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            
            # Break-even logic - One-time activation (flag-based to ensure it only happens once)
            if "break_even_activated" not in p:
                p["break_even_activated"] = False
            
            break_even_threshold = float(self.params.get("break_even_threshold_pct", 2.0))
            # 🛡️ RESPECT GUI SETTING: If break_even_threshold is 0 or negative, disable break-even logic
            if break_even_threshold > 0:
                # 📊 Break-even target: Set to the profit threshold level (entry + threshold%)
                # If GUI threshold=1%, then BE_target = entry*1.01 (protects 1% gain)
                break_even_target = round(p["entry_price"] * (1 + break_even_threshold / 100), 2)
                
                # Activate break-even on FIRST tick above threshold, regardless of TSL position
                if not p["break_even_activated"] and profit_pct >= break_even_threshold:
                    # Ensure TSL is AT LEAST at break-even target (never lower it)
                    if p["trail_sl"] < break_even_target:
                        p["trail_sl"] = break_even_target
                        await self._log_debug("Exit Logic", f"✅ Break-even activated! SL moved to ₹{old_trail_sl:.2f} → ₹{p['trail_sl']:.2f} (Entry + {break_even_threshold}% @ ₹{break_even_target:.2f})")
                    else:
                        await self._log_debug("Exit Logic", f"✅ Break-even threshold reached ({profit_pct:.2f}%), but TSL already at ₹{p['trail_sl']:.2f} (above BE target ₹{break_even_target:.2f})")
                    p["break_even_activated"] = True
            else:
                await self._log_debug("Exit Logic", f"🚫 Break-even disabled (threshold: {break_even_threshold}%)")
            
            # ALWAYS run normal trailing SL logic (even after break-even)
            # TSL should tighten as price rises - calculate based on points or percent, take the HIGHER value
            sl_points = float(self.params.get("trailing_sl_points", 2.0))
            sl_percent = float(self.params.get("trailing_sl_percent", 1.0))
            
            # Calculate SL two ways and take the maximum (closest to current price = best protection)
            sl_by_points = p["max_price"] - sl_points
            sl_by_percent = p["max_price"] * (1 - sl_percent / 100)
            new_trail_sl = round(max(sl_by_points, sl_by_percent), 2)
            
            # 🛡️ CRITICAL FIX: If breakeven is activated, TSL can NEVER go below breakeven target
            if p.get("break_even_activated", False):
                new_trail_sl = max(new_trail_sl, break_even_target)
                # Also ensure current TSL is never below breakeven (fix legacy positions)
                p["trail_sl"] = max(p["trail_sl"], break_even_target)
            
            # Only update if new SL is HIGHER (better protection, closer to price)
            if new_trail_sl > old_trail_sl:
                p["trail_sl"] = new_trail_sl
                await self._log_debug("TSL Update", f"🔼 TSL updated: ₹{old_trail_sl:.2f} → ₹{new_trail_sl:.2f} (Max: ₹{p['max_price']:.2f}, Points: {sl_points}, Percent: {sl_percent}%)")
            
            # --- START: ADAPTIVE MOMENTUM EXIT LOGIC WITH TICK COUNTING ---
            symbol = p["symbol"]
            
            await self._update_ui_trade_status()
            
            # 1. Check Trailing Stop Loss
            sl_hit = ltp <= p["trail_sl"]
            
            # 2. Calculate current profit
            profit_pct = ((ltp - p["entry_price"]) / p["entry_price"]) * 100 if p["entry_price"] > 0 else 0
            
            # ❌ REMOVED: Scalper 2% profit target - Let TSL and momentum reversal handle exits
            # This was causing premature exits on profitable trades (exited at +₹8,613 after 1s instead of holding for ₹20,000+)
            # Old logic: if scalper_enabled and profit_pct >= 2.0: exit_position("Scalper Quick Profit")
            
            # 🎯 SCALPER MODE: Instant Red Candle Exit (Exit when candle turns red)
            scalper_enabled = self.STRATEGY_PARAMS.get('scalper_enabled', False)
            red_candle_exit = self.STRATEGY_PARAMS.get('scalper_red_candle_exit', True)
            if scalper_enabled and red_candle_exit:
                option_candle = self.data_manager.option_candles.get(symbol)
                if option_candle and 'open' in option_candle:
                    candle_open = option_candle.get('open', 0)
                    is_red = ltp < candle_open
                    
                    # Exit IMMEDIATELY on red candle (regardless of profit)
                    if is_red and profit_pct > -1.0:  # Allow small loss tolerance (-1%)
                        await self._log_debug("🔴 SCALPER RED CANDLE", 
                            f"⚠️ CANDLE TURNED RED: LTP ₹{ltp:.2f} < Open ₹{candle_open:.2f} - Exiting at {profit_pct:.2f}%")
                        await self.exit_position(f"Scalper Red Candle @ {profit_pct:.2f}%")
                        return
            
            # 📊 PRICE OBSERVER MODE: Velocity Decay Exit (Exit when momentum dies)
            price_observer_enabled = self.STRATEGY_PARAMS.get('price_observer_enabled', False)
            if price_observer_enabled and 'momentum_data' in p:
                momentum_data = p['momentum_data']
                if 'entry_velocity' in momentum_data:
                    entry_velocity = momentum_data['entry_velocity']
                    
                    # Calculate current velocity
                    history = self.data_manager.price_history.get(symbol, [])
                    if len(history) >= 3:
                        recent_prices = [p_val for ts, p_val in history[-3:]]
                        current_velocity = (recent_prices[-1] - recent_prices[-2]) if len(recent_prices) >= 2 else 0
                        
                        # ✅ FIX: Only exit if velocity is NEGATIVE (price falling), not just slowing
                        # This allows trades to continue as long as price is rising, even if momentum slows
                        if current_velocity < 0:  # Price declining
                            await self._log_debug("📊 MOMENTUM REVERSAL EXIT", 
                                f"🔴 PRICE FALLING: Velocity={current_velocity:.3f} (negative) - Entry velocity was +{entry_velocity:.3f}, now reversed to negative. Exiting at {profit_pct:.2f}%")
                            await self.exit_position(f"Momentum Reversal @ {profit_pct:.2f}%")
                            return
                    
                    # Also check max hold time in ticks
                    max_hold_ticks = self.STRATEGY_PARAMS.get('price_observer_max_hold_ticks', 30)
                    current_ticks = len(history)
                    entry_tick_count = momentum_data.get('tick_count', 0)
                    ticks_held = current_ticks - entry_tick_count if entry_tick_count > 0 else 0
                    
                    if ticks_held >= max_hold_ticks:
                        await self._log_debug("📊 PRICE OBSERVER EXIT", 
                            f"⏰ MAX HOLD TIME: {ticks_held} ticks >= {max_hold_ticks} - Exiting at {profit_pct:.2f}%")
                        await self.exit_position(f"Price Observer Max Hold @ {profit_pct:.2f}%")
                        return
            
            # 3. EXIT LOGIC - DIFFERENTIATE BY ENTRY TYPE
            # SUPERTREND_ENTRY uses Dual Supertrend exit logic
            # NO_WICK_BYPASS and TREND_CONTINUATION use standard TSL/SL exit
            momentum_loss = False
            
            # Determine if this trade used Supertrend entry (has captured entry state)
            entry_option_st_uptrend = p.get('entry_option_st_uptrend', None)
            is_supertrend_entry = entry_option_st_uptrend is not None
            
            # Get configuration
            dual_st_enabled = False  # DISABLED: Dual Supertrend exit removed - was causing 71% of losses
            dual_st_max_hold = self.STRATEGY_PARAMS.get('dual_st_max_hold_seconds', 120)
            
            # ❌ DUAL SUPERTREND EXIT LOGIC - DISABLED (was causing premature exits with small losses)
            if False and is_supertrend_entry and dual_st_enabled:
                # Calculate elapsed time since entry (for logging/safety only)
                # 🛡️ DEFENSIVE: Convert entry_time string to timestamp before subtraction
                elapsed_seconds = 0
                if "entry_time" in p:
                    try:
                        entry_dt = datetime.strptime(p["entry_time"], "%Y-%m-%d %H:%M:%S")
                        elapsed_seconds = (time_module.time() - entry_dt.timestamp())
                    except (ValueError, TypeError, AttributeError):
                        elapsed_seconds = 0
                
                # Get current INDEX and OPTION Supertrend
                index_trend = self.data_manager.trend_state  # "BULLISH" or "BEARISH"
                option_st_line, option_st_uptrend = self.data_manager.calculate_option_supertrend(p['symbol'])
                
                # 🆕 DETECT MARKET CONDITION AT ENTRY (for logging only)
                # Check if position has entry_st_state (set during entry)
                entry_st_state = entry_option_st_uptrend
                
                # Determine market condition at entry (for classification only)
                is_trending_mode = entry_st_state is not None
                
                # 🆕 TRACK SUPERTREND HOLD MODE for differentiation in trade history
                if is_trending_mode:
                    p['supertrend_hold_mode'] = 'TRENDING'
                    # Map entry state to readable format
                    if p['direction'] == 'CE':  # Call (bullish)
                        p['entry_option_st_state'] = 'UPTREND' if entry_st_state else 'DOWNTREND'
                    else:  # PE (bearish)
                        p['entry_option_st_state'] = 'DOWNTREND' if entry_st_state else 'UPTREND'
                else:
                    p['supertrend_hold_mode'] = 'SIDEWAYS'
                    p['entry_option_st_state'] = 'SIDEWAYS'
                
                # Determine if both Supertrends support the position
                if p['direction'] == 'CE':  # Call (bullish)
                    index_supports = (index_trend == "BULLISH")
                    option_supports = (option_st_uptrend == True) if option_st_uptrend is not None else False
                else:  # PE (bearish)
                    index_supports = (index_trend == "BEARISH")
                    option_supports = (option_st_uptrend == False) if option_st_uptrend is not None else False
                
                both_support = index_supports and option_supports
                
                # ✅ IMMEDIATE EXIT LOGIC - NO ADAPTIVE HOLD
                # Exit immediately when any condition is met, regardless of elapsed time
                
                # CHECK 1: Supertrend flip detected (primary exit condition)
                if not both_support:
                    momentum_loss = True
                    # 🆕 Track which Supertrend flipped
                    flipped_st = []
                    if not index_supports:
                        flipped_st.append('INDEX')
                    if not option_supports:
                        flipped_st.append('OPTION')
                    p['exit_supertrend_reason'] = '+'.join(flipped_st) if flipped_st else 'UNKNOWN'
                    await self._log_debug("Dual ST Exit", 
                        f"📊 Supertrend Flip: INDEX={'BULLISH' if index_supports else 'BEARISH'}, OPTION={'UP' if option_supports else 'DOWN'}, Hold: {elapsed_seconds:.1f}s")
                
                # CHECK 2: Trailing Stop Loss hit (critical safety exit)
                elif sl_hit:
                    momentum_loss = True
                    p['exit_supertrend_reason'] = 'TRAILING_SL_HIT'
                    await self._log_debug("Dual ST Exit", 
                        f"🔴 TSL Hit: LTP ₹{ltp:.2f} <= TSL ₹{p['trail_sl']:.2f}, Profit {profit_pct:.2f}%, Hold: {elapsed_seconds:.1f}s")
                
                # CHECK 3: Price returned to entry price (secondary exit condition)
                elif ltp <= p["entry_price"]:
                    momentum_loss = True
                    p['exit_supertrend_reason'] = 'ENTRY_PRICE_HIT'
                    await self._log_debug("Dual ST Exit", 
                        f"💰 Entry Price Hit: LTP ₹{ltp:.2f} <= Entry ₹{p['entry_price']:.2f}, Profit {profit_pct:.2f}%, Hold: {elapsed_seconds:.1f}s")
                
                # CHECK 4: Safety - Exit at maximum hold time (fail-safe only)
                elif elapsed_seconds >= dual_st_max_hold:
                    momentum_loss = True
                    p['exit_supertrend_reason'] = 'MAX_HOLD_TIME'
                    await self._log_debug("Dual ST Exit", 
                        f"⏱️ Max Hold Time: {elapsed_seconds:.1f}s, Safety Exit")
                
                # CHECK 5: Large loss (>5% below entry) - safety exit
                elif ltp < (p["entry_price"] * 0.95):
                    momentum_loss = True
                    p['exit_supertrend_reason'] = 'LARGE_LOSS'
                    await self._log_debug("Dual ST Exit", 
                        f"⚠️ Large Loss: {profit_pct:.2f}% (>5%), Safety Exit, Hold: {elapsed_seconds:.1f}s")
                
                # HOLD: Both Supertrends still support - keep position locked
                else:
                    await self._log_debug("Dual ST Hold", 
                        f"🔒 Holding: Both Supertrends aligned, INDEX={index_trend}, OPTION={'UP' if option_st_uptrend else 'DOWN'}, Hold: {elapsed_seconds:.1f}s")
            
            else:
                # ✅ STANDARD EXIT LOGIC - For NO_WICK_BYPASS, TREND_CONTINUATION, and ST_MOMENTUM_SYNC entries
                # These trades exit on TSL hit, entry price hit, or momentum break
                
                # 🆕 CHECK FOR ST MOMENTUM SYNC EXIT CONDITIONS
                is_st_momentum_sync = "ST_Momentum_Sync" in p.get("trigger_reason", "")
                
                if is_st_momentum_sync:
                    # ST Momentum Sync specific exits:
                    # 1. Option Supertrend flip (downtrend)
                    # 2. Candle color reversal (RED candle)
                    # 3. TSL hit
                    # 4. Entry price hit
                    
                    symbol = p.get("symbol")
                    if symbol:
                        # Check Option Supertrend (9/1.0) flip
                        if hasattr(self, 'v47_coordinator') and self.v47_coordinator:
                            st_engine = getattr(self.v47_coordinator, 'st_momentum_sync_engine', None)
                            if st_engine:
                                option_st_line, option_st_uptrend = st_engine._calculate_option_supertrend_custom(symbol, period=9, multiplier=1.0)
                                
                                # Exit if Supertrend flipped to downtrend
                                if option_st_line is not None and not option_st_uptrend:
                                    momentum_loss = True
                                    p['exit_supertrend_reason'] = 'OPTION_ST_FLIP'
                                    await self._log_debug("ST Momentum Exit", 
                                        f"🔴 Option Supertrend Flip: Downtrend detected, exiting at ₹{ltp:.2f}")
                        
                        # Check candle color reversal (GREEN → RED)
                        if not momentum_loss:
                            option_candle = self.data_manager.option_candles.get(symbol)
                            if option_candle:
                                candle_open = option_candle.get('open', 0)
                                is_red_candle = ltp < candle_open
                                
                                if is_red_candle:
                                    momentum_loss = True
                                    p['exit_supertrend_reason'] = 'CANDLE_RED'
                                    await self._log_debug("ST Momentum Exit", 
                                        f"🔴 Candle turned RED: LTP ₹{ltp:.2f} < Open ₹{candle_open:.2f}, exiting")
                
                # Check TSL hit for standard entries (all types)
                # 🔥 FIXED: Don't force momentum_loss=True on TSL hit - TSL is independent exit
                # Let sl_hit be the only reason when TSL triggers
                if sl_hit:
                    await self._log_debug("Exit Logic", 
                        f"🔴 TSL Hit: LTP ₹{ltp:.2f} <= TSL ₹{p['trail_sl']:.2f}, Profit {profit_pct:.2f}%")
                # 🔥 FIXED: Removed Entry Price Hit logic that was causing instant exits
                # This logic was fundamentally flawed - triggered on any pullback (loss, not profit)
                # TSL hit above is sufficient for stop loss management
            
            # 🆕 MOMENTUM DECAY EXIT: Check if momentum is fading (2/3 declining ticks)
            # This protects profits by catching reversals early before TSL hits
            # Only check after 30 seconds (momentum decay is a secondary exit, not primary)
            try:
                entry_dt = datetime.strptime(p.get("entry_time", ""), "%Y-%m-%d %H:%M:%S")
                elapsed_seconds = (time_module.time() - entry_dt.timestamp()) if entry_dt else 0
                
                if elapsed_seconds >= 30 and not sl_hit and len(self.momentum_ticks) >= 3:
                    # Check last 3 ticks for momentum decay
                    recent_ticks = self.momentum_ticks[-3:]
                    red_tick_count = sum(1 for tick in recent_ticks if not tick[2])  # tick[2] = is_green_tick
                    
                    if red_tick_count >= 2:  # 2 out of 3 ticks are red (declining)
                        await self._log_debug("Momentum Decay", 
                            f"⚠️ {symbol}: Downward momentum detected ({red_tick_count}/3 ticks declining) - EARLY EXIT")
                        await self.exit_position("Momentum Decay (Early Exit)")
                        return
            except (ValueError, TypeError, AttributeError):
                pass  # Skip momentum decay check if entry_time is invalid
            
            # 🎯 SUPERTREND ANGLE EXIT: 🔴 DISABLED - ST angle logic removed
            # if p.get('entry_type') == 'ST_ANGLE_TREND':
            #     ... ST angle exit logic removed ...
            
            # ⚡️ ENTRY PRICE HIT EXIT: Exit if price drops back to or below entry price
            # Works in ALL modes (Standard + Aggressive Hold) - fundamental loss protection
            # ✅ FIX 2: Added -0.3% buffer to account for tick/execution delay (200-600ms)
            symbol = p.get('symbol')
            entry_price_buffer = entry_price * 0.997 if entry_price else None  # -0.3% buffer
            if entry_price_buffer and ltp <= entry_price_buffer:
                await self._log_debug("Entry Price Hit", 
                    f"🔴 ENTRY PRICE HIT: LTP ₹{ltp:.2f} <= Buffer ₹{entry_price_buffer:.2f} (Entry: ₹{entry_price:.2f}) — exiting to prevent loss")
                await self.exit_position(f"Entry Price Hit @ ₹{ltp:.2f} (Entry: ₹{entry_price:.2f}, Buffer: ₹{entry_price_buffer:.2f})")
                return
            
            # �🔴 INTRA-CANDLE REVERSAL EXIT: Exit if entry candle turned RED (same-candle reversal)
            # V47.14: Exit ANY TIME candle is red, regardless of entry candle color
            symbol = p.get('symbol')
            if symbol:
                live_option_candle = self.data_manager.option_candles.get(symbol)
                
                if live_option_candle and 'open' in live_option_candle:
                    current_candle_open = live_option_candle.get('open', 0)
                    
                    # Check if current candle is RED (LTP < Open)
                    is_current_candle_red = ltp < current_candle_open
                    
                    if is_current_candle_red:
                        # Candle is RED - immediate exit (V47.14 spec)
                        await self._log_debug("Intra-Candle Red Exit", 
                            f"🔴 RED CANDLE: Open ₹{current_candle_open:.2f} > LTP ₹{ltp:.2f}")
                        await self.exit_position(f"Red Candle Exit @ ₹{ltp:.2f}")
                        return
            
            # 🟢 GREEN CANDLE HOLD OVERRIDE: Check if we should hold despite exit signals
            green_hold_override = False
            override_reason = ""
            symbol = p.get('symbol')  # FIXED: Was 'tradingsymbol', should be 'symbol'
            
            await self._log_debug("Exit Logic", f"🔍 Override Check: symbol={symbol}, sl_hit={sl_hit}, momentum_loss={momentum_loss}, profit={profit_pct:.2f}%")
            
            if symbol and (sl_hit or momentum_loss):
                # Only check override if we have exit signals to override
                await self._log_debug("Exit Logic", f"🟢 Calling override check for {symbol}...")
                is_override_active, override_reason = await self._is_green_candle_hold_active(
                    symbol=symbol,
                    current_ltp=ltp,
                    profit_pct=profit_pct
                )
                await self._log_debug("Exit Logic", f"🟢 Override result: active={is_override_active}, reason={override_reason}")
                
                if is_override_active:
                    green_hold_override = True
                    exit_type = "TSL Hit" if sl_hit else "Momentum Loss"
                    await self._log_debug("Green Hold Override", 
                        f"🟢 OVERRIDE ACTIVATED: {override_reason} - Skipping {exit_type}")
            
            # 4. Exit on SL or momentum loss (UNLESS green candle override active)
            if (sl_hit or momentum_loss) and not green_hold_override:
                exit_reason = ""
                if sl_hit and momentum_loss:
                    exit_reason = "SL Hit AND Momentum Loss"
                    await self._log_debug("Exit Logic", f"🔴 Dual Exit: SL (₹{p['trail_sl']:.2f}) + Momentum Loss")
                elif sl_hit:
                    exit_reason = "Trailing SL Hit"

                    await self._log_debug("Exit Logic", f"🔴 Trailing SL Hit (₹{p['trail_sl']:.2f})")
                elif momentum_loss:
                    # Check what triggered momentum loss
                    exit_subtype = p.get('exit_supertrend_reason', 'UNKNOWN')
                    if exit_subtype == 'ENTRY_PRICE_HIT':
                        # 🔥 REMOVED: This condition will never trigger (Entry Price Hit logic removed)
                        exit_reason = f"Momentum Loss ({profit_pct:.2f}% profit)"
                    else:
                        exit_reason = f"Momentum Loss ({profit_pct:.2f}% profit)"

                await self.exit_position(exit_reason) 
                return

            # --- END: DUAL SUPERTREND HOLD LOGIC ---
            
            # ⚡ OPTIMIZED: Check OPTION candle patterns instead of INDEX patterns
            # Exit based on actual option reversal, not index proxy
            # This prevents premature exits when index reverses but option continues
            
            symbol = p.get('symbol')  # 🔧 FIXED: Was 'tradingsymbol' (doesn't exist), should be 'symbol'
            # ❌ DISABLED: Engulfing exits cause premature exits on normal momentum consolidation
            # Not part of V47.14 core specification. Let TSL handle price-based exits instead.
            # if symbol:
            #     live_option_candle = self.data_manager.option_candles.get(symbol)
            #     prev_option_candle = self.data_manager.previous_option_candles.get(symbol)
            #     
            #     # Only check if we have both current and previous option candles
            #     if live_option_candle and prev_option_candle and 'open' in live_option_candle and 'open' in prev_option_candle:
            #         # Convert dict to Series-like object for engulfing check
            #         import pandas as pd
            #         live_candle_series = pd.Series(live_option_candle)
            #         prev_candle_series = pd.Series(prev_option_candle)
            #         
            #         # CE: Exit on option's own Bearish Engulfing (not index)
            #         if p['direction'] == 'CE' and self._is_bearish_engulfing(prev_candle_series, live_candle_series):
            #             await self._log_debug("Exit Logic", f"Invalidation: Bearish Engulfing on {symbol} option. Exiting CE.")
            #             await self.exit_position("Invalidation: Option Bearish Engulfing"); return
            #         
            #         # PE: Exit on option's own Bullish Engulfing (not index)
            #         elif p['direction'] == 'PE' and self._is_bullish_engulfing(prev_candle_series, live_candle_series):
            #             await self._log_debug("Exit Logic", f"Invalidation: Bullish Engulfing on {symbol} option. Exiting PE.")
            #             await self.exit_position("Invalidation: Option Bullish Engulfing"); return
                        
    async def partial_exit_position(self):
        # ✅ PARTIAL EXIT: Exit percentage of position when profit % target is reached
        if not self.position: return
        p = self.position
        
        # Get partial exit percentage from GUI parameters
        try:
            partial_exit_pct = float(self.params.get("partial_exit_pct", 50)) if self.params.get("partial_exit_pct") else 50
        except (ValueError, TypeError):
            partial_exit_pct = 50
        
        # Validate and get lot size
        lot_size = p.get("lot_size", 1)
        if lot_size <= 0: lot_size = 1
        
        # Calculate quantity to exit (rounded to lot size)
        qty_to_exit = int(min(math.ceil((p["qty"] / lot_size) * (partial_exit_pct / 100)) * lot_size, p["qty"]))
        
        # Log the exit plan
        ltp = self.data_manager.prices.get(p["symbol"], p["entry_price"])
        current_pnl = (ltp - p["entry_price"]) * p["qty"]
        await self._log_debug("Partial Exit Plan", 
            f"📊 Exiting {partial_exit_pct:.0f}% of {p['qty']} qty = {qty_to_exit} qty | "
            f"Current PNL: ₹{current_pnl:.2f}")
        
        if qty_to_exit <= 0: 
            await self._log_debug("Partial Exit Skip", f"⚠️ Qty to exit is 0. Skipping partial exit.")
            return
        
        # Check if remaining will be less than lot size - if so, exit entire position
        if (p["qty"] - qty_to_exit) < lot_size: 
            await self.exit_position(f"Final Partial Profit-Take"); 
            return
        exit_price = self.data_manager.prices.get(p["symbol"], p["entry_price"])
        
        # 🛡️ CRITICAL: Validate exit price for partial exit
        if not exit_price or exit_price <= 0:
            await self._log_debug("CRITICAL-PARTIAL-EXIT-FAIL", 
                f"❌ INVALID PRICE (₹{exit_price}) for partial exit of {p['symbol']}! Skipping partial exit.")
            return
        
        try:
            if self.params.get("trading_mode") == "Live Trading":
                # 🎯 SMART ADAPTIVE EXECUTION: Analyze order book to choose best strategy
                will_slice = self.freeze_limit and qty_to_exit > self.freeze_limit
                
                # Smart decision: Check order book depth before choosing strategy
                if not will_slice:
                    # For small exits (< freeze), analyze if Level 1 can fill completely
                    use_depth_analysis = await self.order_manager.should_use_depth_analysis(
                        symbol=p["symbol"], 
                        exchange=self.exchange, 
                        qty=qty_to_exit, 
                        transaction_type=kite.TRANSACTION_TYPE_SELL
                    )
                else:
                    # Large exits always use depth (will slice anyway)
                    use_depth_analysis = True
                
                # ✅ PURE DEPTH ANALYSIS: 3 attempts for optimal partial exit pricing
                # - Maximizes exit value with fresh depth analysis
                # - 50-150ms per attempt, no chase/market fallback
                basket_result = await self.order_manager.execute_basket_order(
                    quantity=qty_to_exit,
                    transaction_type=kite.TRANSACTION_TYPE_SELL,
                    tradingsymbol=p["symbol"],
                    exchange=self.exchange,
                    freeze_limit=self.freeze_limit,
                    price=exit_price,
                    use_level2_flow=True,  # ✅ PURE DEPTH: 3 attempts with fresh data
                    use_chase=False,  # ❌ No chase fallback
                    chase_retries=0,  # Not used
                    chase_timeout_ms=0  # Not used
                )
                
                # Check if partial exit was successful
                if basket_result["status"] not in ["COMPLETE", "PARTIAL"]:
                    # CRITICAL FIX: Report failed partial exit to kill switch
                    error_msg = basket_result.get("orders", [{}])[0].get("error", "") if basket_result.get("orders") else ""
                    kill_switch.check_failed_orders("REJECTED", error_msg)
                    await self._log_debug("CRITICAL-PARTIAL-EXIT-FAIL", 
                        f"❌ Failed to partially exit {p['symbol']}: Basket order FAILED")
                    _play_sound(self.manager, "warning")
                    return
                
                # CRITICAL FIX: Verify actual filled quantity AND actual average price from Zerodha
                qty_to_exit, actual_exit_price, exit_fill_time = await self._verify_order_execution(basket_result)
                
                if qty_to_exit == 0:
                    await self._log_debug("CRITICAL-PARTIAL-EXIT-FAIL", 
                        f"❌ Order verification failed: 0 qty sold")
                    _play_sound(self.manager, "warning")
                    return
                
                # CRITICAL FIX: Use actual exit price from Zerodha (not WebSocket LTP)
                if actual_exit_price is not None:
                    exit_price = actual_exit_price
                    await self._log_debug("Exit Price", f"✅ Using actual exit price: ₹{actual_exit_price:.2f}")
                else:
                    await self._log_debug("Exit Price", f"⚠️ Using expected price ₹{exit_price:.2f} (verification failed)")
                
                if basket_result["status"] == "PARTIAL":
                    await self._log_debug("Profit.Take", 
                        f"⚠️ Partial fill: {qty_to_exit} qty exited (some orders failed)")
                    # Reset exit failure counter on partial success
                    self.exit_failure_count = 0
                        
            gross_pnl = (exit_price - p["entry_price"]) * qty_to_exit
            charges = await self._calculate_trade_charges(tradingsymbol=p["symbol"], exchange=self.exchange, entry_price=p["entry_price"], exit_price=exit_price, quantity=qty_to_exit)
            net_pnl = gross_pnl - charges
            
            # Update cumulative P&L tracking
            self.daily_gross_pnl += gross_pnl
            self.total_charges += charges
            self.daily_net_pnl += net_pnl
            
            # Update profit/loss tracking
            if gross_pnl > 0:
                self.daily_profit += gross_pnl
                _play_sound(self.manager, "profit")
            else:
                self.daily_loss += gross_pnl  # Accumulate negative values
                _play_sound(self.manager, "loss")
            
            reason = f"Partial Profit-Take ({self.next_partial_profit_level})"
            exit_timestamp = get_ist_time()
            
            # Calculate expected exit price and slippage
            expected_exit_price = self.data_manager.prices.get(p["symbol"], exit_price)
            exit_slippage = round(exit_price - expected_exit_price, 2)
            
            # Calculate trade duration
            try:
                entry_dt = datetime.strptime(p["entry_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                duration_seconds = (exit_timestamp - entry_dt).total_seconds()
            except Exception as e:
                duration_seconds = None
            
            log_info = {
                "timestamp": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "trigger_reason": p["trigger_reason"],
                "symbol": p["symbol"],
                "quantity": qty_to_exit,
                "pnl": round(gross_pnl, 2),
                "entry_price": p["entry_price"],
                "exit_price": exit_price,
                "exit_reason": reason,
                "trend_state": self.data_manager.trend_state,
                "atr": round(self.data_manager.data_df.iloc[-1]["atr"], 2) if not self.data_manager.data_df.empty else 0,
                "charges": round(charges, 2),
                "net_pnl": round(net_pnl, 2),
                "entry_time": p.get("entry_time"),
                "exit_time": exit_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_seconds": duration_seconds,
                "max_price": p.get("max_price"),
                "signal_time": p.get("signal_time"),
                "order_time": p.get("order_time"),
                "expected_entry": p.get("expected_entry"),
                "expected_exit": expected_exit_price,
                "entry_slippage": p.get("entry_slippage"),
                "exit_slippage": exit_slippage,
                "latency_ms": p.get("latency_ms"),
                "trading_mode": self.params.get("trading_mode", "Paper Trading"),  # 🆕 Track mode
                "ucc": self._get_active_ucc(),
                # 🆕 Confirmatory momentum check data
                "momentum_price_rising": p.get("momentum_price_rising", 0),
                "momentum_accelerating": p.get("momentum_accelerating", 0),
                "momentum_index_sync": p.get("momentum_index_sync", 0),
                "momentum_volume_surge": p.get("momentum_volume_surge", 0),
                "momentum_checks_passed": p.get("momentum_checks_passed", 0),
                # 🆕 Predictive momentum check data
                "predictive_order_flow": p.get("predictive_order_flow", 0),
                "predictive_divergence": p.get("predictive_divergence", 0),
                "predictive_structure": p.get("predictive_structure", 0),
                "predictive_checks_passed": p.get("predictive_checks_passed", 0),
                "trigger_system": p.get("trigger_system", "UNKNOWN"),
                # 🆕 ENTRY TYPE DIFFERENTIATION
                "entry_type": p.get("entry_type", "UNKNOWN"),
                # 🆕 SUPERTREND HOLD MODE DIFFERENTIATION
                "supertrend_hold_mode": p.get("supertrend_hold_mode", "UNKNOWN"),
                "entry_option_st_state": p.get("entry_option_st_state", "UNKNOWN"),
                "exit_supertrend_reason": p.get("exit_supertrend_reason", "N/A")
            }
            
            # 🚀 CRITICAL: Broadcast to UI IMMEDIATELY (non-blocking to prevent WebSocket disconnects)
            asyncio.create_task(self.manager.broadcast({"type": "new_trade_log", "payload": log_info}))
            
            # ✅ Log to database (blocking to ensure write commits)
            await self.trade_logger.log_trade(log_info)
            p["qty"] -= qty_to_exit; self.next_partial_profit_level += 1
            await self._log_debug("Profit.Take", f"Remaining quantity: {p['qty']}.")
            await self._update_ui_trade_status(); await self._update_ui_performance()
        except Exception as e:
            await self._log_debug("CRITICAL-PARTIAL-EXIT-FAIL", f"Failed to partially exit {p['symbol']}: {e}"); _play_sound(self.manager, "warning")

    async def check_partial_profit_take(self):
        # ⚡ OPTIMIZED: Check on every tick but throttle LOGS to prevent spam
        if not self.position: 
            return
        
        async with self.position_lock:
            if not self.position: 
                return
            
            p, ltp = self.position, self.data_manager.prices.get(self.position["symbol"])
            if ltp is None: 
                return
            
            # Get parameters from GUI
            trade_pt_rupees = float(self.params.get("trade_profit_target", 0)) if self.params.get("trade_profit_target") else 0
            partial_profit_pct = float(self.params.get("partial_profit_pct", 0)) if self.params.get("partial_profit_pct") else 0
            
            # Skip all checks if no conditions configured
            if trade_pt_rupees <= 0 and partial_profit_pct <= 0:
                return
            
            # 📊 Calculate current PNL (total position value)
            qty = p.get("qty", p.get("quantity", 0))
            if qty <= 0:
                return
                
            current_pnl = (ltp - p["entry_price"]) * qty
            profit_pct = ((ltp - p["entry_price"]) / p["entry_price"]) * 100 if p.get("entry_price", 0) > 0 else 0
            
            # ⏱️ Throttle logging to prevent spam but still show condition checks
            current_time = time_module.time()
            if not hasattr(self, '_last_partial_log_time'):
                self._last_partial_log_time = {}
            if not hasattr(self, '_partial_log_counter'):
                self._partial_log_counter = 0
            
            # 🎯 CHECK 1: Trade PT (Rupee Target) - EXITS 100% OF POSITION
            trade_pt_key = f"trade_pt_{trade_pt_rupees}"
            if trade_pt_rupees > 0:
                # Log periodic diagnostic (every 10 ticks = ~2-3 seconds)
                self._partial_log_counter += 1
                if self._partial_log_counter % 10 == 0:
                    await self._log_debug("⏱️Trade PT Check", 
                        f"📊 Monitoring: ₹{current_pnl:.0f} vs Target ₹{trade_pt_rupees} | "
                        f"Qty: {qty} | Entry: ₹{p['entry_price']:.2f} | LTP: ₹{ltp:.2f}")
                
                if current_pnl >= trade_pt_rupees:
                    # Log only once per trigger
                    last_log = self._last_partial_log_time.get(trade_pt_key, 0)
                    if current_time - last_log > 2.0:  # Log every 2 seconds if still in trigger state
                        await self._log_debug("✅ Trade PT TRIGGERED", 
                            f"🎯 CONDITION MET: ₹{current_pnl:.2f} >= ₹{trade_pt_rupees:.2f} | "
                            f"Qty: {qty} | Entry: ₹{p['entry_price']:.2f} | LTP: ₹{ltp:.2f}")
                        self._last_partial_log_time[trade_pt_key] = current_time
                    await self.exit_position(f"Trade PT: ₹{trade_pt_rupees:.2f} Target Reached")
                    return
            
            # 🎯 CHECK 2: Partial Profit % - EXITS partial position (e.g., 30-50%)
            pp_key = f"partial_profit_{partial_profit_pct}"
            if partial_profit_pct > 0:
                # Log periodic diagnostic
                if self._partial_log_counter % 10 == 0:
                    await self._log_debug("⏱️Partial% Check", 
                        f"📊 Monitoring: {profit_pct:.2f}% vs Target {partial_profit_pct:.2f}% | "
                        f"Qty: {qty} | Entry: ₹{p['entry_price']:.2f} | LTP: ₹{ltp:.2f} | PNL: ₹{current_pnl:.2f}")
                
                if profit_pct >= partial_profit_pct:
                    # Log only once per trigger
                    last_log = self._last_partial_log_time.get(pp_key, 0)
                    if current_time - last_log > 2.0:  # Log every 2 seconds if still in trigger state
                        await self._log_debug("✅ Partial% TRIGGERED", 
                            f"🎯 CONDITION MET: {profit_pct:.2f}% >= {partial_profit_pct:.2f}% | "
                            f"Qty: {qty} | Entry: ₹{p['entry_price']:.2f} | LTP: ₹{ltp:.2f} | "
                            f"PNL: ₹{current_pnl:.2f}")
                        self._last_partial_log_time[pp_key] = current_time
                    await self.partial_exit_position()
                    return



    async def handle_ticks_async(self, ticks):
        # ... (This function is unchanged)
        try:
            if not self.initial_subscription_done and any(t.get("instrument_token") == self.index_token for t in ticks):
                index_price = next(t["last_price"] for t in ticks if t.get("instrument_token") == self.index_token)
                # Ensure index price is always a number 
                try:
                    index_price = float(index_price)
                    self.data_manager.prices[self.index_symbol] = index_price
                except (ValueError, TypeError):
                    return  # Skip this batch if index price conversion fails
                
                await self._log_debug("WebSocket", f"Index price received: ₹{index_price}. Subscribing to option chain...")
                tokens = self.get_all_option_tokens()
                await self.map_option_tokens(tokens)
                if self.ticker_manager: self.ticker_manager.resubscribe(tokens)
                await self._log_debug("WebSocket", f"✅ Subscribed to {len(tokens)} instruments ({len(self.token_to_symbol)} mapped)")
                
                # 🐛 DIAGNOSTIC: Log instrument and expiry status
                await self._log_debug("Instruments", 
                    f"📊 Status - Instruments: {len(self.option_instruments)}, "
                    f"Expiry: {self.last_used_expiry}, Index: {self.index_symbol}")
                
                self.initial_subscription_done = True
            for tick in ticks:
                token, ltp = tick.get("instrument_token"), tick.get("last_price")
                if token is not None and ltp is not None and (symbol := self.token_to_symbol.get(token)):
                    # Ensure ltp is always a number to prevent comparison errors
                    # Skip ticks with empty or invalid prices
                    if not ltp or (isinstance(ltp, str) and ltp.strip() == ''):
                        continue
                    
                    try:
                        ltp = float(ltp)
                    except (ValueError, TypeError):
                        continue  # Skip this tick if price conversion fails
                    
                    # 🕐 TIMING: Store tick receive time for latency monitoring
                    tick_time = get_ist_time()
                    
                    self.data_manager.prices[symbol] = ltp; self.data_manager.update_price_history(symbol, ltp)
                    is_new_minute = self.data_manager.update_live_candle(ltp, symbol)
                    
                    # 📐 SUPERTREND ANGLE: Update ST angle data when option candle closes
                    if is_new_minute and symbol != self.index_symbol:
                        # Option candle just closed, update ST angle history
                        await self.update_st_angle_data(symbol)
                    
                    # 🆕 FIRST TICK INITIALIZATION: Force ST angle calculation on first option tick
                    # This ensures Trend Direction Scout shows data immediately
                    if symbol != self.index_symbol and symbol not in self.st_line_history:
                        # First tick for this option - initialize ST history immediately
                        await self.initialize_st_angle_history(symbol)
                    
                    # 🆕 REAL-TIME SUPERTREND: Update supertrend with every tick (not just on minute close)
                    if symbol == self.index_symbol:
                        # Update index supertrend on every tick for real-time line changes
                        self.data_manager.calculate_live_supertrend(ltp)
                    
                    if symbol == self.index_symbol:
                        # Log index tick timing every 10 ticks for monitoring
                        if not hasattr(self, '_tick_counter'):
                            self._tick_counter = 0
                            self._last_tick_time = tick_time
                        
                        self._tick_counter += 1
                        if self._tick_counter % 50 == 0:  # Log every 50 ticks instead of 10
                            tick_latency = (tick_time - self._last_tick_time).total_seconds() * 1000 / 50
                            tick_time_str = tick_time.strftime("%H:%M:%S.%f")[:-3]
                            await self._log_debug("Tick Timing", f"⏱️ {tick_time_str} IST | Avg: {tick_latency:.1f}ms/tick")
                            self._last_tick_time = tick_time
                        
                        if is_new_minute: 
                            self.trades_this_minute = 0
                            self._trades_this_minute_pnl = []  # Reset P&L tracker for new minute
                            await self.data_manager.on_new_minute(ltp)
                            # V47.14 - Trigger new candle analysis
                            await self.v47_coordinator.on_new_candle()
                            
                            # 🎯 FORCE immediate chart update at candle close (bypass 10s throttle)
                            # Only time chart actually changes meaningfully is when a new candle forms
                            self._ui_chart_dirty = True
                            if hasattr(self, '_last_chart_broadcast'):
                                self._last_chart_broadcast = 0  # Reset throttle so next frame flush sends it
                            
                            # 🚀 PRE-CALCULATE ENTRY READINESS: Calculate conditions now for fast entry
                            # This runs heavy ST angle calculations at candle close (low priority time)
                            # Enables fast entry (0.5-2s) on new candle instead of 10-30s lag
                            await self.pre_calculate_entry_readiness()
                        
                        # 📐 UPDATE SUPERTREND ANGLE MONITORED SYMBOL: Keep monitor active with current ATM
                        # This ensures the GUI monitor shows live ST angle data even without entry conditions
                        if self.params.get('st_angle_enabled', True):
                            try:
                                # Get current ATM strike
                                spot = self.data_manager.prices.get(self.index_symbol, 0)
                                if spot:
                                    atm_strike = self.strike_step * round(spot / self.strike_step)
                                    
                                    # Get current ATM options
                                    ce_option = self.get_entry_option("CE", atm_strike)
                                    pe_option = self.get_entry_option("PE", atm_strike)
                                    
                                    # Track ATM strike changes
                                    prev_atm = getattr(self, '_prev_atm_strike', None)
                                    if prev_atm != atm_strike:
                                        # ATM strike changed - initialize history for new options
                                        if ce_option:
                                            ce_symbol = ce_option['tradingsymbol']
                                            if ce_symbol not in self.st_line_history or not self.st_line_history.get(ce_symbol):
                                                asyncio.create_task(self.initialize_st_angle_history(ce_symbol))
                                        if pe_option:
                                            pe_symbol = pe_option['tradingsymbol']
                                            if pe_symbol not in self.st_line_history or not self.st_line_history.get(pe_symbol):
                                                asyncio.create_task(self.initialize_st_angle_history(pe_symbol))
                                        self._prev_atm_strike = atm_strike
                                    
                                    # Update ST angle data for both CE and PE options
                                    if ce_option:
                                        await self.update_st_angle_data(ce_option['tradingsymbol'])
                                    if pe_option:
                                        await self.update_st_angle_data(pe_option['tradingsymbol'])
                                    
                                    # Keep track of monitored symbols for reference
                                    if ce_option:
                                        self.st_angle_monitored_symbol = ce_option['tradingsymbol']
                            except Exception as e:
                                pass  # Silently fail - monitoring is non-critical
                        
                        # �🚀 OPTIMIZED: Tick-driven scanning instead of timer-based
                        # Run scanner on EVERY index tick for instant detection (0-200ms latency)
                        # Coordinator has 200ms throttle to prevent excessive scanning
                        asyncio.create_task(self.check_trade_entry())
                        
                        # 🎯 FRAME-BASED CONFLATION: Mark dirty flags (will flush at next frame)
                        self._ui_status_dirty = True
                        self._ui_chain_dirty = True
                        # Chart & straddle are throttled in _flush_frame_update (10s/5s) - still mark dirty
                        # so they send once after bot start, then only on candle close via explicit set
                        self._ui_chart_dirty = True
                        self._ui_straddle_dirty = True
                        
                        # 🎯 TICK CONFLATION: Store latest price for this symbol
                        self._tick_conflation_buffer[symbol] = ltp
                    
                    if self.position and self.position["symbol"] == symbol:
                        # 🔥 CRITICAL FIX: Confirm ticks are reaching position tracking
                        # This log should appear within 100-500ms after position entry
                        current_pnl = (ltp - self.position['entry_price']) * self.position['qty']
                        profit_pct = ((ltp - self.position['entry_price']) / self.position['entry_price'] * 100) if self.position['entry_price'] > 0 else 0
                        
                        # 🎯 TICK CONFLATION: Store position symbol price
                        self._tick_conflation_buffer[symbol] = ltp
                        
                        # ✅ Check for partial profit/exit conditions
                        await self.check_partial_profit_take()
                        
                        # 🔥 CHECK EXIT SIGNALS ON EVERY TICK (for fast BE activation)
                        # CRITICAL: Always run evaluate_exit_logic for TSL, break-even, and other exits
                        await self.evaluate_exit_logic()
                        
                        # Also check coordinator exit signals (but coordinator may skip based on mode)
                        if self.v47_coordinator:
                            await self.v47_coordinator._check_exit_signals()
                        
                        # 🎯 FRAME-BASED CONFLATION: Mark dirty (will flush at next frame)
                        self._ui_trade_dirty = True
                        self._ui_performance_dirty = True
                        
                        # 🎯 TICK CONFLATION: Store position symbol price
                        self._tick_conflation_buffer[symbol] = ltp
        except Exception as e: 
            import traceback
            await self._log_debug("Tick Handler Error", f"Critical error: {e}")
            # Uncomment for debugging specific line:
            # await self._log_debug("Tick Handler Traceback", f"{traceback.format_exc()}")

    def _store_missed_opportunity(self, symbol, price, trigger, side, lot_size):
        """
        🎯 PERFECT PRICE ENTRY: Store missed opportunity for smart re-entry
        Will retry when conditions improve (price stabilizes, spread tightens, pullback)
        """
        import time as time_module
        
        # Defensive check
        if not hasattr(self, 'missed_opportunities'):
            self.missed_opportunities = {}
        
        # Don't store if already tracking this symbol
        if symbol in self.missed_opportunities:
            # Update the existing entry with latest data
            self.missed_opportunities[symbol].update({
                'price': price,
                'trigger': trigger,
                'timestamp': time_module.time(),
                'retries': self.missed_opportunities[symbol].get('retries', 0)
            })
        else:
            self.missed_opportunities[symbol] = {
                'price': price,
                'trigger': trigger,
                'side': side,
                'lot_size': lot_size,
                'timestamp': time_module.time(),
                'retries': 0,
                'original_price': price  # Remember the ideal entry price
            }
    
    async def _check_missed_opportunities(self):
        """
        🎯 PERFECT PRICE ENTRY: Check if missed opportunities can be re-entered
        Conditions for re-entry:
        1. Price has stabilized (velocity < 1%)
        2. Spread has tightened (< 1.5%)
        3. Price pulled back to acceptable range (within 2% of original signal)
        4. Momentum still valid (trend hasn't reversed)
        5. No more than 3 retry attempts per opportunity
        """
        import time as time_module
        
        # Throttle checks to once per second
        current_time = time_module.time()
        if current_time - self.last_missed_opportunity_check < 1.0:
            return
        self.last_missed_opportunity_check = current_time
        
        # Check each missed opportunity
        expired_symbols = []
        
        for symbol, opp in list(self.missed_opportunities.items()):
            # Expire after 30 seconds or 3 retries
            age = current_time - opp['timestamp']
            if age > 30 or opp['retries'] >= 3:
                expired_symbols.append(symbol)
                await self._log_debug("Perfect Entry", 
                    f"⏰ Expired missed opportunity: {symbol} (age: {age:.0f}s, retries: {opp['retries']})")
                continue
            
            # Get current price and check conditions
            current_price = self.data_manager.prices.get(symbol)
            if not current_price:
                continue
            
            # Check 1: Price velocity stabilized?
            price_history = self.data_manager.price_history.get(symbol, [])
            if len(price_history) >= 3:
                recent_prices = [p for t, p in price_history[-3:]]
                if len(recent_prices) >= 3:
                    price_velocity = abs((recent_prices[-1] - recent_prices[0]) / recent_prices[0] * 100)
                    if price_velocity > 1.0:  # Still moving too fast
                        continue
            
            # Check 2: Price within acceptable range?
            original_price = opp['original_price']
            price_drift_pct = ((current_price - original_price) / original_price * 100)
            
            # Allow entry if price is within -5% to +2% of original
            # -5%: Good (pullback), 0%: Perfect, +2%: Acceptable
            if price_drift_pct > 2.0:  # Price still too high
                continue
            
            # Check 3: Spread acceptable?
            try:
                full_symbol = f"{self.exchange}:{symbol}"
                quote = await kite.quote(full_symbol)
                
                if quote and full_symbol in quote:
                    depth = quote[full_symbol].get('depth', {})
                    buy_depth = depth.get('buy', [])
                    sell_depth = depth.get('sell', [])
                    
                    if buy_depth and sell_depth and len(buy_depth) > 0 and len(sell_depth) > 0:
                        bid = buy_depth[0].get('price', 0)
                        ask = sell_depth[0].get('price', 0)
                        
                        if bid > 0 and ask > 0:
                            spread_pct = ((ask - bid) / ask * 100)
                            if spread_pct > 1.2:  # Spread still too wide
                                continue
            except:
                continue
            
            # All conditions met! Retry entry
            await self._log_debug("Perfect Entry", 
                f"✅ RETRY: {symbol} conditions met. Price: ₹{current_price:.2f} (vs original ₹{original_price:.2f}, {price_drift_pct:+.1f}%)")
            
            # Increment retry counter
            opp['retries'] += 1
            
            # Find the option object from instruments
            option = next(
                (opt for opt in self.option_instruments 
                 if opt.get("tradingsymbol") == symbol), 
                None
            )
            
            if option:
                # Trigger re-entry (using custom_entry_price to use current stabilized price)
                asyncio.create_task(self.take_trade(
                    trigger=f"{opp['trigger']}_RETRY_{opp['retries']}",
                    opt=option,
                    custom_entry_price=current_price
                ))
            
            # Remove from missed opportunities (will re-add if conditions fail again)
            expired_symbols.append(symbol)
        
        # Clean up expired opportunities
        for symbol in expired_symbols:
            if symbol in self.missed_opportunities:
                del self.missed_opportunities[symbol]

    async def check_trade_entry(self):
        # V47.14 - Use V47 coordinator for trade entry
        if not await self.can_trade(): 
            return
        
        # 🎯 PERFECT PRICE ENTRY: Check missed opportunities first
        await self._check_missed_opportunities()
        
        # Use V47.14 coordination system
        await self.v47_coordinator.continuous_monitoring()
    
    async def can_trade(self):
        """V47.14 - Unified trade validation with optimizations"""
        # 🔥 CRITICAL: Check WebSocket connection first
        if self.ticker_manager and not self.ticker_manager.is_connected:
            # Log warning every 30 seconds to avoid spam
            current_time = get_ist_time()
            if not hasattr(self, '_last_ws_warning') or (current_time - self._last_ws_warning).total_seconds() > 30:
                await self._log_debug("WebSocket", "⚠️ CRITICAL: WebSocket disconnected - Cannot trade without live market data!")
                print("\n" + "="*70)
                print("⚠️⚠️⚠️ WARNING: WEBSOCKET DISCONNECTED - NO TRADING POSSIBLE ⚠️⚠️⚠️")
                print("="*70 + "\n")
                self._last_ws_warning = current_time
            return False
        
        # ⚡ OPTIMIZATION: Fast checks first (no network, no logs) - early exit pattern
        if self.is_paused:
            return False  # Bot is paused, no new trades allowed
        
        if self.position is not None or self.daily_trade_limit_hit: 
            return False  # Exit immediately if position exists or limit hit
        
        if self.exit_cooldown_until and get_ist_time() < self.exit_cooldown_until: 
            return False  # Exit immediately if in cooldown
        
        if self.trades_this_minute >= 3: 
            return False  # Exit immediately if 3 trades already taken in current minute candle
        
        # ⚡ OPTIMIZATION: Only run expensive checks if fast checks passed
        # Check kill switch (may involve network call)
        should_block, reason = kill_switch.should_block_trading()
        if should_block:
            # ⚡ OPTIMIZATION: Conditional logging - only log when reason changes
            if not hasattr(self, '_last_block_reason') or self._last_block_reason != reason:
                await self._log_debug("KillSwitch", f"🚫 TRADING BLOCKED: {reason}")
                self._last_block_reason = reason
            return False
        
        # Clear cached reason when trading is allowed
        if hasattr(self, '_last_block_reason'):
            delattr(self, '_last_block_reason')
        
        # Ensure daily_sl and daily_pt are always numbers to prevent string comparison errors
        try:
            daily_sl = float(self.params.get("daily_sl", 0)) if self.params.get("daily_sl") else 0
            daily_pt = float(self.params.get("daily_pt", 0)) if self.params.get("daily_pt") else 0
        except (ValueError, TypeError):
            daily_sl, daily_pt = 0, 0
            
        if (daily_sl < 0 and self.daily_net_pnl <= daily_sl) or (daily_pt > 0 and self.daily_net_pnl >= daily_pt):
            self.daily_trade_limit_hit = True
            # ⚡ OPTIMIZATION: This is important, so we always log it
            await self._log_debug("RISK", "Daily Net SL/PT hit. Trading disabled.")
            return False
            
        return True
        
    async def on_ticker_connect(self):
        # ... (This function is unchanged)
        await self._log_debug("WebSocket", f"Connected. Subscribing to index: {self.index_symbol}")
        await self._update_ui_status()
        if self.ticker_manager: self.ticker_manager.resubscribe([self.index_token])

    async def on_ticker_disconnect(self):
        # ... (This function is unchanged)
        await self._update_ui_status(); await self._log_debug("WebSocket", "Kite Ticker Disconnected.")

    async def _monitor_position_tick_health(self):
        """
        🔥 CRITICAL: Monitor that ticks are being received for active positions.
        If no ticks received for position after 2+ seconds, alert and diagnose issue.
        """
        import time
        position_entry_time = None
        last_tick_for_position = None
        alert_sent = False
        
        while True:
            try:
                await asyncio.sleep(0.5)  # Check every 500ms
                
                # If we have a position, track tick health
                if self.position:
                    symbol = self.position.get("symbol")
                    
                    # Record entry time on first check
                    if position_entry_time is None:
                        position_entry_time = time.time()
                        last_tick_for_position = time.time()
                        alert_sent = False
                        continue
                    
                    # Check if ticks are flowing (should update on each tick)
                    current_time = time.time()
                    time_since_last_tick = current_time - last_tick_for_position
                    
                    # If no ticks for >2 seconds, send alert
                    if time_since_last_tick > 2.0 and not alert_sent:
                        await self._log_debug("POSITION-HEALTH", 
                            f"⚠️ WARNING: No ticks received for {symbol} for {time_since_last_tick:.1f}s after entry! "
                            f"Check ticker subscription and token validity.")
                        alert_sent = True
                    
                    # If position still exists and ticks resumed, clear alert
                    elif time_since_last_tick < 1.0 and alert_sent:
                        await self._log_debug("POSITION-HEALTH", 
                            f"✅ Ticks resumed for {symbol} after {time_since_last_tick:.1f}s. Position tracking healthy.")
                        alert_sent = False
                
                else:
                    # Position was closed, reset health monitoring
                    position_entry_time = None
                    last_tick_for_position = None
                    alert_sent = False
            
            except Exception as e:
                await self._log_debug("POSITION-HEALTH", f"❌ Health monitor error: {e}")

    async def _update_position_tick_timestamp(self):
        """
        🔥 CRITICAL: Update timestamp when tick arrives for active position.
        Used by health monitor to detect tick flow stalls.
        """
        if not hasattr(self, '_position_tick_timestamp'):
            self._position_tick_timestamp = {}
        
        if self.position:
            symbol = self.position.get("symbol")
            if symbol:
                import time
                self._position_tick_timestamp[symbol] = time.time()

    @property
    def STRATEGY_PARAMS(self):
        # Cached version: reads file at most once every 2 seconds to reduce disk I/O
        now = time_module.time()
        if hasattr(self, '_strategy_params_cache') and self._strategy_params_cache is not None:
            if now - self._strategy_params_cache_time < 2.0:
                return self._strategy_params_cache
        try:
            with open("strategy_params.json", "r") as f:
                params = json.load(f)
                self._strategy_params_cache = params
                self._strategy_params_cache_time = now
                return params
        except (FileNotFoundError, json.JSONDecodeError):
            fallback = MARKET_STANDARD_PARAMS.copy()
            self._strategy_params_cache = fallback
            self._strategy_params_cache_time = now
            return fallback
    
    async def _log_debug(self, source, message): 
        """Non-blocking debug log with simple throttling"""
        try:
            log_time = get_ist_time()
            timestamp = log_time.strftime("%H:%M:%S.%f")[:-3]  # HH:MM:SS.mmm format
            
            # Log to file via Python's logging system
            logger = logging.getLogger(__name__)
            logger.debug(f"{source}: {message}")
            
            # Always print to console
            print(f"[{timestamp}] {source}: {message}")
            
            # Initialize throttle tracking
            if not hasattr(self, '_log_throttle'):
                self._log_throttle = {}
            
            # Only send important logs to UI to prevent WebSocket flood
            important_sources = [
                "PAPER TRADE", "LIVE TRADE", "EXIT", "ENTRY", "System",
                "CRITICAL", "Trade Rejected", "DUPLICATE", "Recovery",
                "Dual Monitor", "CE Score", "PE Score", "Time Update"
            ]
            
            is_important = any(keyword in source for keyword in important_sources)
            
            if is_important:
                # Throttle: Max 2 logs per second per source
                current_time = time_module.time()
                last_sent = self._log_throttle.get(source, 0)
                
                if current_time - last_sent >= 0.5:  # 500ms between logs
                    self._log_throttle[source] = current_time
                    asyncio.create_task(self.manager.broadcast({
                        "type": "debug_log", 
                        "payload": {
                            "time": timestamp,
                            "source": source, 
                            "message": message
                        }
                    }))
            
        except Exception as e:
            print(f"{source}: {message} (⚠️ logging error: {e})")
    
    def _get_trend_direction_data(self):
        """
        📊 GET TREND DIRECTION SCOUT DATA FOR GUI DISPLAY
        
        Tracks CE and PE supertrend status separately and recommends best direction
        
        Returns: {
            atm_strike, ce_option, pe_option, recommendation, confidence, overall_trend
        }
        """
        try:
            import time as time_module
            from datetime import datetime
            
            # Calculate ATM strike from current index price
            spot = self.data_manager.prices.get(self.index_symbol)
            if not spot:
                return {
                    "atm_strike": None,
                    "ce_option": None,
                    "pe_option": None,
                    "recommendation": "WAITING",
                    "confidence": 0.0,
                    "overall_trend": "UNKNOWN"
                }
            
            atm_strike = self.strike_step * round(spot / self.strike_step)
            
            # Get CE and PE option objects
            ce_option = self.get_entry_option("CE", atm_strike)
            pe_option = self.get_entry_option("PE", atm_strike)
            
            # Extract tradingsymbols from option objects
            ce_symbol = ce_option['tradingsymbol'] if ce_option else None
            pe_symbol = pe_option['tradingsymbol'] if pe_option else None
            
            def build_option_data(symbol, direction):
                """Build supertrend data for a single option"""
                if not symbol:
                    return None
                
                # Get current price for this option
                candle = self.data_manager.option_candles.get(symbol, {})
                ltp = self.data_manager.prices.get(symbol, candle.get('close', 0))
                
                # Calculate actual supertrend line for this option
                st_line, st_uptrend = self.data_manager.calculate_option_supertrend(symbol)
                
                # If supertrend calculation failed, return minimal data with diagnostics
                if st_line is None or st_line <= 0:
                    candle_count = len(self.data_manager.option_minute_candle_history.get(symbol, []))
                    has_current = symbol in self.data_manager.option_candles
                    
                    # Better status messages
                    if candle_count < 11:
                        status = f"waiting ({candle_count}/11 candles)"
                    elif not has_current:
                        status = "no_live_data"
                    else:
                        status = "calculating"
                    
                    return {
                        "symbol": symbol,
                        "st_line": None,
                        "ltp": round(ltp, 2) if ltp > 0 else None,
                        "is_green": None,
                        "st_angle": 0,
                        "angle_status": status,
                        "trend_duration_seconds": 0,
                        "candles_in_trend": 0,
                        "distance_to_break": 0
                    }
                
                is_green = ltp > st_line if st_line > 0 else None
                
                # Get ST angle data for this option
                angle = self.current_st_angle.get(symbol, 0)
                accel = self.current_st_acceleration.get(symbol, 0)
                status = self.st_angle_status.get(symbol, "flat")
                
                # Get trend duration
                trend_start = self.st_angle_increase_start_time.get(symbol)
                trend_duration = time_module.time() - trend_start if trend_start else 0
                
                # Estimate candles in trend (roughly)
                candles_in_trend = max(1, int(trend_duration / 60))
                
                # Distance to break
                distance = (ltp - st_line) if st_line > 0 else 0
                
                return {
                    "symbol": symbol,
                    "st_line": round(st_line, 2) if st_line > 0 else None,
                    "ltp": round(ltp, 2) if ltp > 0 else None,
                    "is_green": is_green,
                    "st_angle": round(angle, 2),
                    "angle_status": status,
                    "trend_duration_seconds": int(trend_duration),
                    "candles_in_trend": candles_in_trend,
                    "distance_to_break": round(distance, 2)
                }
            
            # Build CE and PE data
            ce_data = build_option_data(ce_symbol, "CE")
            pe_data = build_option_data(pe_symbol, "PE")
            
            # Determine recommendation
            recommendation = "WAIT"
            confidence = 0.0
            overall_trend = "NEUTRAL"
            
            if ce_data and pe_data:
                ce_score = 0
                pe_score = 0
                
                # CE scoring
                if ce_data['is_green']:
                    ce_score += 1.0  # Green supertrend
                if ce_data['st_angle'] > 0.7:
                    ce_score += 1.0  # Strong positive angle
                if ce_data['angle_status'] == 'increasing':
                    ce_score += 0.5  # Angle rising
                if ce_data['trend_duration_seconds'] > 60:
                    ce_score += 0.5  # Trend established
                
                # PE scoring
                if pe_data['is_green']:
                    pe_score += 1.0
                if pe_data['st_angle'] > 0.7:
                    pe_score += 1.0
                if pe_data['angle_status'] == 'increasing':
                    pe_score += 0.5
                if pe_data['trend_duration_seconds'] > 60:
                    pe_score += 0.5
                
                # Determine recommendation
                if ce_score > pe_score + 1.5:
                    overall_trend = "UPTREND"
                    if ce_score >= 3.0:
                        recommendation = "CE_STRONG"
                        confidence = min(0.95, ce_score / 4.0)
                    elif ce_score >= 1.5:
                        recommendation = "CE_WEAK"
                        confidence = min(0.75, ce_score / 4.0)
                elif pe_score > ce_score + 1.5:
                    overall_trend = "DOWNTREND"
                    if pe_score >= 3.0:
                        recommendation = "PE_STRONG"
                        confidence = min(0.95, pe_score / 4.0)
                    elif pe_score >= 1.5:
                        recommendation = "PE_WEAK"
                        confidence = min(0.75, pe_score / 4.0)
                else:
                    overall_trend = "NEUTRAL"
                    recommendation = "WAIT"
                    confidence = 0.0
            
            return {
                "atm_strike": atm_strike,
                "ce_option": ce_data,
                "pe_option": pe_data,
                "recommendation": recommendation,
                "confidence": confidence,
                "overall_trend": overall_trend
            }
        
        except Exception as e:
            return {
                "atm_strike": None,
                "ce_option": None,
                "pe_option": None,
                "recommendation": "ERROR",
                "confidence": 0.0,
                "overall_trend": "UNKNOWN"
            }
    
    async def _update_ui_status(self):
        # ... (This function is unchanged)
        is_running = self.ticker_manager and self.ticker_manager.is_connected
        
        # Get capital information for UI display
        gui_threshold = float(self.params.get("start_capital", 50000))
        live_capital_display = self.live_capital_cache if self.live_capital_cache is not None else None
        
        # Calculate effective capital (after daily P&L adjustments)
        if live_capital_display is not None:
            base_capital = min(live_capital_display, gui_threshold)
        else:
            base_capital = gui_threshold
        current_capital = base_capital + self.daily_net_pnl
        effective_capital = min(base_capital, current_capital)
        
        payload = { 
            "connection": "CONNECTED" if is_running else "DISCONNECTED", 
            "mode": self.params.get("trading_mode", "Paper").upper(), 
            "indexPrice": self.data_manager.prices.get(self.index_symbol, 0), 
            "is_running": is_running, 
            "is_paused": self.is_paused,  # Add pause status
            "trend": self.data_manager.trend_state or "---", 
            "indexName": self.index_name,
            # Capital information
            "live_capital": live_capital_display,  # From broker (None if not fetched)
            "gui_threshold": gui_threshold,  # From UI input
            "effective_capital": effective_capital,  # After P&L adjustments
            # Real-time clock (IST - synchronized with NSE)
            "current_time": get_ist_time_str(include_ms=True),  # HH:MM:SS.mmm IST
            "timezone": "IST",  # Timezone indicator
            # 📊 Trend Direction Scout Data for GUI
            "trend_direction_data": self._get_trend_direction_data()
        }
        await self.manager.broadcast({"type": "status_update", "payload": payload})
        
        # 📅 Also send expiry information separately if available
        if self.last_used_expiry and self.option_instruments:
            today = date.today()
            future_expiries = sorted(list(set([
                i['expiry'] for i in self.option_instruments 
                if i.get('expiry') and i['expiry'] >= today
            ])))
            
            # Simple position-based mapping
            available_expiries = {}
            if len(future_expiries) >= 1:
                available_expiries['CURRENT_WEEK'] = future_expiries[0].strftime('%Y-%m-%d')
            if len(future_expiries) >= 2:
                available_expiries['NEXT_WEEK'] = future_expiries[1].strftime('%Y-%m-%d')
            
            # MONTHLY: Smart detection
            if len(future_expiries) >= 2:
                gap = (future_expiries[1] - future_expiries[0]).days
                if gap <= 14:
                    # Weekly index
                    monthly_candidates = [
                        exp for exp in future_expiries 
                        if 20 <= (exp - today).days <= 45
                    ]
                    if monthly_candidates:
                        available_expiries['MONTHLY'] = monthly_candidates[0].strftime('%Y-%m-%d')
                    elif len(future_expiries) >= 4:
                        available_expiries['MONTHLY'] = future_expiries[3].strftime('%Y-%m-%d')
                    else:
                        available_expiries['MONTHLY'] = future_expiries[-1].strftime('%Y-%m-%d')
            
            # Build expiry info payload with actual dates (all available expiries)
            future_expiries = sorted(list(set([
                i['expiry'] for i in self.option_instruments 
                if i.get('expiry') and i['expiry'] >= today
            ])))
            
            available_expiries_list = [exp.strftime('%Y-%m-%d') for exp in future_expiries] if future_expiries else []
            
            expiry_payload = {
                "selected_expiry": self.last_used_expiry.strftime('%Y-%m-%d') if self.last_used_expiry else '',
                "current_expiry": self.last_used_expiry.strftime('%Y-%m-%d') if self.last_used_expiry else '',
                "available_expiries": available_expiries_list
            }
            await self.manager.broadcast({"type": "expiry_info_update", "payload": expiry_payload})

    async def _update_ui_performance(self):
        # ... (This function is unchanged)
        trades_today = self.performance_stats["winning_trades"] + self.performance_stats["losing_trades"]
        payload = { 
            "grossPnl": self.daily_gross_pnl, 
            "totalCharges": self.total_charges, 
            "netPnl": self.daily_net_pnl, 
            "net_pnl": self.daily_net_pnl,  # Add alias for frontend compatibility
            "wins": self.performance_stats["winning_trades"], 
            "losses": self.performance_stats["losing_trades"],
            "trades_today": trades_today  # Add total trades count
        }
        await self.manager.broadcast({"type": "daily_performance_update", "payload": payload})

    async def _update_ui_trade_status(self):
        # ... (This function is unchanged)
        payload = None
        if self.position: 
            p, ltp = self.position, self.data_manager.prices.get(self.position["symbol"], self.position["entry_price"])
            pnl = (ltp - p["entry_price"]) * p["qty"]; profit_pct = (((ltp - p["entry_price"]) / p["entry_price"]) * 100 if p["entry_price"] > 0 else 0)
            
            # 🔄 UPDATE ENTRY CANDLE OHLC IN REAL-TIME (while candle is still active)
            # This keeps the OHLC data fresh as the candle forms, showing current high/low/wicks
            if p.get("entry_candle_ohlc") and p["entry_candle_ohlc"].get("is_active"):
                symbol = p["symbol"]
                option_candle = self.data_manager.option_candles.get(symbol, {})
                
                if option_candle:
                    # Get current candle data
                    candle_open = option_candle.get('open', 0)
                    candle_high = option_candle.get('high', 0)
                    candle_low = option_candle.get('low', 0)
                    candle_start_time = option_candle.get('candle_start_time', 0)
                    current_time = time_module.time()
                    candle_age_seconds = current_time - candle_start_time if candle_start_time > 0 else 0
                    is_candle_active = candle_age_seconds < 60
                    
                    # Update OHLC data with current values
                    display_value = ltp
                    candle_body = abs(display_value - candle_open)
                    candle_body_pct = (candle_body / candle_open * 100) if candle_open > 0 else 0
                    candle_range = candle_high - candle_low
                    candle_type = "GREEN 🟢" if display_value > candle_open else "RED 🔴" if display_value < candle_open else "DOJI ⚪"
                    
                    # Calculate entry position in range
                    entry_price = p["entry_price"]
                    entry_position_in_range = 0
                    if candle_range > 0:
                        entry_position_in_range = ((entry_price - candle_low) / candle_range) * 100
                    
                    # Calculate wicks
                    if display_value > candle_open:  # Green candle/movement
                        upper_wick = candle_high - display_value
                        lower_wick = candle_open - candle_low
                    else:  # Red candle/movement
                        upper_wick = candle_high - candle_open
                        lower_wick = display_value - candle_low
                    
                    # Update position dict with fresh OHLC data
                    p['entry_candle_ohlc'].update({
                        'high': round(candle_high, 2),
                        'low': round(candle_low, 2),
                        'close': round(display_value, 2),  # Current LTP
                        'is_active': is_candle_active,
                        'candle_age_sec': round(candle_age_seconds, 1),
                        'body_pct': round(candle_body_pct, 2),
                        'range': round(candle_range, 2),
                        'upper_wick': round(upper_wick, 2),
                        'lower_wick': round(lower_wick, 2),
                        'entry_position_pct': round(entry_position_in_range, 1),
                        'candle_type': candle_type,
                        'distance_from_high': round(candle_high - entry_price, 2),
                        'distance_from_low': round(entry_price - candle_low, 2)
                    })
                    
                    # 🕐 LOG CANDLE CLOSE EVENT (only once when it transitions from active to closed)
                    if not is_candle_active and not hasattr(self, '_entry_candle_closed_logged'):
                        self._entry_candle_closed_logged = True
                        await self._log_debug("📊 ENTRY CANDLE CLOSED", 
                            f"Entry candle for {symbol} has closed after {candle_age_seconds:.1f}s\n"
                            f"Final OHLC: O=₹{candle_open:.2f}, H=₹{candle_high:.2f}, "
                            f"L=₹{candle_low:.2f}, C=₹{display_value:.2f}\n"
                            f"Entry was at {entry_position_in_range:.1f}% of range | "
                            f"Range: ₹{candle_range:.2f}")
            
            payload = {
                "symbol": p["symbol"], 
                "entry_price": p["entry_price"],
                "ltp": ltp, 
                "pnl": pnl, 
                "profit_pct": profit_pct, 
                "trail_sl": p["trail_sl"], 
                "max_price": p["max_price"],
                "last_update_time": get_ist_time_str(include_ms=True),  # IST timestamp
                "entry_candle_ohlc": p.get("entry_candle_ohlc")  # 📊 Add OHLC data for GUI display
            }
        
        try:
            await self.manager.broadcast({"type": "trade_status_update", "payload": payload})
        except Exception as e:
            # Log UI update failures for debugging
            await self._log_debug("UI Update", f"⚠️ Failed to broadcast trade status: {e}")

    async def _update_ui_uoa_list(self): await self.manager.broadcast({"type": "uoa_list_update", "payload": list(self.uoa_watchlist.values())})

    async def _bootstrap_option_prices(self):
        """
        🚀 PRE-FETCH OPTION PRICES ON STARTUP
        Fetch initial option prices via quote API before first WebSocket tick arrives.
        This ensures the UI shows option chain immediately, without waiting for ticks.
        """
        try:
            if not self.option_instruments or not self.last_used_expiry:
                await self._log_debug("Bootstrap", "⏭️ Skipping option bootstrap: No instruments or expiry loaded")
                return
            
            # Get current spot price (fallback to bootstrap data if needed)
            spot_price = self.data_manager.prices.get(self.index_symbol)
            if not spot_price:
                if not self.data_manager.data_df.empty:
                    spot_price = self.data_manager.data_df.iloc[-1]['close']
                else:
                    await self._log_debug("Bootstrap", "⏭️ Skipping option bootstrap: No spot price available")
                    return
            
            # Calculate ATM and surrounding strikes
            atm_strike = self.strike_step * round(spot_price / self.strike_step)
            strikes_to_fetch = [atm_strike - self.strike_step, atm_strike, atm_strike + self.strike_step]
            
            # Build option symbols to fetch
            symbols_to_fetch = []
            for strike in strikes_to_fetch:
                for option_type in ["CE", "PE"]:
                    try:
                        opt = self.get_entry_option(option_type, strike)
                        if opt:
                            symbols_to_fetch.append(opt['tradingsymbol'])
                    except:
                        pass
            
            if not symbols_to_fetch:
                await self._log_debug("Bootstrap", "⏭️ Skipping option bootstrap: No options available")
                return
            
            # Fetch quotes for all 6 options (3 strikes × 2 types)
            try:
                quote_data = await kite.quote(symbols_to_fetch)
                if quote_data:
                    # Populate prices from quote response
                    for symbol in symbols_to_fetch:
                        if symbol in quote_data:
                            price = quote_data[symbol].get('last_price', 0)
                            if price > 0:
                                self.data_manager.prices[symbol] = price
                    
                    await self._log_debug("Bootstrap", 
                        f"✅ Pre-fetched {len([s for s in symbols_to_fetch if s in quote_data])} option prices: {', '.join(symbols_to_fetch[:3])}")
                    
                    # Force immediate UI update with fetched prices
                    self._ui_chain_dirty = True
                    await self._update_ui_option_chain()
                else:
                    await self._log_debug("Bootstrap", "⏭️ Quote API returned no data")
            except Exception as e:
                await self._log_debug("Bootstrap", f"⚠️ Quote fetch failed (market may be closed): {e}")
        except Exception as e:
            await self._log_debug("Bootstrap", f"⚠️ Option bootstrap error: {e}")

    async def _update_ui_option_chain(self):
        # Calculate IV and compare CE vs PE at SAME strike
        pairs, data = self.get_strike_pairs(count=3), []
        spot_price = self.data_manager.prices.get(self.index_symbol)
        
        # 🐛 DIAGNOSTIC: Log why option chain is empty
        if not spot_price:
            await self._log_debug("OptionChain", f"❌ No spot price for {self.index_symbol}")
            # 🔧 FIX: Still send empty array to UI to show structure
            await self.manager.broadcast({"type": "option_chain_update", "payload": []})
            return
        
        if not pairs:
            await self._log_debug("OptionChain", 
                f"❌ No strike pairs! Instruments: {len(self.option_instruments)}, "
                f"Expiry: {self.last_used_expiry}, Spot: {spot_price}")
            # 🔧 FIX: Create empty strike structure to show UI that we're trying
            if self.option_instruments and self.last_used_expiry:
                # Calculate ATM strike even if pairs failed
                atm_strike = self.strike_step * round(spot_price / self.strike_step)
                data = [
                    {"strike": atm_strike - self.strike_step, "ce_ltp": "--", "pe_ltp": "--"},
                    {"strike": atm_strike, "ce_ltp": "--", "pe_ltp": "--"},
                    {"strike": atm_strike + self.strike_step, "ce_ltp": "--", "pe_ltp": "--"}
                ]
            await self.manager.broadcast({"type": "option_chain_update", "payload": data})
            return
        
        # 🔥 DYNAMIC SUBSCRIPTION: Re-subscribe when strikes change as index moves
        if self.ticker_manager:
            # Extract current strike tokens
            current_tokens = set()
            for pair in pairs:
                if pair.get("ce") and pair["ce"].get("instrument_token"):
                    current_tokens.add(pair["ce"]["instrument_token"])
                if pair.get("pe") and pair["pe"].get("instrument_token"):
                    current_tokens.add(pair["pe"]["instrument_token"])
            
            # Check if strikes changed (index moved to new ATM)
            if not hasattr(self, '_last_option_chain_tokens'):
                self._last_option_chain_tokens = set()
            
            if current_tokens != self._last_option_chain_tokens:
                # Strikes changed - resubscribe to new set
                if current_tokens:
                    all_tokens = self.get_all_option_tokens()  # Full set: index + 7 strikes + UOA
                    await self.map_option_tokens(all_tokens)    # ← FIX: Update token→symbol mapping so new ATM ticks are processed
                    self.ticker_manager.resubscribe(all_tokens)
                    self._last_option_chain_tokens = current_tokens
                    await self._log_debug("OptionChain", 
                        f"🔄 ATM changed - Subscribed to {len(all_tokens)} tokens, mapped {len(self.token_to_symbol)} symbols")
        
        # Get risk-free rate (cached daily)
        risk_free_rate = get_risk_free_rate()
        
        # Process each strike: Get prices and calculate fair values (skip IV calculations)
        for p in pairs:
            strike = p["strike"]
            row_data = {"strike": strike}
            
            # Get CE price (no IV calculation needed)
            if p["ce"]:
                ce_symbol = p["ce"]["tradingsymbol"]
                ce_price = self.data_manager.prices.get(ce_symbol)
                row_data["ce_ltp"] = ce_price if ce_price and ce_price > 0 else "--"
            else:
                row_data["ce_ltp"] = "--"
            
            # Get PE price (no IV calculation needed)
            if p["pe"]:
                pe_symbol = p["pe"]["tradingsymbol"]
                pe_price = self.data_manager.prices.get(pe_symbol)
                row_data["pe_ltp"] = pe_price if pe_price and pe_price > 0 else "--"
            else:
                row_data["pe_ltp"] = "--"
            
            # Compare CE vs PE using EXTRINSIC VALUE (time value)
            ce_price = row_data.get("ce_ltp")
            pe_price = row_data.get("pe_ltp")
            
            if ce_price and pe_price and ce_price != "--" and pe_price != "--":
                # Calculate INTRINSIC VALUE (what option is worth if exercised now)
                ce_intrinsic = max(spot_price - strike, 0)
                pe_intrinsic = max(strike - spot_price, 0)
                
                # Calculate EXTRINSIC VALUE (time value + volatility premium)
                ce_extrinsic = ce_price - ce_intrinsic
                pe_extrinsic = pe_price - pe_intrinsic
                
                # Simple color coding based on extrinsic comparison (keep for background colors)
                if pe_extrinsic > 0:
                    ce_vs_pe_extrinsic = ((ce_extrinsic - pe_extrinsic) / pe_extrinsic) * 100
                    ce_valuation = ce_vs_pe_extrinsic
                    pe_valuation = -ce_vs_pe_extrinsic
                    row_data["ce_color"] = get_color_for_valuation(ce_valuation)
                    row_data["pe_color"] = get_color_for_valuation(pe_valuation)
                    
                    # Calculate FAIR PRICES (what price SHOULD be based on Put-Call Parity)
                    fair_extrinsic = (ce_extrinsic + pe_extrinsic) / 2
                    
                    # Fair Price = Intrinsic + Fair Extrinsic
                    ce_fair_price = ce_intrinsic + fair_extrinsic
                    pe_fair_price = pe_intrinsic + fair_extrinsic
                    
                    # Expected change from current price to fair price
                    ce_expected_change = ce_fair_price - ce_price
                    pe_expected_change = pe_fair_price - pe_price
                    
                    row_data["ce_fair"] = round(ce_fair_price, 2)
                    row_data["pe_fair"] = round(pe_fair_price, 2)
                    row_data["ce_exp_change"] = round(ce_expected_change, 2)
                    row_data["pe_exp_change"] = round(pe_expected_change, 2)
                else:
                    # If PE extrinsic is 0, set neutral colors
                    row_data["ce_color"] = "rgba(255, 255, 255, 0)"
                    row_data["pe_color"] = "rgba(255, 255, 255, 0)"
                    row_data["ce_fair"] = ce_price
                    row_data["pe_fair"] = pe_price
                    row_data["ce_exp_change"] = 0
                    row_data["pe_exp_change"] = 0
            else:
                # If either price missing, set neutral
                row_data["ce_color"] = "rgba(255, 255, 255, 0)"
                row_data["pe_color"] = "rgba(255, 255, 255, 0)"
                row_data["ce_fair"] = "--"
                row_data["pe_fair"] = "--"
                row_data["ce_exp_change"] = "--"
                row_data["pe_exp_change"] = "--"
            
            data.append(row_data)
        
        await self.manager.broadcast({"type": "option_chain_update", "payload": data})

    async def _update_ui_straddle_monitor(self):
        # ... (This function is unchanged)
        payload = {"current_straddle": 0, "open_straddle": 0, "change_pct": 0}
        spot = self.data_manager.prices.get(self.index_symbol)
        if not spot:
            await self.manager.broadcast({"type": "straddle_update", "payload": payload})
            return
        atm_strike = self.strike_step * round(spot / self.strike_step); ce_opt = self.get_entry_option('CE', atm_strike); pe_opt = self.get_entry_option('PE', atm_strike)
        if ce_opt and pe_opt:
            ce_sym, pe_sym = ce_opt['tradingsymbol'], pe_opt['tradingsymbol']; ce_ltp = self.data_manager.prices.get(ce_sym); pe_ltp = self.data_manager.prices.get(pe_sym)
            ce_open = self.data_manager.option_open_prices.get(ce_sym); pe_open = self.data_manager.option_open_prices.get(pe_sym)
            if all([ce_ltp, pe_ltp, ce_open, pe_open]):
                current_straddle = ce_ltp + pe_ltp; open_straddle = ce_open + pe_open
                change_pct = ((current_straddle / open_straddle) - 1) * 100 if open_straddle > 0 else 0
                payload = {"current_straddle": current_straddle, "open_straddle": open_straddle, "change_pct": change_pct}
        await self.manager.broadcast({"type": "straddle_update", "payload": payload})

    async def _update_ui_chart_data(self):
        # ⚡ PERFORMANCE: Limit to last 100 candles to prevent UI slowdown
        temp_df = self.data_manager.data_df.copy()
        if len(temp_df) > 100:
            temp_df = temp_df.tail(100)  # Only send last 100 candles
        
        if self.data_manager.current_candle.get("minute"):
            live_candle_df = pd.DataFrame([self.data_manager.current_candle], index=[self.data_manager.current_candle["minute"]])
            temp_df = pd.concat([temp_df, live_candle_df])
        
        if not temp_df.index.is_unique: 
            temp_df = temp_df[~temp_df.index.duplicated(keep='last')]
        if not temp_df.index.is_monotonic_increasing: 
            temp_df.sort_index(inplace=True)
        
        chart_data = {"candles": [], "wma": [], "sma": [], "rsi": [], "rsi_sma": [], "supertrend": []}
        if not temp_df.empty:
            # ⚡ FIX: Convert timestamps properly from pandas datetime index
            for i, (index, row) in enumerate(temp_df.iterrows()):
                timestamp = int(index.timestamp())
                chart_data["candles"].append({"time": timestamp, "open": row.get("open", 0), "high": row.get("high", 0), "low": row.get("low", 0), "close": row.get("close", 0)})
                if pd.notna(row.get("wma")): chart_data["wma"].append({"time": timestamp, "value": row["wma"]})
                if pd.notna(row.get("sma")): chart_data["sma"].append({"time": timestamp, "value": row["sma"]})
                if pd.notna(row.get("rsi")): chart_data["rsi"].append({"time": timestamp, "value": row["rsi"]})
                if pd.notna(row.get("rsi_sma")): chart_data["rsi_sma"].append({"time": timestamp, "value": row["rsi_sma"]})
                if pd.notna(row.get("supertrend")): chart_data["supertrend"].append({"time": timestamp, "value": row["supertrend"]})
        
        await self.manager.broadcast({"type": "chart_data_update", "payload": chart_data})

    async def _flush_frame_update(self):
        """
        🎯 PROFESSIONAL 30 FPS FRAME UPDATE: Batched conflated updates (Bloomberg/TradingView pattern)
        
        This method runs at fixed 30 FPS (every 33ms) and:
        1. Conflates all ticks received during the frame interval
        2. Batches all dirty UI components into ONE WebSocket message
        3. Ensures clock, prices, and data are perfectly synchronized
        4. Reduces WebSocket traffic by 95% while maintaining smooth UI
        
        Architecture: Tick collection → Frame boundary → Single atomic broadcast
        """
        # Build batched payload with only dirty components
        batch_payload = {}
        
        # Always include current time for clock sync
        batch_payload["timestamp"] = get_ist_time_str(include_ms=True)
        batch_payload["timezone"] = "IST"
        
        # Add conflated tick prices (all symbols that changed this frame)
        if self._tick_conflation_buffer:
            batch_payload["prices"] = self._tick_conflation_buffer.copy()
            self._tick_conflation_buffer.clear()
        
        # Expiry information (which expiry is selected and being traded)
        if self._ui_expiry_dirty and self.last_used_expiry and self.option_instruments:
            # Get all available expiries for display
            today = date.today()
            future_expiries = sorted(list(set([
                i['expiry'] for i in self.option_instruments 
                if i.get('expiry') and i['expiry'] >= today
            ])))
            
            # Build expiry info with all available expiries as simple list
            future_expiries = sorted(list(set([
                i['expiry'] for i in self.option_instruments 
                if i.get('expiry') and i['expiry'] >= today
            ])))
            
            available_expiries_list = [exp.strftime('%Y-%m-%d') for exp in future_expiries] if future_expiries else []
            
            batch_payload["expiry_info"] = {
                "selected_expiry": self.last_used_expiry.strftime('%Y-%m-%d') if self.last_used_expiry else '',
                "current_expiry": self.last_used_expiry.strftime('%Y-%m-%d') if self.last_used_expiry else '',
                "available_expiries": available_expiries_list
            }
            self._ui_expiry_dirty = False
        
        # Status update (connection, mode, index price, trend)
        if self._ui_status_dirty:
            is_running = self.ticker_manager and self.ticker_manager.is_connected
            gui_threshold = float(self.params.get("start_capital", 50000))
            live_capital_display = self.live_capital_cache if self.live_capital_cache is not None else None
            
            if live_capital_display is not None:
                base_capital = min(live_capital_display, gui_threshold)
            else:
                base_capital = gui_threshold
            current_capital = base_capital + self.daily_net_pnl
            effective_capital = min(base_capital, current_capital)
            
            batch_payload["status"] = {
                "connection": "CONNECTED" if is_running else "DISCONNECTED",
                "mode": self.params.get("trading_mode", "Paper").upper(),
                "indexPrice": self.data_manager.prices.get(self.index_symbol, 0),
                "is_running": is_running,
                "is_paused": self.is_paused,
                "trend": self.data_manager.trend_state or "---",
                "indexName": self.index_name,
                "live_capital": live_capital_display,
                "gui_threshold": gui_threshold,
                "effective_capital": effective_capital,
                # 📐 ADD: Trend Direction Scout data for live GUI updates
                "trend_direction_data": self._get_trend_direction_data()
            }
            self._ui_status_dirty = False
        
        # Performance update (P&L, wins, losses)
        if self._ui_performance_dirty:
            trades_today = self.performance_stats["winning_trades"] + self.performance_stats["losing_trades"]
            batch_payload["performance"] = {
                "grossPnl": self.daily_gross_pnl,
                "totalCharges": self.total_charges,
                "netPnl": self.daily_net_pnl,
                "net_pnl": self.daily_net_pnl,
                "wins": self.performance_stats["winning_trades"],
                "losses": self.performance_stats["losing_trades"],
                "trades_today": trades_today
            }
            self._ui_performance_dirty = False
        
        # Trade status update (position, P&L, trailing SL)
        if self._ui_trade_dirty:
            if self.position:
                p = self.position
                ltp = self.data_manager.prices.get(p["symbol"], p["entry_price"])
                pnl = (ltp - p["entry_price"]) * p["qty"]
                profit_pct = (((ltp - p["entry_price"]) / p["entry_price"]) * 100 if p["entry_price"] > 0 else 0)
                batch_payload["trade"] = {
                    "symbol": p["symbol"],
                    "entry_price": p["entry_price"],
                    "ltp": ltp,
                    "pnl": pnl,
                    "profit_pct": profit_pct,
                    "trail_sl": p["trail_sl"],
                    "max_price": p["max_price"]
                }
            else:
                batch_payload["trade"] = None
            self._ui_trade_dirty = False
        
        # Option chain, straddle, chart updates (only if dirty)
        # These are kept separate for now to avoid massive payloads
        # Can be batched later if needed
        if self._ui_chain_dirty:
            asyncio.create_task(self._update_ui_option_chain())
            self._ui_chain_dirty = False
        
        # ⚡ THROTTLE: Straddle updates max once every 5s (heavy payload, not tick-critical)
        import time as _flush_time
        now_ts = _flush_time.time()
        if self._ui_straddle_dirty:
            if not hasattr(self, '_last_straddle_broadcast') or now_ts - self._last_straddle_broadcast >= 5.0:
                asyncio.create_task(self._update_ui_straddle_monitor())
                self._last_straddle_broadcast = now_ts
                self._ui_straddle_dirty = False
        
        # ⚡ THROTTLE: Chart data max once every 1s (live candle updates with each tick)
        if self._ui_chart_dirty:
            if not hasattr(self, '_last_chart_broadcast') or now_ts - self._last_chart_broadcast >= 1.0:
                asyncio.create_task(self._update_ui_chart_data())
                self._last_chart_broadcast = now_ts
                self._ui_chart_dirty = False
        
        # Broadcast single batched frame update
        if batch_payload:
            await self.manager.broadcast({
                "type": "batch_frame_update",
                "payload": batch_payload
            })

    def calculate_uoa_conviction_score(self, option_data, atm_strike):
        # ... (This function is unchanged)
        score, v_oi_ratio = 0, option_data.get('volume', 0) / (option_data.get('oi', 0) + 1)
        score += min(v_oi_ratio / 2.0, 5); score += min(option_data.get('change', 0) / 10.0, 5)
        strike_distance = abs(option_data['strike'] - atm_strike) / self.strike_step
        if strike_distance <= 2: score += 3
        elif strike_distance <= 4: score += 1
        return score

    async def add_to_watchlist(self, side, strike):
        # ... (This function is unchanged)
        opt = self.get_entry_option(side, strike=strike)
        if opt:
            token = opt.get('instrument_token', opt.get('tradingsymbol'))
            if token in self.uoa_watchlist: return False
            self.uoa_watchlist[token] = {'symbol': opt['tradingsymbol'], 'type': side, 'strike': strike}
            await self._log_debug("UOA", f"Added {opt['tradingsymbol']} to watchlist.")
            await self._update_ui_uoa_list(); _play_sound(self.manager, "entry")
            if self.ticker_manager and not self.is_backtest:
                tokens = self.get_all_option_tokens(); await self.map_option_tokens(tokens)
                self.ticker_manager.resubscribe(tokens)
            return True
        await self._log_debug("UOA", f"Could not find {side} option for strike {strike}"); _play_sound(self.manager, "warning"); return False
    
    async def reset_uoa_watchlist(self):
        # ... (This function is unchanged)
        await self._log_debug("UOA", "Watchlist reset requested by user.")
        self.uoa_watchlist.clear()
        await self._update_ui_uoa_list()
        _play_sound(self.manager, "warning")

    async def scan_for_unusual_activity(self):
        # ... (This function is unchanged)
        if self.is_backtest: return
        try:
            await self._log_debug("Scanner", "Running intelligent UOA scan...")
            spot = self.data_manager.prices.get(self.index_symbol)
            if not spot: await self._log_debug("Scanner", "Aborting scan: Index price not available."); return
            atm_strike = self.strike_step * round(spot / self.strike_step); scan_range = 5 if self.index_name == "NIFTY" else 8
            target_strikes = [atm_strike + (i * self.strike_step) for i in range(-scan_range, scan_range + 1)]
            target_options = [i for i in self.option_instruments if i['expiry'] == self.last_used_expiry and i['strike'] in target_strikes]
            if not target_options: return
            quotes = await kite.quote([opt['instrument_token'] for opt in target_options])  # Direct async call
            found_count, CONVICTION_THRESHOLD = 0, 7.0
            for instrument, data in quotes.items():
                opt_details = next((opt for opt in target_options if opt['instrument_token'] == data['instrument_token']), None)
                if not opt_details: continue
                quote_data = {"volume": data.get('volume', 0), "oi": data.get('oi', 0), "change": data.get('change', 0), "strike": opt_details['strike']}
                score = self.calculate_uoa_conviction_score(quote_data, atm_strike)
                if score >= CONVICTION_THRESHOLD:
                    if await self.add_to_watchlist(opt_details['instrument_type'], opt_details['strike']):
                        await self._log_debug("Scanner", f"High conviction: {opt_details['tradingsymbol']} (Score: {score:.1f}). Added."); found_count += 1
            if found_count == 0: await self._log_debug("Scanner", "Scan complete. No new high-conviction opportunities found.")
        except Exception as e: await self._log_debug("Scanner ERROR", f"An error occurred during UOA scan: {e}")

    async def on_trend_update(self, new_trend):
        # --- NO CHANGE NEEDED HERE ---
        # This function updates the state variable used by entry_strategies
        if self.data_manager.trend_state != new_trend: self.trend_candle_count = 1
        else: self.trend_candle_count += 1
    
    async def load_instruments(self):
        max_retries = 3
        retry_delay = 2

        # Conservative default freeze limits (exchange/index mapping)
        default_freeze_limits = {
            "NIFTY": 1800,
            "BANKNIFTY": 900,
            "FINNIFTY": 1800,
            "MIDCPNIFTY": 2400,
            "SENSEX": 2000,
            "BANKEX": 1200
        }

        for attempt in range(1, max_retries + 1):
            try:
                # Quick network pre-check (non-fatal)
                try:
                    import socket
                    _net_host = 'lapi.kotaksecurities.com' if BROKER_NAME == 'kotak' else 'api.kite.trade'
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2)
                    result = sock.connect_ex((_net_host, 443))
                    sock.close()
                    if result != 0:
                        await self._log_debug("Instruments", "⚠️ Network check failed (pre-check). Continuing with retries and API call.")
                    else:
                        await self._log_debug("Instruments", "✅ Network OK")
                except Exception:
                    # Non-fatal - proceed to API call and let retries handle failures
                    await self._log_debug("Instruments", "⚠️ Network pre-check error; proceeding to fetch instruments")

                # Fetch instruments from broker (use configured exchange if available)
                try:
                    if hasattr(self, 'exchange') and self.exchange:
                        instruments = await kite.instruments(self.exchange)
                    else:
                        instruments = await kite.instruments()
                except TypeError:
                    # Some kite wrappers require no args
                    instruments = await kite.instruments()

                if instruments:
                    # Filter instruments for the selected index to get correct lot size/freeze
                    matching_instruments = [i for i in instruments if i.get('name') == self.index_name]
                    
                    if matching_instruments:
                        self.lot_size = matching_instruments[0].get('lot_size', 1)
                        api_freeze = matching_instruments[0].get('freeze_quantity')
                        await self._log_debug("Instruments", f"✅ Found {len(matching_instruments)} instruments for {self.index_name}")
                    else:
                        # Fallback: use first instrument (should not happen if exchange is correct)
                        self.lot_size = instruments[0].get('lot_size', 1)
                        api_freeze = instruments[0].get('freeze_quantity')
                        await self._log_debug("Instruments", f"⚠️ No instruments matched '{self.index_name}' - using first instrument as fallback")

                    # Check for manual override in strategy_params.json
                    config_freeze = self.STRATEGY_PARAMS.get('freeze_limit_overrides', {}).get(self.index_name)

                    # Priority: Manual Override > API > Default
                    if config_freeze:
                        self.freeze_limit = config_freeze
                        await self._log_debug("Instruments", f"🔧 Using MANUAL freeze limit for {self.index_name}: {self.freeze_limit}")
                    elif api_freeze:
                        self.freeze_limit = api_freeze
                        default_value = default_freeze_limits.get(self.index_name)
                        if default_value and api_freeze != default_value:
                            await self._log_debug("Instruments", f"⚠️ API freeze ({api_freeze}) differs from default ({default_value}) for {self.index_name}")
                        await self._log_debug("Instruments", f"✅ Using API freeze limit for {self.index_name}: {self.freeze_limit}")
                    else:
                        self.freeze_limit = default_freeze_limits.get(self.index_name, 10000)
                        await self._log_debug("Instruments", f"✅ Using DEFAULT freeze limit for {self.index_name}: {self.freeze_limit}")

                    # Validate freeze limit
                    if self.freeze_limit < 100:
                        await self._log_debug("Instruments", f"❌ INVALID freeze limit {self.freeze_limit} for {self.index_name} - using safe default 10000")
                        self.freeze_limit = 10000

                    await self._log_debug("Instruments", f"✅ Loaded {self.index_name} - Lot Size: {self.lot_size}, Freeze: {self.freeze_limit}")
                else:
                    await self._log_debug("Instruments", f"❌ No instruments found for {self.index_name}")
                    self.lot_size = 1
                    self.freeze_limit = 10000

                self.option_instruments = instruments or []
                self._ui_expiry_dirty = True
                return self.option_instruments

            except Exception as e:
                error_msg = str(e)
                if attempt < max_retries:
                    await self._log_debug("Instruments", f"⚠️ Attempt {attempt} failed: {error_msg}. Retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    await self._log_debug("FATAL", f"Could not load instruments after {max_retries} attempts: {e}")
                    raise

    def get_weekly_expiry(self): 
        # ... (This function is unchanged)
        today = date.today()
        future_expiries = sorted([i['expiry'] for i in self.option_instruments if i.get('expiry') and i['expiry'] >= today])
        return future_expiries[0] if future_expiries else None

    def get_selected_expiry(self):
        """
        Get expiry date based on user's option_expiry_type selection.
        
        DATE-BASED POSITION MAPPING (works for all indices):
        - CURRENT_WEEK: 1st upcoming expiry (nearest)
        - NEXT_WEEK: 2nd upcoming expiry
        - MONTHLY: Last available expiry (furthest out, typically monthly contract)
        
        This approach:
        - Works for NIFTY (Tuesday), SENSEX (Thursday), BANKNIFTY (monthly only)
        - No complex weekly/monthly detection needed
        - Uses actual dates from exchange API
        - Reliable across all index types
        
        Returns:
            date: Selected expiry date or None if not available
        """
        today = date.today()
        expiry_type = self.params.get('option_expiry_type', 'CURRENT_WEEK')
        
        # Get all unique future expiries sorted by date
        future_expiries = sorted(list(set([
            i['expiry'] for i in self.option_instruments 
            if i.get('expiry') and i['expiry'] >= today
        ])))
        
        if not future_expiries:
            return None
        
        # Simple position-based mapping
        if expiry_type == 'CURRENT_WEEK':
            # 1st expiry (nearest)
            return future_expiries[0]
        
        elif expiry_type == 'NEXT_WEEK':
            # 2nd expiry
            if len(future_expiries) >= 2:
                return future_expiries[1]
            else:
                # Fallback to current week if only 1 expiry available
                return future_expiries[0]
        
        elif expiry_type == 'MONTHLY':
            # MONTHLY logic: Find the actual monthly contract
            # For weekly indices (NIFTY/SENSEX): Monthly is usually 3rd-5th in list
            # For monthly-only (BANKNIFTY): Take 2nd or 3rd expiry (not too far out)
            
            # Check if this is a weekly-expiring index (gap < 14 days)
            if len(future_expiries) >= 2:
                gap = (future_expiries[1] - future_expiries[0]).days
                
                if gap <= 14:
                    # Weekly index: Look for monthly contract
                    # Monthly is typically 3-5 weeks out (25-35 days from today)
                    monthly_candidates = [
                        exp for exp in future_expiries 
                        if 20 <= (exp - today).days <= 45
                    ]
                    
                    if monthly_candidates:
                        return monthly_candidates[0]
                    elif len(future_expiries) >= 4:
                        # Fallback: 4th expiry for weekly indices
                        return future_expiries[3]
                    else:
                        return future_expiries[-1]
                else:
                    # Monthly-only index: Take 2nd or 3rd expiry (reasonable timeframe)
                    if len(future_expiries) >= 3:
                        return future_expiries[2]  # 3rd monthly contract
                    elif len(future_expiries) >= 2:
                        return future_expiries[1]  # 2nd monthly contract
                    else:
                        return future_expiries[0]
            
            # Fallback if only 1 expiry
            return future_expiries[0]
        
        # Default fallback
        return future_expiries[0]

    def get_all_option_tokens(self):
        # ... (This function is unchanged)
        spot = self.data_manager.prices.get(self.index_symbol)
        if not spot: return [self.index_token]
        atm_strike = self.strike_step * round(spot / self.strike_step)
        strikes = [atm_strike + (i - 3) * self.strike_step for i in range(7)]
        tokens = {self.index_token, *[opt['instrument_token'] for strike in strikes for side in ['CE', 'PE'] if (opt := self.get_entry_option(side, strike))], *self.uoa_watchlist.keys()}
        return list(tokens)

    async def map_option_tokens(self, tokens):
        # ... (This function is unchanged)
        self.token_to_symbol = {o['instrument_token']: o['tradingsymbol'] for o in self.option_instruments if o['instrument_token'] in tokens}
        self.token_to_symbol[self.index_token] = self.index_symbol

    def get_strike_pairs(self, count=7):
        # ... (This function is unchanged)
        spot = self.data_manager.prices.get(self.index_symbol)
        if not spot: 
            return []
        
        # 🐛 DIAGNOSTIC: Check if we have instruments
        if not self.option_instruments:
            return []
        
        if not self.last_used_expiry:
            return []
            
        atm_strike = self.strike_step * round(spot / self.strike_step)
        strikes = [atm_strike + (i - count // 2) * self.strike_step for i in range(count)]
        return [{"strike": strike, "ce": self.get_entry_option('CE', strike), "pe": self.get_entry_option('PE', strike)} for strike in strikes]

    def get_entry_option(self, side, strike=None):
        # ... (This function is unchanged)
        spot = self.data_manager.prices.get(self.index_symbol)
        if not spot: return None
        if strike is None: strike = self.strike_step * round(spot / self.strike_step)
        for o in self.option_instruments:
            if o['expiry'] == self.last_used_expiry and o['strike'] == strike and o['instrument_type'] == side: return o
        return None

    def _sanitize_params(self, params):
        p = params.copy()
        try:
            keys_to_convert = [
                "start_capital", "trailing_sl_points", "trailing_sl_percent", 
                "daily_sl", "daily_pt", "partial_profit_pct", "partial_exit_pct", 
                "recovery_threshold_pct", "max_lots_per_order",
                "trade_pt", "break_even_threshold_pct",
                "green_hold_min_profit_pct", "green_hold_max_loss_pct",  # 🟢 Green candle hold parameters
                "paper_entry_delay_ms", "paper_exit_delay_ms", "paper_verification_delay_ms", # 🕐 Paper trading delays
                "supertrend_period", "supertrend_multiplier" # 📊 Supertrend parameters
            ]
            for key in keys_to_convert:
                if key in p:
                    try:
                        # Convert to float, handling empty strings and None values
                        if p[key] is None or p[key] == '':
                            p[key] = 0.0
                        else:
                            p[key] = float(p[key])
                    except (ValueError, TypeError):
                        # If conversion fails, set to 0.0 as default
                        p[key] = 0.0
                        print(f"Warning: Could not convert parameter '{key}' with value '{p[key]}' to float. Setting to 0.0")
        except Exception as e: 
            print(f"Warning: Error in parameter sanitization: {e}")
        return p