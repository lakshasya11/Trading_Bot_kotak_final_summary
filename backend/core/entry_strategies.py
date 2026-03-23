# backend/core/entry_strategies.py
import math
import asyncio
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

# ==============================================================================
# SECTION 1: CANDLESTICK PATTERN HELPER FUNCTIONS
# ==============================================================================

def is_bullish_engulfing(prev, last):
    if prev is None or last is None or pd.isna(prev['open']) or pd.isna(last['open']): return False
    prev_body = abs(prev['close'] - prev['open'])
    last_body = abs(last['close'] - last['open'])
    return (prev['close'] < prev['open'] and last['close'] > last['open'] and
            last['close'] > prev['open'] and last['open'] < prev['close'] and
            last_body > prev_body * 0.8)

def is_bearish_engulfing(prev, last):
    if prev is None or last is None or pd.isna(prev['open']) or pd.isna(last['open']): return False
    prev_body = abs(prev['close'] - prev['open'])
    last_body = abs(last['close'] - last['open'])
    return (prev['close'] > prev['open'] and last['close'] < last['open'] and
            last['open'] > prev['close'] and last['close'] < prev['open'] and
            last_body > prev_body * 0.8)

def is_morning_star(c1, c2, c3):
    if c1 is None or c2 is None or c3 is None or any(pd.isna(c['open']) for c in [c1, c2, c3]): return False
    b1 = abs(c1['close'] - c1['open']); b2 = abs(c2['close'] - c2['open']); b3 = abs(c3['close'] - c3['open'])
    return (c1['close'] < c1['open'] and b2 < b1 * 0.3 and c2['high'] < c1['close'] and 
            c3['open'] > c2['high'] and c3['close'] > c3['open'] and b3 > b1 * 0.6)

def is_evening_star(c1, c2, c3):
    if c1 is None or c2 is None or c3 is None or any(pd.isna(c['open']) for c in [c1, c2, c3]): return False
    b1 = abs(c1['close'] - c1['open']); b2 = abs(c2['close'] - c2['open']); b3 = abs(c3['close'] - c3['open'])
    return (c1['close'] > c1['open'] and b2 < b1 * 0.3 and c2['low'] > c1['close'] and 
            c3['open'] < c2['low'] and c3['close'] < c3['open'] and b3 > b1 * 0.6)

def is_hammer(c):
    if c is None or pd.isna(c['open']): return False
    body = abs(c['close'] - c['open'])
    if body == 0: return False
    lower_wick = min(c['open'], c['close']) - c['low']
    upper_wick = c['high'] - max(c['open'], c['close'])
    price_range = c['high'] - c['low']
    return (lower_wick > body * 2.5 and upper_wick < body * 0.5 and (min(c['open'], c['close']) - c['low']) > price_range * 0.6)

def is_hanging_man(c):
    return is_hammer(c)

def is_doji(c, tol=0.05):
    if c is None or pd.isna(c['open']): return False
    body = abs(c['close'] - c['open']); rng = c['high'] - c['low']
    if rng == 0: return False
    return (body / rng) < tol

# ==============================================================================
# SECTION 2: BASE CLASS AND ALL ENTRY STRATEGIES
# ==============================================================================

class BaseEntryStrategy(ABC):
    def __init__(self, strategy_instance):
        self.strategy = strategy_instance
        self.params = strategy_instance.params
        self.data_manager = strategy_instance.data_manager

    @abstractmethod
    async def check(self):
        pass

    async def _validate_entry_conditions(self, side, opt):
        if not opt: return False
        symbol = opt['tradingsymbol']
        strike = opt['strike']
        
        # --- NEW CONFORMATION: Option Price must be above its current 1-minute open (Green Candle) ---
        option_candle = self.data_manager.option_candles.get(symbol)
        current_price = self.data_manager.prices.get(symbol)
        
        if option_candle and 'open' in option_candle and current_price and current_price <= option_candle['open']:
             await self.strategy._log_debug("Validation", f"REJECTED {symbol}: Option price {current_price} is not above its 1-min open {option_candle['open']}.")
             return False
        # --- END NEW CONFIRMATION ---
        
        # Late entry check REMOVED - Allow entries at any time during candle

        if not self.data_manager.is_average_price_trending(symbol, 'up'):
            return False

        if not await self._is_opposite_falling(side, strike):
            return False
            
        if not self._momentum_ok(side, symbol): return False
        if not self._is_accelerating(symbol): return False
        await self.strategy._log_debug("Validation", f"PASS: All entry conditions met for {symbol}.")
        return True

    async def _is_opposite_falling(self, side, strike):
        opposite_side = 'PE' if side == 'CE' else 'CE'
        opposite_opt = self.strategy.get_entry_option(opposite_side, strike)
        if not opposite_opt: return True
        
        opposite_symbol = opposite_opt['tradingsymbol']
        return self.data_manager.is_average_price_trending(opposite_symbol, 'down')

    def _momentum_ok(self, side, opt_sym, look=20):
        idx_prices = self.data_manager.price_history.get(self.strategy.index_symbol, [])
        opt_prices = self.data_manager.price_history.get(opt_sym, [])
        if len(idx_prices) < look or len(opt_prices) < look: return False
        
        # Get prices only, discard timestamps for this calculation
        idx_price_values = [p for ts, p in idx_prices]
        opt_price_values = [p for ts, p in opt_prices]

        idx_up = sum(1 for i in range(1, look) if idx_price_values[-i] > idx_price_values[-i - 1])
        opt_up = sum(1 for i in range(1, look) if opt_price_values[-i] > opt_price_values[-i - 1])
        
        if side == 'CE':
            return idx_up >= 1 and opt_up >= 1
        else: # PE
            idx_dn = (look - 1) - idx_up
            return idx_dn >= 1 and opt_up >= 1

    def _is_accelerating(self, symbol, lookback_ticks=20, acceleration_factor=2.0):
        history = self.data_manager.price_history.get(symbol, [])
        if len(history) < lookback_ticks: return False
        
        # Get prices only from (timestamp, price) tuples
        prices = [p for ts, p in history]

        recent_prices = prices[-lookback_ticks:]
        diffs = np.diff(recent_prices)
        if len(diffs) < 2: return False

        current_velocity = diffs[-1]
        avg_velocity = np.mean(diffs[:-1])

        if current_velocity <= 0: return False
        if avg_velocity > 0 and current_velocity > avg_velocity * acceleration_factor:
            return True
            
        return False

# ==============================================================================
# DUAL-OPTION MONITOR STRATEGY (PRIORITY 1)
# ==============================================================================

class DualOptionMonitorStrategy(BaseEntryStrategy):
    """
    🎯 DUAL-OPTION MONITOR STRATEGY
    
    Monitors BOTH CE and PE options simultaneously and enters whichever shows
    the strongest setup, regardless of index trend direction.
    
    Scoring System (0-100 points):
    - Green Candle: 30 points (LTP > Open)
    - Velocity: 30 points (speed of price movement)
    - Momentum: 20 points (40-second average trending up)
    - Candle Position: 20 points (LTP near high of candle)
    
    Entry Criteria:
    - Option must score > 70/100
    - If both score > 70, enter the one with higher score
    - Still validates through standard filters (validation passed)
    """
    
    def __init__(self, strategy_instance):
        super().__init__(strategy_instance)
        self.min_score_threshold = 70  # Configurable minimum score
        self.confirmation_ticks = 3  # Require score to remain high for 3 ticks
        self.score_history = {}  # Track scores over time {symbol: [scores]}
        self.last_log_time = 0  # Throttle score logging
        self.log_interval = 2.0  # Log scores every 2 seconds
        self.last_atm_strike = None  # Track ATM changes to clean history
    
    async def check(self):
        """Check both CE and PE, return the strongest option"""
        
        # Don't check if already in position
        if self.strategy.position:
            return None, None, None
        
        current_price = self.data_manager.prices.get(self.strategy.index_symbol)
        if not current_price:
            return None, None, None
        
        # Calculate ATM strike
        atm_strike = self.strategy.strike_step * round(current_price / self.strategy.strike_step)
        
        # Clean up score history if ATM changed (prevent memory leak)
        if self.last_atm_strike != atm_strike:
            # Keep only current and adjacent strikes
            valid_strikes = [atm_strike - self.strategy.strike_step, atm_strike, atm_strike + self.strategy.strike_step]
            symbols_to_keep = set()
            for strike in valid_strikes:
                ce_opt = self.strategy.get_entry_option('CE', strike)
                pe_opt = self.strategy.get_entry_option('PE', strike)
                if ce_opt:
                    symbols_to_keep.add(ce_opt['tradingsymbol'])
                if pe_opt:
                    symbols_to_keep.add(pe_opt['tradingsymbol'])
            
            # Remove old symbols from history
            symbols_to_remove = set(self.score_history.keys()) - symbols_to_keep
            for symbol in symbols_to_remove:
                del self.score_history[symbol]
            
            self.last_atm_strike = atm_strike
        
        # Get both CE and PE options
        ce_option = self.strategy.get_entry_option('CE', atm_strike)
        pe_option = self.strategy.get_entry_option('PE', atm_strike)
        
        # Score both options
        ce_score = await self._score_option(ce_option, 'CE')
        pe_score = await self._score_option(pe_option, 'PE')
        
        # Update score history for confirmation
        if ce_option:
            ce_symbol = ce_option['tradingsymbol']
            if ce_symbol not in self.score_history:
                self.score_history[ce_symbol] = []
            self.score_history[ce_symbol].append(ce_score)
            # Keep only last 5 scores
            self.score_history[ce_symbol] = self.score_history[ce_symbol][-5:]
        
        if pe_option:
            pe_symbol = pe_option['tradingsymbol']
            if pe_symbol not in self.score_history:
                self.score_history[pe_symbol] = []
            self.score_history[pe_symbol].append(pe_score)
            self.score_history[pe_symbol] = self.score_history[pe_symbol][-5:]
        
        # Log scores for visibility (throttled to avoid spam)
        import time as time_module
        current_time = time_module.time()
        if current_time - self.last_log_time >= self.log_interval:
            await self.strategy._log_debug("Dual Monitor", 
                f"📊 CE Score: {ce_score}/100 | PE Score: {pe_score}/100")
            self.last_log_time = current_time
        
        # Determine which option to enter
        selected_side = None
        selected_option = None
        selected_score = 0
        
        # Check if CE meets threshold and has confirmation
        if ce_score >= self.min_score_threshold and ce_option:
            ce_symbol = ce_option['tradingsymbol']
            if len(self.score_history.get(ce_symbol, [])) >= self.confirmation_ticks:
                recent_scores = self.score_history[ce_symbol][-self.confirmation_ticks:]
                if all(s >= self.min_score_threshold for s in recent_scores):
                    selected_side = 'CE'
                    selected_option = ce_option
                    selected_score = ce_score
        
        # Check if PE meets threshold and has confirmation (and is stronger than CE)
        if pe_score >= self.min_score_threshold and pe_option:
            pe_symbol = pe_option['tradingsymbol']
            if len(self.score_history.get(pe_symbol, [])) >= self.confirmation_ticks:
                recent_scores = self.score_history[pe_symbol][-self.confirmation_ticks:]
                if all(s >= self.min_score_threshold for s in recent_scores):
                    if pe_score > selected_score:  # PE is stronger
                        selected_side = 'PE'
                        selected_option = pe_option
                        selected_score = pe_score
        
        # If we have a winner, validate and return
        if selected_side and selected_option:
            await self.strategy._log_debug("Dual Monitor", 
                f"🎯 {selected_side} selected with score {selected_score}/100")
            
            # Validate through standard entry conditions
            if await self._validate_entry_conditions(selected_side, selected_option):
                reason = f'DualMonitor_{selected_side}_Score_{selected_score}'
                return selected_side, reason, selected_option
            else:
                await self.strategy._log_debug("Dual Monitor", 
                    f"❌ {selected_side} validation failed despite high score")
        
        return None, None, None
    
    async def _score_option(self, option, side):
        """
        Score an option from 0-100 based on multiple criteria
        
        Returns:
            int: Score from 0-100
        """
        if not option:
            return 0
        
        symbol = option['tradingsymbol']
        ltp = self.data_manager.prices.get(symbol)
        
        if not ltp:
            return 0
        
        score = 0
        score_breakdown = []
        
        # CRITERION 1: Green Candle (30 points)
        candle = self.data_manager.option_candles.get(symbol)
        if candle and 'open' in candle:
            candle_open = candle.get('open', ltp)
            if ltp > candle_open:
                score += 30
                score_breakdown.append(f"Green:30")
            else:
                score_breakdown.append(f"Red:0")
        
        # CRITERION 2: Velocity (30 points)
        try:
            velocity = self.strategy.calculate_price_velocity(symbol, lookback_seconds=1.5)
            if velocity >= 1.0:
                score += 30
                score_breakdown.append(f"Vel:30(₹{velocity:.2f}/s)")
            elif velocity >= 0.5:
                score += 20
                score_breakdown.append(f"Vel:20(₹{velocity:.2f}/s)")
            elif velocity >= 0.1:
                score += 10
                score_breakdown.append(f"Vel:10(₹{velocity:.2f}/s)")
            else:
                score_breakdown.append(f"Vel:0(₹{velocity:.2f}/s)")
        except:
            score_breakdown.append("Vel:0")
        
        # CRITERION 3: Momentum (20 points) - 40-second average trending up
        if self.data_manager.is_average_price_trending(symbol, 'up'):
            score += 20
            score_breakdown.append("Mom:20")
        else:
            score_breakdown.append("Mom:0")
        
        # CRITERION 4: Early Body Formation (20 points) - Enter when body STARTS, not at peak
        if candle and 'high' in candle and 'low' in candle and 'open' in candle:
            candle_open = candle.get('open', ltp)
            candle_high = candle.get('high', ltp)
            candle_low = candle.get('low', ltp)
            
            # Calculate body % from open
            if candle_open > 0:
                body_percent = abs((ltp - candle_open) / candle_open) * 100
                
                # GREEN CANDLE: Check if body is forming early
                if ltp > candle_open:
                    # Early entry zones (prioritize catching move early!)
                    if body_percent >= 1.0 and body_percent <= 8.0:
                        # Sweet spot: Body just started (1-8%) - BEST ENTRY!
                        position_score = 20
                        score_breakdown.append(f"Pos:20(Early{body_percent:.1f}%)")
                    elif body_percent > 8.0 and body_percent <= 15.0:
                        # Good entry: Body forming (8-15%)
                        position_score = 18
                        score_breakdown.append(f"Pos:18(Good{body_percent:.1f}%)")
                    elif body_percent > 15.0 and body_percent <= 25.0:
                        # OK entry: Body mid-size (15-25%)
                        position_score = 15
                        score_breakdown.append(f"Pos:15(Mid{body_percent:.1f}%)")
                    elif body_percent > 25.0:
                        # Late entry: Large body already formed (>25%) - might be near peak!
                        # Check if still has momentum by looking at position in range
                        candle_range = candle_high - candle_low
                        if candle_range > 0:
                            position = (ltp - candle_low) / candle_range
                            if position >= 0.9:  # At peak (>90%)
                                position_score = 5  # Too late!
                                score_breakdown.append(f"Pos:5(Late{body_percent:.1f}%Peak)")
                            elif position >= 0.7:  # High but not peak
                                position_score = 10
                                score_breakdown.append(f"Pos:10(Late{body_percent:.1f}%)")
                            else:  # Still has room
                                position_score = 15
                                score_breakdown.append(f"Pos:15(Late{body_percent:.1f}%)")
                        else:
                            position_score = 10
                            score_breakdown.append(f"Pos:10(Late{body_percent:.1f}%)")
                    else:
                        # Body too small (<1%) - wait for confirmation
                        position_score = 5
                        score_breakdown.append(f"Pos:5(Tiny{body_percent:.1f}%)")
                    
                    score += position_score
                else:
                    # RED CANDLE: No points
                    score_breakdown.append("Pos:0(Red)")
            else:
                score_breakdown.append("Pos:0(NoOpen)")
        
        # Log detailed breakdown if score is significant
        if score >= 50:
            await self.strategy._log_debug("Dual Monitor Score", 
                f"{symbol}: {score}/100 [{', '.join(score_breakdown)}]")
        
        return score

class UoaEntryStrategy(BaseEntryStrategy):
    async def check(self):
        if not self.strategy.uoa_watchlist: return None, None, None
        
        for token, data in list(self.strategy.uoa_watchlist.items()):
            symbol, side, strike = data['symbol'], data['type'], data['strike']
            option_candle = self.data_manager.option_candles.get(symbol)
            current_price = self.data_manager.prices.get(symbol)
            if not option_candle or 'open' not in option_candle or not current_price: continue
            if current_price <= option_candle['open']:
                await self.strategy._log_debug("UOA Trigger", f"REJECTED: {symbol} price {current_price} is not above its 1-min open {option_candle['open']}.")
                continue
            
            # Late entry check REMOVED - Allow UOA entries at any time during candle
            
            opt = self.strategy.get_entry_option(side, strike)
            
            if await self._validate_entry_conditions1(side, opt):
                del self.strategy.uoa_watchlist[token]
                await self.strategy._update_ui_uoa_list()
                return side, "UOA_Entry", opt
        return None, None, None

    async def _validate_entry_conditions1(self, side, opt):
        if not opt: return False
        symbol = opt['tradingsymbol']
        log_report = []

        is_trending = self.data_manager.is_average_price_trending(symbol, 'up')
        log_report.append(f"Avg Price Trending: {is_trending}")
        if not is_trending:
            await self.strategy._log_debug("UOA Validation", f"REJECTED {symbol} | Report: [{', '.join(log_report)}]")
            return False

        momentum_is_ok = self._momentum_ok(side, symbol)
        log_report.append(f"Momentum OK: {momentum_is_ok}")
        if not momentum_is_ok:
            await self.strategy._log_debug("UOA Validation", f"REJECTED {symbol} | Report: [{', '.join(log_report)}]")
            return False
        
        await self.strategy._log_debug("UOA Validation", f"PASS {symbol} | Report: [{', '.join(log_report)}]")
        return True

class TrendContinuationStrategy(BaseEntryStrategy):
    async def check(self):
        trend = self.data_manager.trend_state
        if not trend or len(self.data_manager.data_df) < 2: return None, None, None
        prev_candle = self.data_manager.data_df.iloc[-1]
        current_price = self.data_manager.prices.get(self.strategy.index_symbol)
        if not current_price: return None, None, None
        side, reason = None, None
        
        # --- MODIFIED LOGIC: Index Breakout Check ---
        if trend == 'BULLISH' and current_price > prev_candle['high']: 
            side, reason = 'CE', 'Trend_Continuation_CE_Breakout'
        elif trend == 'BEARISH' and current_price < prev_candle['low']: 
            side, reason = 'PE', 'Trend_Continuation_PE_Breakout'
        # --- END MODIFIED LOGIC ---

        if side:
            opt = self.strategy.get_entry_option(side)
            # _validate_entry_conditions now contains the "Green Candle" check
            if await self._validate_entry_conditions(side, opt):
                return side, reason, opt
        return None, None, None

class MaCrossoverStrategy(BaseEntryStrategy):
    async def check(self):
        df = self.data_manager.data_df
        if len(df) < 2: return None, None, None
        last, prev = df.iloc[-1], df.iloc[-2]
        if any(pd.isna(v) for v in [last['wma'], last['sma'], prev['wma'], prev['sma']]): return None, None, None
        side, reason = None, None
        if prev['wma'] <= prev['sma'] and last['wma'] > last['sma'] and last['close'] > last['open']: side, reason = 'CE', "MA_Crossover_CE"
        elif prev['wma'] >= prev['sma'] and last['wma'] < last['sma'] and last['close'] < last['open']: side, reason = 'PE', "MA_Crossover_PE"
        if side:
            opt = self.strategy.get_entry_option(side)
            if await self._validate_entry_conditions(side, opt):
                return side, reason, opt
        return None, None, None

class CandlePatternEntryStrategy(BaseEntryStrategy):
    async def check(self):
        df = self.data_manager.data_df
        if len(df) < 3 or not self.data_manager.trend_state: return None, None, None
        last = df.iloc[-1]
        pattern, side = None, None
        if is_doji(last) and self.strategy.trend_candle_count >= 5:
            if self.data_manager.trend_state == 'BULLISH': pattern, side = 'Doji_Reversal_Elite', 'PE'
            elif self.data_manager.trend_state == 'BEARISH': pattern, side = 'Doji_Reversal_Elite', 'CE'
        if pattern:
            opt = self.strategy.get_entry_option(side)
            if await self._validate_entry_conditions(side, opt):
                return side, pattern, opt
        return None, None, None

class IntraCandlePatternStrategy(BaseEntryStrategy):
    async def check(self):
        if self.strategy.position: return None, None, None
        df = self.data_manager.data_df
        if len(df) < 3 or 'open' not in self.data_manager.current_candle: return None, None, None
        live_candle = self.data_manager.current_candle
        prev_candle = df.iloc[-1]
        prev_candle_2 = df.iloc[-2]
        pattern, side = None, None
        if is_bullish_engulfing(prev_candle, live_candle): pattern, side = 'Live_BullishEngulf', 'CE'
        elif is_bearish_engulfing(prev_candle, live_candle): pattern, side = 'Live_BearishEngulf', 'PE'
        elif is_morning_star(prev_candle_2, prev_candle, live_candle): pattern, side = 'Live_MorningStar', 'CE'
        elif is_evening_star(prev_candle_2, prev_candle, live_candle): pattern, side = 'Live_EveningStar', 'PE'
        elif is_hammer(live_candle) and self.data_manager.trend_state == 'BEARISH': pattern, side = 'Live_Hammer', 'CE'
        elif is_hanging_man(live_candle) and self.data_manager.trend_state == 'BULLISH': pattern, side = 'Live_HangingMan', 'PE'
        if pattern:
            opt = self.strategy.get_entry_option(side)
            if await self._validate_entry_conditions(side, opt):
                return side, pattern, opt
        return None, None, None