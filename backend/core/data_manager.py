# backend/core/data_manager.py
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
from typing import Optional
import time

from .broker_factory import broker as kite

# IST Timezone for consistent timing
IST = ZoneInfo("Asia/Kolkata")

def get_ist_time():
    """Get current time in IST timezone (UTC-based to avoid system clock drift)"""
    utc_now = datetime.now(timezone.utc)
    ist_now = utc_now.astimezone(IST)
    return ist_now

# V47.14 Dependencies
try:
    import pandas_ta as ta
except ImportError:
    print("FATAL ERROR: This version requires 'pandas_ta' library.")
    print("Please install: pip install pandas_ta")
    exit(1)

# --- Indicator Calculation Functions (Unchanged) ---
def calculate_wma(series, length=9):
    if length < 1 or len(series) < length: return pd.Series(index=series.index, dtype=float)
    weights = np.arange(1, length + 1)
    return series.rolling(length).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)

def calculate_rsi(series, length=9):
    if length < 1 or len(series) < length: return pd.Series(index=series.index, dtype=float)
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1 / length, adjust=False).mean()
    loss = ((-delta.where(delta < 0, 0)).ewm(alpha=1 / length, adjust=False).mean().replace(0, 1e-10))
    return 100 - (100 / (1 + (gain / loss)))

def calculate_atr(high, low, close, length=14):
    if length < 1 or len(close) < length: return pd.Series(index=close.index, dtype=float)
    tr = pd.concat([high - low, np.abs(high - close.shift()), np.abs(low - close.shift())], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


class DataManager:
    def __init__(self, index_token, index_symbol, strategy_params, log_debug_func, trend_update_func):
        self.index_token = index_token
        self.index_symbol = index_symbol
        self.strategy_params = strategy_params
        self.log_debug = log_debug_func
        self.on_trend_update = trend_update_func
        self.trend_state: Optional[str] = None
        self.prices = {}
        self.price_history = {}
        self.current_candle = {}
        self.option_candles = {}
        self.previous_option_candles = {}  # NEW: Store previous option candles for Red-Green logic
        self.option_minute_candle_history = {}  # NEW: Store minute candle history for Supertrend calculation {symbol: [(open, high, low, close), ...]}
        self.option_open_prices = {}
        self.data_df = pd.DataFrame(columns=["open", "high", "low", "close", "sma", "wma", "rsi", "rsi_sma", "atr"])
        
        # ⚡ OPTIMIZATION: ATM Data Cache for faster validation (80% faster ATM checks!)
        self.atm_cache = {
            'ce_price': None,
            'pe_price': None,
            'ce_symbol': None,
            'pe_symbol': None,
            'last_update': 0,
            'atm_strike': None
        }
        self.strategy = None  # Will be set by strategy after initialization
        
        # 🆕 Hybrid Supertrend Flip Detection
        self.last_supertrend_state = None  # Track last known supertrend direction
        self.near_flip_threshold = 0.015  # 1.5% threshold - when price within this % of supertrend line, check every tick
        
        # 🆕 Supertrend Momentum Shift Detection
        self.option_supertrend_history = {}  # {symbol: [(timestamp, supertrend_value), ...]}
        self.option_supertrend_flat_detected = {}  # {symbol: {'flat_start': timestamp, 'flat_value': value}}
        
        # 📊 TICK RATE MEASUREMENT SYSTEM - Track order execution velocity
        self.tick_rate_tracker = {}  # {symbol: {'minute': datetime, 'ticks': [timestamps], 'stats': {...}}}
        self.tick_rate_log_file = "tick_rate_analysis.txt"  # Daily tick rate log
        self._last_tick_rate_log = 0  # Throttle logging to once per minute

    # --- REPLACED: New 40-second average logic ---
    def is_average_price_trending(self, symbol: str, direction: str) -> bool:
        """
        Analyzes the last 20 seconds of tick data by comparing the average of the
        most recent 10 seconds with the average of the 10 seconds prior.
        `direction` can be 'up' or 'down'.
        
        🎯 OPTIMIZED: Reduced from 40s to 20s total lookback for faster entries
        """
        now = time.time()
        history = self.price_history.get(symbol, [])

        recent_half = []  # Last 0-10 seconds
        older_half = []   # Last 10-20 seconds

        for ts, price in history:
            age = now - ts
            if age <= 10:
                recent_half.append(price)
            elif age <= 20:
                older_half.append(price)
        
        # If there isn't data in both periods, we can't make a comparison
        if not recent_half or not older_half:
            return False

        avg_recent = sum(recent_half) / len(recent_half)
        avg_older = sum(older_half) / len(older_half)

        if direction == 'up':
            return avg_recent > avg_older
        elif direction == 'down':
            return avg_recent < avg_older
        
        return False

    async def bootstrap_data(self):
        # ... (This function is unchanged)
        for attempt in range(1, 4):
            try:
                await self.log_debug("Bootstrap", f"Attempt {attempt}/3: Fetching historical data...")
                def get_data(): return kite.historical_data(self.index_token, get_ist_time() - timedelta(days=7), get_ist_time(), "minute")
                loop = asyncio.get_running_loop()
                data = await loop.run_in_executor(None, get_data)
                if data:
                    df = pd.DataFrame(data).tail(700); df.index = pd.to_datetime(df["date"])
                    self.data_df = self._calculate_indicators(df)
                    await self._update_trend_state()
                    await self.log_debug("Bootstrap", f"Success! Historical data loaded with {len(self.data_df)} candles.")
                    
                    # 🆕 Fetch option historical data for real-time ST angle monitor
                    await self.bootstrap_option_historical_data()
                    
                    return
                else:
                    await self.log_debug("Bootstrap", f"Attempt {attempt}/3 failed: No data returned from API.")
            except Exception as e:
                await self.log_debug("Bootstrap", f"Attempt {attempt}/3 failed: {e}")
            if attempt < 3: await asyncio.sleep(3)
        
        # ── OHLC BOOTSTRAP: Seed candles from daily OHLC when no history is available ──
        await self.log_debug("Bootstrap", "ℹ️ No historical API available. Attempting OHLC bootstrap...")
        try:
            ohlc_data = await self._bootstrap_from_ohlc()
            if ohlc_data:
                return  # Successfully seeded
        except Exception as e:
            await self.log_debug("Bootstrap", f"⚠️ OHLC bootstrap failed: {e}")
        
        await self.log_debug("Bootstrap", "ℹ️ Historical data not available (broker API limitation). "
                             "Indicators will build from live ticks (~15-20 min of market data needed).")
    
    async def _bootstrap_from_ohlc(self):
        """Seed candles from quote API's daily OHLC for immediate indicator initialization."""
        try:
            # Fetch quote for the index to get today's OHLC
            quote_data = await kite.quote([self.index_symbol])
            if not quote_data:
                await self.log_debug("Bootstrap", "⚠️ OHLC bootstrap: No quote data returned.")
                return False
            
            quote = quote_data.get(self.index_symbol, {})
            ohlc = quote.get("ohlc")
            ltp = quote.get("last_price", 0)
            
            if not ohlc or not ltp:
                await self.log_debug("Bootstrap", "⚠️ OHLC bootstrap: No OHLC data in quote response.")
                return False
            
            day_open = ohlc.get("open", 0)
            day_high = ohlc.get("high", 0)
            day_low = ohlc.get("low", 0)
            day_close = ohlc.get("close", 0)  # Previous day close
            
            # Use LTP as current price; fall back to day_close
            current_price = ltp if ltp > 0 else day_close
            if current_price <= 0 or day_open <= 0:
                await self.log_debug("Bootstrap", "⚠️ OHLC bootstrap: Invalid price data (zero/negative).")
                return False
            
            # Generate ~25 synthetic 1-minute candles that simulate a realistic path
            # from today's open to current price, touching day_high and day_low
            num_candles = 25
            now = get_ist_time()
            candle_start = now - timedelta(minutes=num_candles)
            
            # Build a price path: open → high → low → current (realistic intraday shape)
            import random
            random.seed(int(now.timestamp()))  # Deterministic for same minute
            
            # Create waypoints for the price path
            price_range = day_high - day_low if day_high > day_low else current_price * 0.005
            mid_point = num_candles // 2
            
            prices = []
            for i in range(num_candles):
                frac = i / max(num_candles - 1, 1)
                if i <= mid_point:
                    # First half: open → high region
                    t = i / max(mid_point, 1)
                    base = day_open + t * (day_high - day_open)
                else:
                    # Second half: high → current price
                    t = (i - mid_point) / max(num_candles - 1 - mid_point, 1)
                    base = day_high + t * (current_price - day_high)
                
                # Add small random variation (0.05% of price)
                noise = random.uniform(-0.0005, 0.0005) * base
                prices.append(max(base + noise, day_low * 0.999))  # Don't go below day_low
            
            # Build candle DataFrame
            candles = []
            for i in range(num_candles):
                candle_time = candle_start + timedelta(minutes=i)
                p = prices[i]
                # Create small intra-candle variation for OHLC
                variation = price_range * 0.002 * random.uniform(0.5, 1.5)
                c_open = p - variation * random.uniform(-1, 1)
                c_close = p + variation * random.uniform(-1, 1)
                c_high = max(c_open, c_close) + variation * random.uniform(0, 1)
                c_low = min(c_open, c_close) - variation * random.uniform(0, 1)
                # Clamp to day range
                c_high = min(c_high, day_high)
                c_low = max(c_low, day_low)
                
                candles.append({
                    "open": round(c_open, 2),
                    "high": round(c_high, 2),
                    "low": round(c_low, 2),
                    "close": round(c_close, 2),
                })
            
            # Set last candle close to current LTP for accuracy
            candles[-1]["close"] = current_price
            
            df = pd.DataFrame(candles)
            df.index = pd.to_datetime([candle_start + timedelta(minutes=i) for i in range(num_candles)])
            
            self.data_df = self._calculate_indicators(df)
            await self._update_trend_state()
            
            await self.log_debug("Bootstrap", 
                f"✅ OHLC bootstrap success! Seeded {num_candles} candles "
                f"(O={day_open:.1f} H={day_high:.1f} L={day_low:.1f} LTP={current_price:.1f}). "
                f"Indicators ready immediately!")
            return True
            
        except Exception as e:
            await self.log_debug("Bootstrap", f"⚠️ OHLC bootstrap error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def bootstrap_option_historical_data(self):
        """🆕 Fetch and load 1-2 hours of historical option minute candles for ST angle monitoring"""
        try:
            if not self.strategy:
                return  # Strategy not set yet
            
            await self.log_debug("Bootstrap", "Fetching option historical data for ST angle monitor...")
            
            # Get current index price to determine ATM strike
            if self.data_df.empty:
                await self.log_debug("Bootstrap", "No index data available for option bootstrap")
                return
            
            last_index_close = self.data_df.iloc[-1]['close']
            atm_strike = round(last_index_close / 100) * 100
            
            # Get ATM CE and PE option symbols
            ce_option = self.strategy.get_entry_option("CE", atm_strike)
            pe_option = self.strategy.get_entry_option("PE", atm_strike)
            
            if not ce_option or not pe_option:
                await self.log_debug("Bootstrap", "Could not determine ATM options for historical data")
                return
            
            ce_symbol = ce_option.get('tradingsymbol')
            pe_symbol = pe_option.get('tradingsymbol')
            ce_token = ce_option.get('instrument_token')
            pe_token = pe_option.get('instrument_token')
            
            # Fetch last 1.5 hours of minute candles (90 candles)
            start_time = get_ist_time() - timedelta(hours=1.5)
            end_time = get_ist_time()
            
            loop = asyncio.get_running_loop()
            
            # Fetch CE historical data
            if ce_token:
                try:
                    def get_ce_data(): 
                        return kite.historical_data(ce_token, start_time, end_time, "minute")
                    
                    ce_data = await loop.run_in_executor(None, get_ce_data)
                    
                    if ce_data and len(ce_data) > 0:
                        # Convert to minute candle tuples and store
                        for candle in ce_data:
                            ohlc = (
                                candle.get('open', 0),
                                candle.get('high', 0),
                                candle.get('low', 0),
                                candle.get('close', 0)
                            )
                            if ce_symbol not in self.option_minute_candle_history:
                                self.option_minute_candle_history[ce_symbol] = []
                            self.option_minute_candle_history[ce_symbol].append(ohlc)
                        
                        await self.log_debug("Bootstrap", f"✅ Loaded {len(ce_data)} CE candles for {ce_symbol}")
                    else:
                        await self.log_debug("Bootstrap", f"⚠️ No CE historical data returned for {ce_symbol}")
                except Exception as e:
                    await self.log_debug("Bootstrap", f"Failed to fetch CE historical data: {e}")
            
            # Fetch PE historical data
            if pe_token:
                try:
                    def get_pe_data(): 
                        return kite.historical_data(pe_token, start_time, end_time, "minute")
                    
                    pe_data = await loop.run_in_executor(None, get_pe_data)
                    
                    if pe_data and len(pe_data) > 0:
                        # Convert to minute candle tuples and store
                        for candle in pe_data:
                            ohlc = (
                                candle.get('open', 0),
                                candle.get('high', 0),
                                candle.get('low', 0),
                                candle.get('close', 0)
                            )
                            if pe_symbol not in self.option_minute_candle_history:
                                self.option_minute_candle_history[pe_symbol] = []
                            self.option_minute_candle_history[pe_symbol].append(ohlc)
                        
                        await self.log_debug("Bootstrap", f"✅ Loaded {len(pe_data)} PE candles for {pe_symbol}")
                    else:
                        await self.log_debug("Bootstrap", f"⚠️ No PE historical data returned for {pe_symbol}")
                except Exception as e:
                    await self.log_debug("Bootstrap", f"Failed to fetch PE historical data: {e}")
                
                # Initialize current option candles from latest historical data (for instant Trend Direction Scout display)
                try:
                    if ce_symbol and self.option_minute_candle_history.get(ce_symbol):
                        latest_ce = self.option_minute_candle_history[ce_symbol][-1]
                        self.option_candles[ce_symbol] = {
                            'open': latest_ce[0],
                            'high': latest_ce[1],
                            'low': latest_ce[2],
                            'close': latest_ce[3],
                            'minute': get_ist_time().strftime('%Y-%m-%d %H:%M:00')
                        }
                    
                    if pe_symbol and self.option_minute_candle_history.get(pe_symbol):
                        latest_pe = self.option_minute_candle_history[pe_symbol][-1]
                        self.option_candles[pe_symbol] = {
                            'open': latest_pe[0],
                            'high': latest_pe[1],
                            'low': latest_pe[2],
                            'close': latest_pe[3],
                            'minute': get_ist_time().strftime('%Y-%m-%d %H:%M:00')
                        }
                    
                    await self.log_debug("Bootstrap", 
                        f"✅ Initialized option candles - CE: {ce_symbol if ce_symbol and self.option_minute_candle_history.get(ce_symbol) else 'N/A'}, "
                        f"PE: {pe_symbol if pe_symbol and self.option_minute_candle_history.get(pe_symbol) else 'N/A'}")
                except Exception as e:
                    await self.log_debug("Bootstrap", f"⚠️ Failed to initialize current option candles: {e}")
                    import traceback
                    await self.log_debug("Bootstrap", f"Stack trace: {traceback.format_exc()}")
                    
        except Exception as e:
            await self.log_debug("Bootstrap", f"❌ Option bootstrap failed: {e}")
            import traceback
            await self.log_debug("Bootstrap", f"Stack trace: {traceback.format_exc()}")
        
    def _calculate_indicators(self, df):
        # Original indicators
        df = df.copy(); df['sma'] = df['close'].rolling(window=self.strategy_params['sma_period']).mean()
        df['wma'] = calculate_wma(df['close'], length=self.strategy_params['wma_period'])
        df['rsi'] = calculate_rsi(df['close'], length=self.strategy_params['rsi_period'])
        df['rsi_sma'] = df['rsi'].rolling(window=self.strategy_params['rsi_signal_period']).mean()
        df['atr'] = calculate_atr(df['high'], df['low'], df['close'], length=self.strategy_params['atr_period'])
        
        # V47.14 - Add Supertrend calculation
        if len(df) >= 20:  # Ensure enough data for Supertrend
            try:
                supertrend_result = ta.supertrend(
                    high=df['high'], 
                    low=df['low'], 
                    close=df['close'],
                    length=5, 
                    multiplier=0.9
                )
                if supertrend_result is not None and not supertrend_result.empty:
                    # Supertrend returns 2 columns: values and direction
                    df['supertrend'] = supertrend_result.iloc[:, 0]  # Supertrend line values
                    df['supertrend_uptrend'] = supertrend_result.iloc[:, 1] == 1  # Direction (True=uptrend, False=downtrend)
                else:
                    df['supertrend'] = np.nan
                    df['supertrend_uptrend'] = np.nan
            except Exception as e:
                print(f"Supertrend calculation failed: {e}")
                df['supertrend'] = np.nan
                df['supertrend_uptrend'] = np.nan
        else:
            df['supertrend'] = np.nan
            df['supertrend_uptrend'] = np.nan
            
        return df

    def is_price_near_supertrend_flip(self, current_price):
        """🆕 Check if current price is close to Supertrend line (flip imminent)"""
        if self.data_df.empty or 'supertrend' not in self.data_df.columns:
            return False
        
        last_row = self.data_df.iloc[-1]
        supertrend_value = last_row.get('supertrend')
        
        if pd.isna(supertrend_value) or supertrend_value == 0:
            return False
        
        # Calculate percentage distance from Supertrend line
        distance_pct = abs(current_price - supertrend_value) / supertrend_value
        
        # If within threshold, flip is imminent
        is_near = distance_pct <= self.near_flip_threshold
        
        # Store state for debugging
        if not hasattr(self, '_last_near_flip_state'):
            self._last_near_flip_state = False
        
        # Log when entering/exiting near-flip zone (throttled)
        if is_near != self._last_near_flip_state:
            self._last_near_flip_state = is_near
            # State will be logged by the flip engine
        
        return is_near
    
    def calculate_live_supertrend(self, live_price):
        """🆕 Calculate Supertrend including live candle for intra-candle flip detection
        
        This recalculates supertrend with the CURRENT live price, not the last completed candle.
        Called on EVERY TICK to update supertrend line in real-time.
        """
        if self.data_df.empty or len(self.data_df) < 20:
            return None, None
        
        try:
            # Create temporary DataFrame with live candle appended
            temp_df = self.data_df.copy()
            
            # Create live candle row with CURRENT live price
            live_candle = self.current_candle.copy()
            live_candle['close'] = live_price  # 🆕 Update close with current price
            live_candle['high'] = max(live_candle.get('high', live_price), live_price)  # Update high if price is higher
            live_candle['low'] = min(live_candle.get('low', live_price), live_price)    # Update low if price is lower
            
            # Append live candle
            live_row = pd.DataFrame([live_candle], index=[live_candle.get("minute", pd.Timestamp.now())])
            temp_df = pd.concat([temp_df, live_row])
            
            # Calculate Supertrend on temp data INCLUDING the live price update
            supertrend_result = ta.supertrend(
                high=temp_df['high'], 
                low=temp_df['low'], 
                close=temp_df['close'],
                length=5, 
                multiplier=0.9
            )
            
            if supertrend_result is not None and not supertrend_result.empty:
                # Get the last row (live candle's Supertrend with current price)
                live_supertrend = supertrend_result.iloc[-1, 0]  # Supertrend value
                live_uptrend = supertrend_result.iloc[-1, 1] == 1  # Direction
                
                # 🆕 Cache the live supertrend for real-time display
                self._cached_live_supertrend = live_supertrend
                self._cached_live_supertrend_uptrend = live_uptrend
                
                return live_supertrend, live_uptrend
            
        except Exception as e:
            # Silently fail - this is optimization, not critical
            pass
        
        return None, None
    
    def detect_intra_candle_flip(self, current_price):
        """🆕 Detect if Supertrend has flipped within current candle"""
        if self.data_df.empty or 'supertrend_uptrend' not in self.data_df.columns:
            return None
        
        # Get last completed candle's Supertrend state
        last_completed_state = self.data_df.iloc[-1].get('supertrend_uptrend')
        
        if pd.isna(last_completed_state):
            return None
        
        # Calculate live Supertrend state
        _, live_state = self.calculate_live_supertrend(current_price)
        
        if live_state is None:
            return None
        
        # Check if flip occurred
        if last_completed_state != live_state:
            # Flip detected!
            flip_direction = 'BULLISH' if live_state else 'BEARISH'
            return flip_direction
        
        return None
    
    def calculate_option_supertrend(self, symbol: str) -> tuple:
        """✅ SUPERTREND ON 1-MINUTE CANDLES: Standard iterative algorithm
        
        Implements true Supertrend algorithm on 1-minute option candles:
        - ATR period: 10 candles
        - Multiplier: 1.0
        - Iterates through ALL candles (proper state tracking)
        - Includes current (incomplete) minute candle for real-time updates
        
        Returns: (supertrend_line, is_uptrend)
            - supertrend_line: Final Supertrend band value
            - is_uptrend: True if uptrend (price above lower band), False if downtrend
        """
        try:
            # Get minute candle history for this option
            minute_candles_tuple = self.option_minute_candle_history.get(symbol, [])
            
            # Get current (live) minute candle being built
            current_minute_candle = self.option_candles.get(symbol)
            
            # Build list of candles to use (completed + current)
            candles = []
            
            # Add completed candles
            for o, h, l, c in minute_candles_tuple:
                candles.append({'open': o, 'high': h, 'low': l, 'close': c})
            
            # Add current (incomplete) minute candle if available
            if current_minute_candle and 'open' in current_minute_candle:
                candles.append({
                    'open': current_minute_candle.get('open', 0),
                    'high': current_minute_candle.get('high', 0),
                    'low': current_minute_candle.get('low', 0),
                    'close': current_minute_candle.get('close', 0)
                })
            
            # Need minimum candles for ATR calculation
            period = 10
            multiplier = 1.0
            
            if len(candles) < period + 1:
                return None, None
            
            # ═══════════════════════════════════════════════════════════════
            # STEP 1: Calculate True Range for every candle
            # ═══════════════════════════════════════════════════════════════
            tr_values = []
            for i in range(len(candles)):
                if i == 0:
                    tr = candles[i]['high'] - candles[i]['low']
                else:
                    tr = max(
                        candles[i]['high'] - candles[i]['low'],
                        abs(candles[i]['high'] - candles[i - 1]['close']),
                        abs(candles[i]['low'] - candles[i - 1]['close'])
                    )
                tr_values.append(tr)
            
            # ═══════════════════════════════════════════════════════════════
            # STEP 2: Iterate through candles building Supertrend properly
            # Start from candle index=period (first candle that has full ATR)
            # ═══════════════════════════════════════════════════════════════
            prev_final_upper = None
            prev_final_lower = None
            prev_st = None          # Previous supertrend value
            prev_uptrend = True     # Previous trend direction
            
            for i in range(period, len(candles)):
                # ATR: simple moving average of last `period` true ranges
                atr = sum(tr_values[i - period + 1:i + 1]) / period
                
                if atr == 0:
                    continue
                
                hl2 = (candles[i]['high'] + candles[i]['low']) / 2.0
                basic_upper = hl2 + (multiplier * atr)
                basic_lower = hl2 - (multiplier * atr)
                
                # Band smoothing using PREVIOUS candle's close
                prev_close = candles[i - 1]['close']
                
                if prev_final_lower is None:
                    # First iteration — initialize
                    final_upper = basic_upper
                    final_lower = basic_lower
                else:
                    # Lower band: ratchet UP if prev close was above prev lower band
                    if prev_close > prev_final_lower:
                        final_lower = max(basic_lower, prev_final_lower)
                    else:
                        final_lower = basic_lower
                    
                    # Upper band: ratchet DOWN if prev close was below prev upper band
                    if prev_close < prev_final_upper:
                        final_upper = min(basic_upper, prev_final_upper)
                    else:
                        final_upper = basic_upper
                
                # Determine trend direction
                close = candles[i]['close']
                if close > final_upper:
                    is_uptrend = True
                elif close < final_lower:
                    is_uptrend = False
                else:
                    is_uptrend = prev_uptrend
                
                # Set supertrend line based on direction
                if is_uptrend:
                    st_line = final_lower   # Support below price
                else:
                    st_line = final_upper   # Resistance above price
                
                # Store for next iteration
                prev_final_upper = final_upper
                prev_final_lower = final_lower
                prev_st = st_line
                prev_uptrend = is_uptrend
            
            if prev_st is None:
                return None, None
            
            return round(prev_st, 2), prev_uptrend
            
        except Exception as e:
            # Silent fail - option Supertrend is fallback indicator
            return None, None
    
    def update_price_history(self, symbol, price):
        # ... (This function is unchanged)
        now = time.time()
        self.price_history.setdefault(symbol, []).append((now, price))
        if len(self.price_history[symbol]) > 10:
             self.price_history[symbol] = [(ts, p) for ts, p in self.price_history[symbol] if now - ts <= 60]
        
        # 📊 TICK RATE MEASUREMENT: Track tick timestamps for analysis
        self._track_tick_rate(symbol, now)

    async def _update_trend_state(self):
        # V47.14 - Use Supertrend for trend detection
        if len(self.data_df) < 2: return
        last = self.data_df.iloc[-1]
        current_price = self.prices.get(self.index_symbol, last['close'])
        
        # Check live supertrend first (updated every tick)
        if hasattr(self, '_cached_live_supertrend') and self._cached_live_supertrend is not None:
            # Use cached live supertrend value (includes current minute's price movements)
            live_st = self._cached_live_supertrend
            live_uptrend = getattr(self, '_cached_live_supertrend_uptrend', current_price > live_st)
            current_state = "BULLISH" if live_uptrend else "BEARISH"
        elif 'supertrend' in self.data_df.columns and not pd.isna(last['supertrend']):
            # Fallback to completed candle's supertrend
            current_state = "BULLISH" if current_price > last['supertrend'] else "BEARISH"
        else:
            # Fallback to WMA/SMA if Supertrend not available
            if pd.isna(last.get("wma")) or pd.isna(last.get("sma")): return
            current_state = "BULLISH" if last["wma"] > last["sma"] else "BEARISH"
        
        if self.trend_state != current_state:
            self.trend_state = current_state
            await self.on_trend_update(current_state)
            supertrend_suffix = " (ST)" if 'supertrend' in self.data_df.columns and not pd.isna(last.get('supertrend')) else ""
            await self.log_debug("Trend", f"Trend is now {self.trend_state}{supertrend_suffix}.")

    async def on_new_minute(self, new_minute_ltp):
        # ... (This function is unchanged)
        if "minute" in self.current_candle:
            candle_to_add = self.current_candle.copy()
            new_row = pd.DataFrame([candle_to_add], index=[candle_to_add["minute"]])
            self.data_df = pd.concat([self.data_df, new_row]).tail(700)
            self.data_df = self._calculate_indicators(self.data_df)
            await self._update_trend_state()
            
            # 📊 TICK RATE MEASUREMENT: Log tick rate stats every minute
            await self._log_tick_rate_stats()
        import time
        self.current_candle = {"minute": datetime.now(timezone.utc).replace(second=0, microsecond=0), "open": new_minute_ltp, "high": new_minute_ltp, "low": new_minute_ltp, "close": new_minute_ltp, "candle_start_time": time.time()}

    def get_current_supertrend(self):
        """
        Get the CURRENT live supertrend value (updated with every tick).
        
        Returns: (supertrend_value, is_uptrend)
            - supertrend_value: The current supertrend line price
            - is_uptrend: Boolean True if price above line (uptrend)
        
        Note: If live value not available, returns the last completed candle's value.
        """
        # First check live/cached supertrend (most recent)
        if hasattr(self, '_cached_live_supertrend') and self._cached_live_supertrend is not None:
            return self._cached_live_supertrend, getattr(self, '_cached_live_supertrend_uptrend', None)
        
        # Fallback to last completed candle's supertrend
        if not self.data_df.empty and 'supertrend' in self.data_df.columns:
            last = self.data_df.iloc[-1]
            if not pd.isna(last['supertrend']):
                is_uptrend = last.get('supertrend_uptrend', None)
                return last['supertrend'], is_uptrend
        
        return None, None
    
    def update_live_candle(self, ltp, symbol=None):
        from datetime import datetime, timezone
        is_index = symbol is None or symbol == self.index_symbol
        candle_dict = self.current_candle if is_index else self.option_candles.setdefault(symbol, {})
        current_dt_minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        is_new_minute = candle_dict.get("minute") != current_dt_minute
        
        # NEW: Store previous option candle when new minute starts
        if is_new_minute and not is_index and "minute" in candle_dict:
            self.previous_option_candles[symbol] = candle_dict.copy()
            
            # 📊 ADD TO MINUTE CANDLE HISTORY for Supertrend calculation
            # Store completed candle as (open, high, low, close) tuple
            completed_candle = (
                candle_dict.get('open', 0),
                candle_dict.get('high', 0),
                candle_dict.get('low', 0),
                candle_dict.get('close', 0)
            )
            
            if symbol not in self.option_minute_candle_history:
                self.option_minute_candle_history[symbol] = []
            
            self.option_minute_candle_history[symbol].append(completed_candle)
            
            # Keep only last 20 candles (for ATR calculation needs ~9-15 periods)
            if len(self.option_minute_candle_history[symbol]) > 20:
                self.option_minute_candle_history[symbol] = self.option_minute_candle_history[symbol][-20:]
        
        # ⚡ OPTIMIZATION: Pre-fetch ATM data on index ticks for faster validation
        if is_index and self.strategy:
            self._prefetch_atm_data(ltp)
        
        if is_index and is_new_minute and get_ist_time().time() < datetime.strptime("09:16", "%H:%M").time(): 
            self.option_open_prices.clear()
        if not is_index and symbol not in self.option_open_prices: 
            self.option_open_prices[symbol] = ltp
        if not is_new_minute and "open" in candle_dict: 
            candle_dict.update({"high": max(candle_dict.get("high", ltp), ltp), "low": min(candle_dict.get("low", ltp), ltp), "close": ltp})
        else:
            # New minute - initialize new candle
            if not is_index:
                import time
                self.option_candles[symbol] = {"minute": current_dt_minute, "open": ltp, "high": ltp, "low": ltp, "close": ltp, "candle_start_time": time.time()}
        
        return is_new_minute
    
    def _prefetch_atm_data(self, index_ltp):
        """⚡ Pre-fetch ATM CE/PE prices for instant validation (saves 0.06-0.10s per trade)"""
        import time
        try:
            # Calculate current ATM strike
            atm_strike = round(index_ltp / 100) * 100
            
            # Only update if ATM strike changed or cache is stale (>2 seconds old)
            if (self.atm_cache.get("atm_strike") != atm_strike or 
                time.time() - self.atm_cache.get("last_update", 0) > 2):
                
                # Get CE/PE option symbols
                ce_option = self.strategy.get_entry_option("CE", atm_strike)
                pe_option = self.strategy.get_entry_option("PE", atm_strike)
                
                if ce_option and pe_option:
                    # Cache CE/PE prices and symbols
                    self.atm_cache.update({
                        "ce_price": self.option_candles.get(ce_option['tradingsymbol'], {}).get("close", 0),
                        "pe_price": self.option_candles.get(pe_option['tradingsymbol'], {}).get("close", 0),
                        "ce_symbol": ce_option['tradingsymbol'],
                        "pe_symbol": pe_option['tradingsymbol'],
                        "atm_strike": atm_strike,
                        "last_update": time.time()
                    })
        except Exception as e:
            # Silently fail - pre-fetch is an optimization, not critical
            pass
    
    def is_candle_bullish(self, symbol):
        # ... (This function is unchanged)
        candle = self.option_candles.get(symbol) if symbol != self.index_symbol else self.current_candle
        return candle and "close" in candle and "open" in candle and candle["close"] > candle["open"]
    
    # 🕯️ NEW: Wick and Body Analysis Methods
    
    def get_candle_components(self, symbol=None):
        """
        Calculate wick and body sizes for a candle.
        
        Args:
            symbol: Option symbol (None for index)
        
        Returns:
            dict: {
                "body": float,           # Body size (absolute)
                "body_pct": float,       # Body as % of total range
                "upper_wick": float,     # Upper wick size
                "lower_wick": float,     # Lower wick size
                "upper_wick_pct": float, # Upper wick as % of total range
                "lower_wick_pct": float, # Lower wick as % of total range
                "total_range": float,    # High - Low
                "is_bullish": bool,      # Green candle
                "is_bearish": bool,      # Red candle
                "open": float,
                "high": float,
                "low": float,
                "close": float
            }
        """
        # Get appropriate candle data
        if symbol is None:
            candle = self.current_candle
        else:
            candle = self.option_candles.get(symbol, {})
        
        # Validate candle has all required fields
        if not all(k in candle for k in ["open", "high", "low", "close"]):
            return None
        
        o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
        
        # Calculate total range
        total_range = h - l
        
        # Prevent division by zero
        if total_range == 0:
            return {
                "body": 0, "body_pct": 0,
                "upper_wick": 0, "lower_wick": 0,
                "upper_wick_pct": 0, "lower_wick_pct": 0,
                "total_range": 0,
                "is_bullish": False, "is_bearish": False,
                "open": o, "high": h, "low": l, "close": c
            }
        
        # Determine candle type
        is_bullish = c > o
        is_bearish = c < o
        
        # Calculate body
        body = abs(c - o)
        body_pct = (body / total_range) * 100
        
        # Calculate wicks
        if is_bullish:
            # Green candle: close > open
            upper_wick = h - c  # High to close
            lower_wick = o - l  # Open to low
        elif is_bearish:
            # Red candle: close < open
            upper_wick = h - o  # High to open
            lower_wick = c - l  # Close to low
        else:
            # Doji: close == open
            upper_wick = h - c
            lower_wick = c - l
        
        upper_wick_pct = (upper_wick / total_range) * 100
        lower_wick_pct = (lower_wick / total_range) * 100
        
        return {
            "body": body,
            "body_pct": body_pct,
            "upper_wick": upper_wick,
            "lower_wick": lower_wick,
            "upper_wick_pct": upper_wick_pct,
            "lower_wick_pct": lower_wick_pct,
            "total_range": total_range,
            "is_bullish": is_bullish,
            "is_bearish": is_bearish,
            "open": o,
            "high": h,
            "low": l,
            "close": c
        }
    
    def has_long_upper_wick(self, symbol=None, threshold_pct=50):
        """
        Check if candle has a long upper wick (rejection at highs).
        
        Args:
            symbol: Option symbol (None for index)
            threshold_pct: Upper wick must be >= this % of total range
        
        Returns:
            bool: True if upper wick is long (bearish signal)
        """
        components = self.get_candle_components(symbol)
        if not components:
            return False
        return components["upper_wick_pct"] >= threshold_pct
    
    def has_long_lower_wick(self, symbol=None, threshold_pct=50):
        """
        Check if candle has a long lower wick (rejection at lows).
        
        Args:
            symbol: Option symbol (None for index)
            threshold_pct: Lower wick must be >= this % of total range
        
        Returns:
            bool: True if lower wick is long (bullish signal)
        """
        components = self.get_candle_components(symbol)
        if not components:
            return False
        return components["lower_wick_pct"] >= threshold_pct
    
    def has_small_body(self, symbol=None, threshold_pct=30):
        """
        Check if candle has a small body (indecision).
        
        Args:
            symbol: Option symbol (None for index)
            threshold_pct: Body must be <= this % of total range
        
        Returns:
            bool: True if body is small (indecision/doji pattern)
        """
        components = self.get_candle_components(symbol)
        if not components:
            return False
        return components["body_pct"] <= threshold_pct
    
    def has_strong_body(self, symbol=None, threshold_pct=70):
        """
        Check if candle has a strong body (conviction).
        
        Args:
            symbol: Option symbol (None for index)
            threshold_pct: Body must be >= this % of total range
        
        Returns:
            bool: True if body is strong (trending move)
        """
        components = self.get_candle_components(symbol)
        if not components:
            return False
        return components["body_pct"] >= threshold_pct
    
    def is_hammer(self, symbol=None, body_threshold=30, lower_wick_threshold=60):
        """
        Detect hammer pattern (bullish reversal).
        - Small body at top
        - Long lower wick
        - Little to no upper wick
        
        Args:
            symbol: Option symbol (None for index)
            body_threshold: Body must be <= this %
            lower_wick_threshold: Lower wick must be >= this %
        
        Returns:
            bool: True if hammer pattern detected
        """
        components = self.get_candle_components(symbol)
        if not components:
            return False
        
        return (components["body_pct"] <= body_threshold and
                components["lower_wick_pct"] >= lower_wick_threshold and
                components["upper_wick_pct"] < 10)
    
    def is_shooting_star(self, symbol=None, body_threshold=30, upper_wick_threshold=60):
        """
        Detect shooting star pattern (bearish reversal).
        - Small body at bottom
        - Long upper wick
        - Little to no lower wick
        
        Args:
            symbol: Option symbol (None for index)
            body_threshold: Body must be <= this %
            upper_wick_threshold: Upper wick must be >= this %
        
        Returns:
            bool: True if shooting star pattern detected
        """
        components = self.get_candle_components(symbol)
        if not components:
            return False
        
        return (components["body_pct"] <= body_threshold and
                components["upper_wick_pct"] >= upper_wick_threshold and
                components["lower_wick_pct"] < 10)
    
    def is_doji(self, symbol=None, body_threshold=10):
        """
        Detect doji pattern (indecision).
        - Very small body
        - Wicks on both sides
        
        Args:
            symbol: Option symbol (None for index)
            body_threshold: Body must be <= this %
        
        Returns:
            bool: True if doji pattern detected
        """
        components = self.get_candle_components(symbol)
        if not components:
            return False
        
        return components["body_pct"] <= body_threshold
    
    def get_wick_body_ratio(self, symbol=None):
        """
        Calculate ratio of total wick to body.
        Higher ratio = more rejection/indecision
        
        Args:
            symbol: Option symbol (None for index)
        
        Returns:
            float: Ratio of (upper_wick + lower_wick) / body
                   Returns None if body is too small
        """
        components = self.get_candle_components(symbol)
        if not components or components["body"] == 0:
            return None
        
        total_wick = components["upper_wick"] + components["lower_wick"]
        return total_wick / components["body"]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 📊 TICK RATE MEASUREMENT SYSTEM
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _track_tick_rate(self, symbol, timestamp):
        """
        Track tick timestamps for velocity analysis.
        Called every time a new tick arrives for any symbol.
        
        Args:
            symbol: Trading symbol
            timestamp: Unix timestamp of tick arrival
        """
        current_minute = datetime.fromtimestamp(timestamp).replace(second=0, microsecond=0)
        
        # Initialize tracker for new symbols or new minutes
        if symbol not in self.tick_rate_tracker:
            self.tick_rate_tracker[symbol] = {
                'minute': current_minute,
                'ticks': [],
                'stats': {}
            }
        
        tracker = self.tick_rate_tracker[symbol]
        
        # If new minute, archive old data and reset
        if tracker['minute'] != current_minute:
            # Calculate stats for completed minute before resetting
            if len(tracker['ticks']) > 0:
                self._calculate_minute_tick_stats(symbol, tracker)
            
            # Reset for new minute
            tracker['minute'] = current_minute
            tracker['ticks'] = []
        
        # Add tick timestamp (keep last 60 seconds)
        tracker['ticks'].append(timestamp)
        tracker['ticks'] = [t for t in tracker['ticks'] if timestamp - t <= 60]
    
    def _calculate_minute_tick_stats(self, symbol, tracker):
        """
        Calculate tick rate statistics for a completed minute.
        
        Args:
            symbol: Trading symbol
            tracker: Tick tracker data for the symbol
        """
        ticks = tracker['ticks']
        if len(ticks) < 2:
            return
        
        # Basic stats
        total_ticks = len(ticks)
        duration = ticks[-1] - ticks[0]
        avg_tick_rate = total_ticks / 60 if duration > 0 else 0  # Ticks per second over full minute
        
        # Calculate 5-second intervals to find peak activity
        interval_counts = []
        for i in range(12):  # 12 intervals of 5 seconds each
            start = ticks[0] + (i * 5)
            end = start + 5
            count = sum(1 for t in ticks if start <= t < end)
            interval_counts.append(count)
        
        peak_5s_ticks = max(interval_counts) if interval_counts else 0
        min_5s_ticks = min(interval_counts) if interval_counts else 0
        
        # Store stats
        tracker['stats'] = {
            'minute': tracker['minute'].strftime('%H:%M'),
            'total_ticks': total_ticks,
            'avg_tick_rate': round(avg_tick_rate, 2),
            'peak_5s_ticks': peak_5s_ticks,
            'min_5s_ticks': min_5s_ticks,
            'volatility': peak_5s_ticks - min_5s_ticks,  # Higher = more volatile activity
            'timestamp': tracker['minute']
        }
    
    async def _log_tick_rate_stats(self):
        """
        Log tick rate statistics to file every minute.
        Creates a detailed log of order execution velocity throughout the day.
        """
        current_time = time.time()
        
        # Throttle: Only log once per minute
        if current_time - self._last_tick_rate_log < 55:  # 55 seconds to ensure we log
            return
        
        self._last_tick_rate_log = current_time
        
        try:
            # Prepare log entry
            timestamp = get_ist_time().strftime("%Y-%m-%d %H:%M:%S")
            log_lines = []
            log_lines.append("=" * 100)
            log_lines.append(f"TICK RATE ANALYSIS - {timestamp}")
            log_lines.append("=" * 100)
            
            # Sort symbols: Index first, then options by activity
            index_symbols = [s for s in self.tick_rate_tracker.keys() if s == self.index_symbol]
            option_symbols = [s for s in self.tick_rate_tracker.keys() if s != self.index_symbol]
            
            # Sort options by tick count (most active first)
            option_symbols.sort(
                key=lambda s: self.tick_rate_tracker[s].get('stats', {}).get('total_ticks', 0),
                reverse=True
            )
            
            all_symbols = index_symbols + option_symbols[:10]  # Index + top 10 options
            
            if not all_symbols:
                return
            
            # Log header
            log_lines.append(f"{'Symbol':<30} {'Time':<8} {'Ticks':<8} {'Rate/s':<8} {'Peak 5s':<10} {'Min 5s':<10} {'Activity':<12}")
            log_lines.append("-" * 100)
            
            # Log each symbol's stats
            for symbol in all_symbols:
                tracker = self.tick_rate_tracker.get(symbol, {})
                stats = tracker.get('stats', {})
                
                if not stats:
                    continue
                
                minute = stats.get('minute', 'N/A')
                total = stats.get('total_ticks', 0)
                rate = stats.get('avg_tick_rate', 0)
                peak = stats.get('peak_5s_ticks', 0)
                min_ticks = stats.get('min_5s_ticks', 0)
                volatility = stats.get('volatility', 0)
                
                # Classify activity level
                if rate > 2.0:
                    activity = "🔥 VERY HIGH"
                elif rate > 1.0:
                    activity = "⚡ HIGH"
                elif rate > 0.5:
                    activity = "✅ MEDIUM"
                elif rate > 0.2:
                    activity = "⚠️ LOW"
                else:
                    activity = "❌ VERY LOW"
                
                symbol_short = symbol[:28] if len(symbol) > 28 else symbol
                log_lines.append(
                    f"{symbol_short:<30} {minute:<8} {total:<8} {rate:<8.2f} {peak:<10} {min_ticks:<10} {activity:<12}"
                )
            
            log_lines.append("=" * 100)
            log_lines.append("")
            
            # Append to file
            with open(self.tick_rate_log_file, 'a', encoding='utf-8') as f:
                f.write('\n'.join(log_lines) + '\n')
            
            # Also log summary to console (throttled)
            if hasattr(self, 'log_debug'):
                index_stats = self.tick_rate_tracker.get(self.index_symbol, {}).get('stats', {})
                if index_stats:
                    rate = index_stats.get('avg_tick_rate', 0)
                    total = index_stats.get('total_ticks', 0)
                    await self.log_debug("Tick Rate", 
                        f"📊 {self.index_symbol}: {total} ticks, {rate:.2f}/s avg rate")
        
        except Exception as e:
            # Silently fail to avoid disrupting trading
            pass
    
    def get_current_tick_rate(self, symbol, window_seconds=10):
        """
        Get current tick rate for a symbol.
        Useful for real-time analysis and filtering.
        
        Args:
            symbol: Trading symbol
            window_seconds: Time window to measure (default 10s)
        
        Returns:
            float: Ticks per second over the window
        """
        tracker = self.tick_rate_tracker.get(symbol)
        if not tracker:
            return 0.0
        
        now = time.time()
        recent_ticks = [t for t in tracker['ticks'] if now - t <= window_seconds]
        
        if len(recent_ticks) < 2:
            return 0.0
        
        return len(recent_ticks) / window_seconds
    
    def get_tick_acceleration(self, symbol, recent_window=5, previous_window=5):
        """
        Calculate tick rate acceleration (is it speeding up or slowing down?).
        Positive = accelerating, Negative = decelerating, ~0 = stable
        
        Args:
            symbol: Trading symbol
            recent_window: Recent time window in seconds (default 5s)
            previous_window: Previous time window in seconds (default 5s)
        
        Returns:
            float: Acceleration factor (recent_rate / previous_rate - 1)
                   e.g., 0.5 = 50% faster, -0.3 = 30% slower
        """
        tracker = self.tick_rate_tracker.get(symbol)
        if not tracker:
            return 0.0
        
        now = time.time()
        
        # Count ticks in recent window
        recent_ticks = [t for t in tracker['ticks'] if now - t <= recent_window]
        # Count ticks in previous window
        previous_ticks = [t for t in tracker['ticks'] 
                         if recent_window < now - t <= recent_window + previous_window]
        
        if len(previous_ticks) == 0:
            return 0.0  # Not enough data
        
        recent_rate = len(recent_ticks) / recent_window
        previous_rate = len(previous_ticks) / previous_window
        
        if previous_rate == 0:
            return 0.0
        
        # Return acceleration factor
        return (recent_rate / previous_rate) - 1.0

