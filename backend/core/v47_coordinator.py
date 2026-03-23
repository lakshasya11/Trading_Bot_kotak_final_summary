# backend/core/v47_coordinator.py
import asyncio
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple

# 🔥 Import IST timezone utilities
def get_ist_time():
    """Get current time in IST timezone"""
    from datetime import timezone
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    utc_now = datetime.now(timezone.utc)
    return utc_now.astimezone(IST)

class V47StrategyCoordinator:
    """V47.14 Strategy Coordination System"""
    
    def __init__(self, strategy):
        self.strategy = strategy
        
        # V47.14 specific state
        self.atr_squeeze_detected = False
        self.squeeze_range = {'high': 0, 'low': 0}
        self.pending_steep_signal = None
        
        # --- NEW: Infrastructure for Option-specific Indicators ---
        self.option_data_dfs = {}  # Stores DataFrame for each option
        self.option_supertrend_state = {}  # Stores supertrend state for each option
        self.previous_option_candles = {}  # Stores previous option candles
        # --- END OF NEW INFRASTRUCTURE ---
        
        # ✅ PURE MODE: Only Trend Continuation Engine (77-80% WR)
        # All other engines removed - they were losing strategies
        self.trend_engine = V47TrendContinuationEngine(strategy)
        
        self.engines = [
            self.trend_engine  # Primary: 77-80% win rate, best quality
        ]
    
    async def on_new_candle(self):
        """Called when a new minute candle is formed"""
        # Update ATR squeeze detection
        self._check_atr_squeeze()
        
        # Check all engines in priority order
        await self.scan_for_signals()
    
    async def continuous_monitoring(self):
        """Called every few seconds for intra-candle analysis"""
        if not await self.strategy.can_trade():
            return
        
        # Periodic status update (every 10 seconds when actively monitoring)
        if not hasattr(self, '_last_status_log_time'):
            self._last_status_log_time = 0
            self._monitoring_cycles = 0
        
        self._monitoring_cycles += 1
        current_time = time.time()
        
        if current_time - self._last_status_log_time >= 10.0:
            index_price = self.strategy.data_manager.prices.get(self.strategy.index_symbol, 0)
            trend = self.strategy.data_manager.trend_state or "---"
            await self.strategy._log_debug(
                "Scanner",
                f"📊 {self.strategy.index_name}: ₹{index_price:.2f} | Trend: {trend} | Monitoring active"
            )
            self._last_status_log_time = current_time
        
        # 🔥 CRITICAL FIX: Check for EXIT signals if position is open
        if self.strategy.position:
            await self._check_exit_signals()
            
        # Check engines in priority order for ENTRY signals
        await self.scan_for_signals()
    
    async def scan_for_signals(self):
        """Scan all engines in priority order with timeout protection"""
        # ⚡ CRITICAL: Block scanning if entry is in progress to prevent duplicate signals
        if self.strategy.entry_in_progress:
            # 🔥 AUTO-RESET: If entry flag is stuck (>8s), reset it so scanning can resume
            # This handles cases where check_trade_entry() was cancelled mid-execution by a timeout
            if self.strategy.entry_started_at:
                try:
                    stuck_seconds = (get_ist_time() - self.strategy.entry_started_at).total_seconds()
                    if stuck_seconds > 8:
                        await self.strategy._log_debug("Entry Guard",
                            f"⚠️ entry_in_progress stuck {stuck_seconds:.1f}s - auto-resetting to unblock scanning")
                        self.strategy.entry_in_progress = False
                        self.strategy.entry_started_at = None
                        # Fall through to continue scanning
                    else:
                        return
                except Exception:
                    return
            else:
                return
        
        # 🚀 FAST ENTRY PRIORITY CHECK: If pre-calculated conditions met, enter immediately (0.5-2s)
        # This runs BEFORE engine scans to enable rapid entry when candle body starts forming
        # Pre-calculation runs at candle close (10:30:59), fast entry triggers at 10:31:01
        try:
            fast_entry_result = await self.strategy.check_fast_entry_conditions()
            if fast_entry_result:
                # Fast entry conditions met! Take trade immediately
                symbol, side, entry_data = fast_entry_result
                
                # Get option details for trade execution
                spot = self.strategy.data_manager.prices.get(self.strategy.index_symbol, 0)
                if spot:
                    atm_strike = self.strategy.strike_step * round(spot / self.strategy.strike_step)
                    if side == "CE":
                        option = self.strategy.get_entry_option("CE", atm_strike)
                    else:
                        option = self.strategy.get_entry_option("PE", atm_strike)
                    
                    if option and option.get('tradingsymbol') == symbol:
                        # 🏆 Execute fast entry with pre-calculated data
                        momentum_data = {
                            'momentum_checks_passed': 5,  # Pre-calc already validated full conditions
                            'st_angle': entry_data.get('st_angle'),
                            'st_accel': entry_data.get('st_accel'),
                            'velocity': entry_data.get('velocity')
                        }
                        
                        signal_generation_time = entry_data.get('timestamp').timestamp() if entry_data.get('timestamp') else time.time()
                        
                        await self.strategy._log_debug("Fast Entry", 
                            f"⚡ FAST ENTRY ACTIVATED: {symbol} ({side}) | "
                            f"Velocity: ₹{entry_data.get('velocity', 0):.2f}/s | "
                            f"ST Angle: {entry_data.get('st_angle', 0):.2f}%")
                        
                        await self.strategy.take_trade(
                            "FAST_ENTRY_PRE_CALCULATED", 
                            option, 
                            momentum_data=momentum_data,
                            signal_generation_time=signal_generation_time
                        )
                        return  # Exit after fast entry attempt
        except Exception as e:
            await self.strategy._log_debug("Fast Entry", f"⚠️ Error in fast entry check: {e}")
        
        # ⚡ PERFORMANCE: Light throttle to prevent excessive calls while maintaining responsiveness
        import time
        current_time = time.time()
        if not hasattr(self, '_last_scan_time'):
            self._last_scan_time = 0
        
        # Skip if called too frequently (< 200ms since last scan)
        if current_time - self._last_scan_time < 0.2:
            return
        
        self._last_scan_time = current_time
        
        # 📐 SUPERTREND ANGLE STRATEGY: 🔴 DISABLED - Using only Trend Continuation and No-Wick Bypass
        # if self.strategy.params.get('st_angle_enabled', False):
        #     ... ST angle logic removed ...
        
        for engine in self.engines:
            try:
                # 🔥 CRITICAL: Capture signal generation time BEFORE validation
                signal_generation_time = time.time()
                
                # CRITICAL FIX: Add timeout to prevent engine from hanging
                # 🔍 ENABLE LOGGING: Pass log=True to see NO-WICK detection and validation details
                result = await asyncio.wait_for(engine.check_entry(log=True), timeout=5.0)
                
                # Handle both old format (side, trigger, option) and new format with validation data
                if len(result) == 3:
                    side, trigger, option = result
                    validation_data = None
                elif len(result) == 4:
                    side, trigger, option, validation_data = result
                else:
                    continue
                
                if side and trigger and option:
                    # For signals with validation_data (Trend Continuation, Red-Green), validation already done
                    if validation_data and 'prev_close' in validation_data:
                        custom_entry_price = validation_data['prev_close'] + 0.10
                        
                        # Get full momentum data from validation_data (includes all signal flags)
                        momentum_data = validation_data.get('momentum_data', {
                            'momentum_checks_passed': validation_data.get('momentum_passed', 0)
                        })
                        
                        await self.strategy._log_debug("V47.14", f"🎯 {trigger} validated")
                        # 🔥 Pass signal generation time to track from signal to entry
                        await self.strategy.take_trade(trigger, option, custom_entry_price=custom_entry_price, momentum_data=momentum_data, signal_generation_time=signal_generation_time)
                        return  # Stop after trade attempt
                    else:
                        # For other signals, validate through universal gauntlet (with timeout)
                        try:
                            is_valid, momentum_data = await asyncio.wait_for(
                                self.universal_validation_gauntlet(option, side, trigger),
                                timeout=1.5
                            )
                            
                            if is_valid:
                                await self.strategy._log_debug("V47.14", f"🎯 Signal validated: {trigger}")
                                # 🔥 Pass signal generation time to track from signal to entry
                                await self.strategy.take_trade(trigger, option, momentum_data=momentum_data, signal_generation_time=signal_generation_time)
                                return  # ✅ FIX: Only return if validation PASSED and trade attempted
                            else:
                                # Validation failed - continue to next engine
                                await self.strategy._log_debug("V47.14", f"❌ {trigger} validation failed, trying next engine")
                                continue
                        except asyncio.TimeoutError:
                            # ⚡ THROTTLE: Only log timeout once per second per trigger
                            import time
                            current_time = time.time()
                            if not hasattr(self, '_last_timeout_log'):
                                self._last_timeout_log = {}
                            log_key = f"timeout_{trigger}"
                            if current_time - self._last_timeout_log.get(log_key, 0) >= 1.0:
                                await self.strategy._log_debug("V47.14", f"⚠️ Validation timeout for {trigger}, trying next engine")
                                self._last_timeout_log[log_key] = current_time
                            continue  # Try next engine on timeout
                        
            except asyncio.TimeoutError:
                await self.strategy._log_debug("V47.14", f"⚠️ Engine check_entry() timed out, skipping")
                continue  # Try next engine
            except Exception as e:
                await self.strategy._log_debug("V47.14", f"Engine error: {e}")
                continue  # Try next engine
    
    async def _check_exit_signals(self):
        """🔥 CRITICAL: Monitor position for exit signals - ALL checks every 200ms"""
        if not self.strategy.position:
            return
        
        # 🛡️ PREVENT DUPLICATE EXITS
        if hasattr(self.strategy, 'exit_in_progress') and self.strategy.exit_in_progress:
            return
        
        # Throttle ALL checks to 50ms - ⚡ Faster for real-time responsiveness
        if not hasattr(self, '_last_exit_check_time'):
            self._last_exit_check_time = 0
        
        current_time = time.time()
        if (current_time - self._last_exit_check_time) < 0.05:  # ⚡ 50ms throttle (was 200ms) - 4x faster
            return
        
        self._last_exit_check_time = current_time
        
        try:
            p = self.strategy.position
            
            # 🛡️ CRITICAL: Skip ALL coordinator exits in Aggressive Hold mode
            # Aggressive Hold only uses evaluate_exit_logic() exits (Red Candle, Break-even, Max Hold Time)
            exit_mode = self.strategy.params.get("exit_mode", "Standard")
            if exit_mode == "Aggressive Hold":
                # Don't run ANY coordinator exit logic in Aggressive Hold mode
                return
            
            symbol = p.get('symbol')
            direction = p.get('direction')
            entry_price = float(p.get('entry_price', 0)) if p.get('entry_price') else 0
            current_price_raw = self.strategy.data_manager.prices.get(symbol, entry_price)
            
            # 🛡️ CRITICAL: Convert current_price to float (might be string from websocket)
            try:
                current_price = float(current_price_raw) if current_price_raw else entry_price
            except (ValueError, TypeError):
                current_price = entry_price
            
            exit_detection_time = get_ist_time().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            profit_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            
            # 🛡️ SAFE FLOAT CONVERSION - Handle empty strings from GUI
            def safe_float(val, default):
                """Convert to float, return default if empty or invalid"""
                if val is None or val == '' or val == 'undefined':
                    return default
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default
            
            # 🔥 BREAK-EVEN ACTIVATION & TSL TIGHTENING (happens every 200ms)
            if not p.get("break_even_activated", False) and entry_price and current_price:
                break_even_threshold = safe_float(self.strategy.params.get("break_even_threshold_pct", 2.0), 2.0)  # ✅ FROM GUI: BE %
                
                if profit_pct >= break_even_threshold:
                    break_even_target = round(entry_price * (1 + break_even_threshold / 100), 2)  # ✅ FIXED: Use GUI threshold, not hardcoded 0.75%
                    old_trail_sl = p.get("trail_sl", entry_price)
                    p["trail_sl"] = max(old_trail_sl, break_even_target)
                    await self.strategy._log_debug("BREAK-EVEN", 
                        f"✅ ACTIVATED! Profit: {profit_pct:.2f}% (threshold: {break_even_threshold:.1f}%). TSL: ₹{old_trail_sl:.2f} → ₹{p['trail_sl']:.2f} [Detection: {exit_detection_time}]")
                    p["break_even_activated"] = True
            
            # 🔥 TSL TIGHTENING (every 200ms)
            if entry_price and current_price:
                sl_points = safe_float(self.strategy.params.get("trailing_sl_points", 5.0), 5.0)  # ✅ FROM GUI: SL (Points)
                sl_percent = safe_float(self.strategy.params.get("trailing_sl_percent", 2.5), 2.5)  # ✅ FROM GUI: SL (%)
                max_price = p.get("max_price", current_price)
                
                if current_price > max_price:
                    p["max_price"] = current_price
                    max_price = current_price
                
                sl_by_points = max_price - sl_points
                sl_by_percent = max_price * (1 - sl_percent / 100)
                new_trail_sl = round(max(sl_by_points, sl_by_percent), 2)  # ✅ FIXED: max = tighter SL (matches strategy.py)
                
                if p.get("break_even_activated", False):
                    be_threshold = safe_float(self.strategy.params.get("break_even_threshold_pct", 2.0), 2.0)
                    be_target = entry_price * (1 + be_threshold / 100)  # ✅ FIXED: Match strategy.py formula
                    new_trail_sl = max(new_trail_sl, round(be_target, 2))
                
                old_trail_sl = p.get("trail_sl", entry_price)
                if new_trail_sl > old_trail_sl:
                    p["trail_sl"] = new_trail_sl
            
            # 🔥 PRIORITY 0: PROFIT TARGET (ULTRA-HIGH) - EXIT WHEN TARGET REACHED
            if entry_price and current_price:
                trade_profit_target = safe_float(self.strategy.params.get("trade_profit_target", 0), 0)
                
                if trade_profit_target > 0:
                    current_gross_profit = (current_price - entry_price) * p.get('qty', 1)
                    
                    if current_gross_profit >= trade_profit_target:
                        await self.strategy._log_debug("EXIT-SIGNAL",
                            f"🚪 [P0-PT] PROFIT TARGET HIT: ₹{current_gross_profit:.2f} >= ₹{trade_profit_target:.2f}, exiting {symbol} [Detection: {exit_detection_time}]")
                        await self.strategy.exit_position(f"Profit Target Hit (₹{current_gross_profit:.2f})")
                        return
            
            # 🔥 PRIORITY 1: TRAILING STOP LOSS (CRITICAL) - READ FROM GUI
            if entry_price and current_price:
                trail_sl_raw = self.strategy.position.get('trail_sl', entry_price)
                # 🛡️ CRITICAL: Convert trail_sl to float (might be string)
                try:
                    trail_sl = float(trail_sl_raw) if trail_sl_raw else entry_price
                except (ValueError, TypeError):
                    trail_sl = entry_price
                    
                if current_price <= trail_sl:
                    await self.strategy._log_debug("EXIT-SIGNAL",
                        f"🚪 [P1-TSL] TRAILING SL HIT: ₹{current_price:.2f} <= ₹{trail_sl:.2f}, exiting {symbol} [Detection: {exit_detection_time}]")
                    await self.strategy.exit_position("Trailing SL Hit")
                    return
            
            # 🔥 PRIORITY 2: ENTRY PRICE HIT (Break-Even)
            if entry_price and current_price:
                if current_price <= entry_price:
                    await self.strategy._log_debug("EXIT-SIGNAL",
                        f"🚪 [P2-BE] ENTRY PRICE HIT: Price returned to entry, exiting {symbol} @ ₹{current_price:.2f} [Detection: {exit_detection_time}]")
                    await self.strategy.exit_position(f"Entry Price Hit ({profit_pct:.2f}% profit)")
                    return
            
            # 🔥 PRIORITY 3: RED CANDLE
            if symbol:
                live_option_candle = self.strategy.data_manager.option_candles.get(symbol)
                if live_option_candle:
                    candle_open = live_option_candle.get('open', 0)
                    candle_close = live_option_candle.get('close', current_price)
                    
                    if candle_close < candle_open:
                        await self.strategy._log_debug("EXIT-SIGNAL",
                            f"🚪 [P3-CANDLE] RED CANDLE: Close ₹{candle_close:.2f} < Open ₹{candle_open:.2f}, exiting {symbol} [Detection: {exit_detection_time}]")
                        await self.strategy.exit_position("Red Candle (Momentum Loss)")
                        return
            
            # 🔥 PRIORITY 4: INDEX SUPERTREND FLIP
            if self.strategy.data_manager.data_df is not None and not self.strategy.data_manager.data_df.empty:
                index_trend = self.strategy.data_manager.trend_state
                
                if index_trend == 'BULLISH' and 'PE' in direction:
                    await self.strategy._log_debug("EXIT-SIGNAL", 
                        f"🚪 [P4-TREND] INDEX SUPERTREND FLIP: Trend BULLISH, exiting PE {symbol} [Detection: {exit_detection_time}]")
                    await self.strategy.exit_position("Index Supertrend Flip (Trend Reversal)")
                    return
                
                elif index_trend == 'BEARISH' and 'CE' in direction:
                    await self.strategy._log_debug("EXIT-SIGNAL",
                        f"🚪 [P4-TREND] INDEX SUPERTREND FLIP: Trend BEARISH, exiting CE {symbol} [Detection: {exit_detection_time}]")
                    await self.strategy.exit_position("Index Supertrend Flip (Trend Reversal)")
                    return
            
            # ❌ DISABLED: Engulfing exits cause premature exits on normal momentum consolidation
            # Bearish engulfing on CE shows strong momentum, not a reason to exit
            # Not part of V47.14 core specification. Let TSL handle price-based exits instead.
            # if symbol and direction and 'CE' in direction:
            #     live_option_candle = self.strategy.data_manager.option_candles.get(symbol)
            #     prev_option_candle = self.strategy.data_manager.previous_option_candles.get(symbol)
            #     
            #     if live_option_candle and prev_option_candle:
            #         if self.strategy._is_bearish_engulfing(
            #             __import__('pandas').Series(prev_option_candle),
            #             __import__('pandas').Series(live_option_candle)
            #         ):
            #             await self.strategy._log_debug("EXIT-SIGNAL",
            #                 f"🚪 [P5-PATTERN] BEARISH ENGULFING: Pattern on {symbol}, exiting CE [Detection: {exit_detection_time}]")
            #             await self.strategy.exit_position("Invalidation: Option Bearish Engulfing")
            #             return
            
            # ❌ DISABLED: Engulfing exits cause premature exits on normal momentum consolidation
            # Not part of V47.14 core specification. Let TSL handle price-based exits instead.
            # if symbol and direction and 'PE' in direction:
            #     live_option_candle = self.strategy.data_manager.option_candles.get(symbol)
            #     prev_option_candle = self.strategy.data_manager.previous_option_candles.get(symbol)
            #     
            #     if live_option_candle and prev_option_candle:
            #         if self.strategy._is_bullish_engulfing(
            #             __import__('pandas').Series(prev_option_candle),
            #             __import__('pandas').Series(live_option_candle)
            #         ):
            #             await self.strategy._log_debug("EXIT-SIGNAL",
            #                 f"🚪 [P6-PATTERN] BULLISH ENGULFING: Pattern on {symbol}, exiting PE [Detection: {exit_detection_time}]")
            #             await self.strategy.exit_position("Invalidation: Option Bullish Engulfing")
            #             return
            
            # 🔥 PRIORITY 7: OPTION SUPERTREND
            if hasattr(self.strategy.data_manager, 'option_data_dfs'):
                option_df = self.strategy.data_manager.option_data_dfs.get(symbol)
                if option_df is not None and not option_df.empty and 'supertrend' in option_df.columns:
                    latest_option_row = option_df.iloc[-1]
                    option_st = latest_option_row.get('supertrend')
                    current_option_price = latest_option_row.get('close', 0)
                    
                    if pd.notna(option_st) and current_option_price and current_option_price < option_st:
                        await self.strategy._log_debug("EXIT-SIGNAL",
                            f"🚪 [P7-OPTION] OPTION SUPERTREND FLIP: Price below ST, exiting {symbol} [Detection: {exit_detection_time}]")
                        await self.strategy.exit_position("Option Supertrend Flip")
                        return
        
        except Exception as e:
            await self.strategy._log_debug("EXIT-CHECK-ERROR", f"Error checking exit signals: {e}")
    
    async def universal_validation_gauntlet(self, option, side, trigger):
        """V47.14 Universal Validation Gauntlet - OPTIMIZED with Parallel Execution
        Returns: (is_valid, momentum_data_dict)
        """
        # Determine if this is a reversal trade
        is_reversal = 'Reversal' in trigger or 'Flip' in trigger or 'Counter' in trigger
        
        # ⚡ PARALLEL OPTIMIZATION: Run momentum + ATM simultaneously (40-50% faster)
        try:
            # Step 1: Run momentum and ATM in parallel (both independent)
            results = await asyncio.gather(
                self._validate_momentum_conditions(option, side),
                self._is_atm_confirming(side, is_reversal=is_reversal),
                return_exceptions=True
            )
            
            # CRITICAL FIX: Check if any result is an exception
            if isinstance(results[0], Exception):
                await self.strategy._log_debug("Gauntlet", f"❌ Momentum validation raised exception: {results[0]}")
                return False, {}
            if isinstance(results[1], Exception):
                await self.strategy._log_debug("Gauntlet", f"❌ ATM confirmation raised exception: {results[1]}")
                return False, {}
            
            momentum_valid, momentum_data = results[0]
            atm_valid = results[1]
            momentum_passed = momentum_data.get('momentum_checks_passed', 0)
            
            # Step 2: Run candle check (depends on momentum_passed)
            candle_valid = await self._validate_candle_conditions(option, side, is_reversal, momentum_passed)
            
            # Check each result and log specific failures
            # 🔥 ATM CHECK DISABLED: Momentum + Candle checks are sufficient
            # ATM spread requirements were too restrictive and caused missed opportunities
            # if not atm_valid:
            #     await self.strategy._log_debug("Gauntlet", f"❌ ATM confirmation failed for {trigger}")
            #     return False, {}
            
            # 🔥 MOMENTUM CHECK DISABLED: Testing without momentum filters
            # Momentum checks were blocking valid entries
            # if not momentum_valid:
            #     await self.strategy._log_debug("Gauntlet", f"❌ Momentum validation failed for {trigger}")
            #     return False, {}
            
            if not candle_valid:
                await self.strategy._log_debug("Gauntlet", f"❌ Candle validation failed for {trigger}")
                return False, {}
            
            await self.strategy._log_debug("Gauntlet", f"✅ All validations passed (Momentum DISABLED, Candle ACTIVE) for {trigger}")
            return True, momentum_data
            
        except Exception as e:
            await self.strategy._log_debug("Gauntlet", f"❌ Validation error: {e}")
            return False, {}
    
    def _check_atr_squeeze(self, lookback_period=30, squeeze_range_candles=5):
        """V47.14 ATR Squeeze Detection - IMPROVED: More realistic squeeze conditions"""
        if len(self.strategy.data_manager.data_df) < lookback_period or 'atr' not in self.strategy.data_manager.data_df.columns:
            return {'in_squeeze': False}
        
        recent_atr = self.strategy.data_manager.data_df['atr'].tail(lookback_period)
        current_atr = recent_atr.iloc[-1]
        atr_min = recent_atr.min()
        atr_20th_percentile = recent_atr.quantile(0.20)  # Bottom 20% of ATR values
        
        # ✅ IMPROVED: Squeeze only if BOTH conditions met:
        # 1. Current ATR is at absolute minimum OR within 10% of minimum
        # 2. Current ATR is below 20th percentile (bottom 20% of values)
        is_at_minimum = current_atr <= (atr_min * 1.10)  # Within 10% of minimum
        is_low_volatility = current_atr <= atr_20th_percentile
        
        if is_at_minimum and is_low_volatility:
            if not self.atr_squeeze_detected:
                # Define breakout range from last 5 candles
                squeeze_candles = self.strategy.data_manager.data_df.tail(squeeze_range_candles)
                self.squeeze_range['high'] = squeeze_candles['high'].max()
                self.squeeze_range['low'] = squeeze_candles['low'].min()
                self.atr_squeeze_detected = True
                asyncio.create_task(self.strategy._log_debug("V47.14", 
                    f"🔥 ATR Squeeze Detected. ATR: {current_atr:.2f} (Min: {atr_min:.2f}, 20th%: {atr_20th_percentile:.2f}), Range: {self.squeeze_range}"))
            return {'in_squeeze': True, 'range_high': self.squeeze_range['high'], 'range_low': self.squeeze_range['low']}
        else:
            if self.atr_squeeze_detected:
                self.atr_squeeze_detected = False
                asyncio.create_task(self.strategy._log_debug("V47.14", 
                    f"ATR Squeeze ended. ATR: {current_atr:.2f} (above threshold)"))
            return {'in_squeeze': False}
    
    async def _is_atm_confirming(self, side, is_reversal=False):
        """
        V47.14 Enhanced ATM Confirmation with 3-minute lookback and 2.0% spread
        ⚡ OPTIMIZED: Uses pre-fetched ATM cache for instant price retrieval
        Checks if the ATM strike pair shows relative strength, adapting for time decay.
        Uses a shorter lookback for reversals to catch momentum shifts faster.
        """
        # ⚡ THROTTLE ATM LOGS: Log at most once per second to prevent spam
        import time
        current_time = time.time()
        if not hasattr(self, '_last_atm_log'):
            self._last_atm_log = {}
        
        # 🆕 DYNAMIC ATM SPREAD: Adjust based on market volatility
        index_volatility = self._calculate_index_volatility()
        
        # Calculate dynamic spread requirements (REDUCED - max 1.0%):
        # - Low volatility (stable): Require higher spread (1.0%)
        # - Medium volatility (normal): Standard spread (0.8%)
        # - High volatility (trending): Lower spread (0.6%)
        if index_volatility < 0.003:  # Low vol - choppy market
            base_spread = 1.0 if not is_reversal else 0.6
        elif index_volatility > 0.008:  # High vol - trending market
            base_spread = 0.6 if not is_reversal else 0.4
        else:  # Normal vol
            base_spread = 0.8 if not is_reversal else 0.5
        
        lookback_minutes = 1 if is_reversal else 4
        performance_spread = base_spread
        
        spot = self.strategy.data_manager.prices.get(self.strategy.index_symbol)
        if not spot:
            log_key = f"no_spot_{side}"
            if current_time - self._last_atm_log.get(log_key, 0) >= 1.0:
                await self.strategy._log_debug("ATM Check", f"❌ No index price available")
                self._last_atm_log[log_key] = current_time
            return False
        
        # ⚡ OPTIMIZATION: Try cache first (saves 0.06-0.10s per trade)
        atm_cache = self.strategy.data_manager.atm_cache
        cache_is_valid = (atm_cache.get("last_update", 0) > current_time - 3 and 
                         atm_cache.get("ce_symbol") and atm_cache.get("pe_symbol"))
        
        if cache_is_valid:
            # Use cached symbols (instant retrieval)
            ce_symbol = atm_cache["ce_symbol"]
            pe_symbol = atm_cache["pe_symbol"]
        else:
            # Fallback to calculation (backward compatibility)
            atm_strike = self.strategy.config.get('strike_step', 50) * round(spot / self.strategy.config.get('strike_step', 50))
            ce_opt = self.strategy.get_entry_option('CE', atm_strike)
            pe_opt = self.strategy.get_entry_option('PE', atm_strike)
            
            if not (ce_opt and pe_opt):
                log_key = f"no_atm_opts_{side}"
                if current_time - self._last_atm_log.get(log_key, 0) >= 1.0:
                    await self.strategy._log_debug("ATM Check", f"❌ ATM Options not found: CE={bool(ce_opt)}, PE={bool(pe_opt)}")
                    self._last_atm_log[log_key] = current_time
                return False
            
            ce_symbol = ce_opt['tradingsymbol']
            pe_symbol = pe_opt['tradingsymbol']
        
        ce_current_price = self.strategy.data_manager.prices.get(ce_symbol)
        pe_current_price = self.strategy.data_manager.prices.get(pe_symbol)
        ce_past_price = self._get_price_from_history(ce_symbol, lookback_minutes)
        pe_past_price = self._get_price_from_history(pe_symbol, lookback_minutes)
        
        if not all([ce_current_price, pe_current_price, ce_past_price, pe_past_price]):
            log_key = f"insufficient_data_{side}"
            if current_time - self._last_atm_log.get(log_key, 0) >= 1.0:
                await self.strategy._log_debug("ATM Check", f"❌ Insufficient price data for ATM confirmation")
                self._last_atm_log[log_key] = current_time
            return False  # Not enough data
        
        # Calculate percentage changes
        ce_pct_change = ((ce_current_price - ce_past_price) / ce_past_price) * 100 if ce_past_price > 0 else 0
        pe_pct_change = ((pe_current_price - pe_past_price) / pe_past_price) * 100 if pe_past_price > 0 else 0
        
        spread = ce_pct_change - pe_pct_change
        
        # Check spread requirements
        if side == 'CE':
            is_confirming = spread >= performance_spread
        elif side == 'PE':
            is_confirming = spread <= -performance_spread
        else:
            is_confirming = False
        
        # ⚡ THROTTLE: Only log ATM confirmation results once per second
        log_key = f"atm_result_{side}_{is_confirming}"
        if current_time - self._last_atm_log.get(log_key, 0) >= 1.0:
            await self.strategy._log_debug("ATM Check", 
                f"{'✅' if is_confirming else '❌'} {side} ATM Confirmation: "
                f"CE%={ce_pct_change:.2f}, PE%={pe_pct_change:.2f}, "
                f"Spread={spread:.2f}, Required={'≥' if side == 'CE' else '≤'}{performance_spread if side == 'CE' else -performance_spread}")
            self._last_atm_log[log_key] = current_time
        
        return is_confirming
    
    async def _validate_candle_conditions(self, option, side, is_reversal, momentum_passed=0):
        """V47.14 Enhanced Candle Validation with Predictive Override
        
        Args:
            momentum_passed: Number of predictive signals passed (0-3)
                           If >= 2, green candle requirement is WAIVED for early entry
        """
        # ⚡ THROTTLE CANDLE LOGS: Log at most once per second to prevent spam
        import time
        current_time = time.time()
        if not hasattr(self, '_last_candle_log'):
            self._last_candle_log = {}
        
        symbol = option['tradingsymbol']
        current_price = self.strategy.data_manager.prices.get(symbol)
        
        if not current_price:
            return False
        
        # Get option candle data
        option_candle = getattr(self.strategy.data_manager, 'option_candles', {}).get(symbol)
        previous_candle = getattr(self.strategy.data_manager, 'previous_option_candles', {}).get(symbol)
        
        # For reversal trades, we're more flexible with candle requirements
        if is_reversal:
            return True
        
        # Enhanced validation for trend continuation trades
        if option_candle:
            open_price = option_candle.get('open', current_price)
            high_price = option_candle.get('high', current_price)
            low_price = option_candle.get('low', current_price)
            
            # 🔮 PREDICTIVE OVERRIDE: If 2+ predictive signals pass, SKIP green candle requirement
            # This allows early entry BEFORE candle turns green (true predictive entry)
            if momentum_passed >= 2:
                log_key = f"predictive_override_{symbol}"
                if current_time - self._last_candle_log.get(log_key, 0) >= 1.0:
                    await self.strategy._log_debug("Candle Check", 
                        f"✅ {symbol}: PREDICTIVE OVERRIDE - Green candle waived ({momentum_passed}/3 signals strong)")
                    self._last_candle_log[log_key] = current_time
                # Still check minimum body to avoid dead flat candles
                if abs(current_price - open_price) < (open_price * 0.002):  # Less than 0.2% range
                    return False  # Too flat, no momentum
                return True  # Accept red/green if predictive signals strong
            
            # 1. Standard check: Must be a green candle (current price > open) if predictive weak
            if current_price <= open_price:
                log_key = f"not_green_{symbol}"
                if current_time - self._last_candle_log.get(log_key, 0) >= 1.0:
                    await self.strategy._log_debug("Candle Check", 
                        f"❌ {symbol}: Not green & predictive weak ({momentum_passed}/3). Price: {current_price}, Open: {open_price}")
                    self._last_candle_log[log_key] = current_time
                return False
            
            # 2. Minimum 0.1% body requirement (optimized for fast entries)
            # Reduced from 0.3% to allow earlier entries while still filtering flat candles
            minimum_body_price = open_price * 1.001
            if current_price < minimum_body_price:
                log_key = f"body_small_{symbol}"
                if current_time - self._last_candle_log.get(log_key, 0) >= 1.0:
                    await self.strategy._log_debug("Candle Check", f"❌ {symbol}: Body too small (Current: {current_price}, Min Required: {minimum_body_price:.2f})")
                    self._last_candle_log[log_key] = current_time
                return False
            
            # 3. Price must be in top 60% of candle range - ONLY for RED candles
            # GREEN candles (current_price > open_price) skip this check = faster entries
            # RED candles still need top 60% = protection against weak breakouts
            candle_range = high_price - low_price
            is_green_candle = current_price > open_price
            if candle_range > 0 and not is_green_candle:
                top_60_percent_threshold = low_price + (candle_range * 0.40)
                if current_price < top_60_percent_threshold:
                    log_key = f"not_top60_{symbol}"
                    if current_time - self._last_candle_log.get(log_key, 0) >= 1.0:
                        await self.strategy._log_debug("Candle Check", f"❌ {symbol}: RED candle not in top 60% of range (Price: {current_price}, Threshold: {top_60_percent_threshold:.2f})")
                        self._last_candle_log[log_key] = current_time
                    return False
        
        # 4. Simplified breakout validation - prioritize momentum over structure
        # ⚡ OPTIMIZED: Enter on body formation without waiting for previous high/close break
        # This allows much faster entries while momentum is building
        if previous_candle and option_candle:
            prev_high = previous_candle.get('high', 0)
            prev_close = previous_candle.get('close', 0)
            prev_open = previous_candle.get('open', 0)
            current_open = option_candle.get('open', 0)
            
            # Primary condition: Current candle forms body (price > open)
            forms_body = current_price > current_open
            
            # Optional bonus: Already breaking previous levels (ideal but not required)
            breaks_prev_high = current_price > prev_high
            breaks_prev_close = current_price > prev_close
            
            # Accept entry if body is forming (momentum confirmed)
            # No need to wait for previous high/close break
            if not forms_body:
                log_key = f"no_body_{symbol}"
                if current_time - self._last_candle_log.get(log_key, 0) >= 1.0:
                    await self.strategy._log_debug("Candle Check", f"❌ {symbol}: No body formation yet (Price: {current_price}, Open: {current_open:.2f})")
                    self._last_candle_log[log_key] = current_time
                return False
        
        log_key = f"candle_pass_{symbol}"
        if current_time - self._last_candle_log.get(log_key, 0) >= 1.0:
            await self.strategy._log_debug("Candle Check", f"✅ {symbol}: All candle validations passed")
            self._last_candle_log[log_key] = current_time
        return True
    
    async def _validate_momentum_conditions(self, option, side):
        """DUAL MOMENTUM SYSTEM: Predictive + Confirmatory (OR Logic)
        Returns: (is_valid, momentum_data_dict)
        
        Enters trade if EITHER condition met:
        - PREDICTIVE: 2/3 leading indicators (Order Flow, Divergence, Structure)
        - CONFIRMATORY: 2/3 lagging indicators (Price Rising, Acceleration, Volume Surge)
        """
        # ⚡ THROTTLE MOMENTUM LOGS: Log at most once per second to prevent spam
        import time
        current_time = time.time()
        if not hasattr(self, '_last_momentum_log'):
            self._last_momentum_log = {}
        
        symbol = option['tradingsymbol']
        
        # ========== PREDICTIVE CHECKS (Leading Indicators) ==========
        predictive_checks = []
        
        # 🔮 PREDICTIVE Check 1: Order Flow Imbalance (predicts next move)
        # Analyzes bid-ask pressure to forecast direction BEFORE price moves
        order_flow_bullish = await self._check_order_flow_imbalance(symbol)
        predictive_checks.append(("Order Flow Bullish", order_flow_bullish))
        
        # 🔮 PREDICTIVE Check 2: Tick Momentum Divergence (early reversal/continuation signal)
        # Detects when price slowing down OR speeding up - catches turns early
        has_positive_divergence = self._check_tick_momentum_divergence(symbol)
        predictive_checks.append(("Positive Divergence", has_positive_divergence))
        
        # 🔮 PREDICTIVE Check 3: Micro-Structure Break (institutional buying signal)
        # Identifies when big money is entering - leading indicator of sustained move
        has_structure_break = self._check_micro_structure_break(symbol)
        predictive_checks.append(("Structure Break", has_structure_break))
        
        predictive_passed = sum(1 for _, passed in predictive_checks if passed)
        predictive_valid = predictive_passed >= 2  # 2/3 required
        
        # ========== CONFIRMATORY CHECKS (Lagging Indicators) ==========
        confirmatory_checks = []
        
        # ✅ CONFIRMATORY Check 1: Price Rising (last 4 ticks)
        # Confirms price is already moving in right direction
        price_rising = self._is_price_actively_rising(symbol, ticks=4)
        confirmatory_checks.append(("Price Rising (4 ticks)", price_rising))
        
        # ✅ CONFIRMATORY Check 2: Acceleration (velocity increasing)
        # Confirms momentum is building (30% speed increase)
        is_accelerating = self._is_accelerating(symbol, lookback_ticks=20, acceleration_factor=1.3)
        confirmatory_checks.append(("Accelerating (1.3x)", is_accelerating))
        
        # ✅ CONFIRMATORY Check 3: Volume Surge (recent > previous)
        # Confirms increased trading activity
        volume_surge = self._check_volume_surge(symbol)
        confirmatory_checks.append(("Volume Surge", volume_surge))
        
        confirmatory_passed = sum(1 for _, passed in confirmatory_checks if passed)
        confirmatory_valid = confirmatory_passed >= 2  # 2/3 required
        
        # ✅ FIXED: Removed broken acceleration requirement that was blocking early entries
        # ❌ BUG WAS: Block entry if acceleration missing - rejected entries at momentum START
        # ✅ FIX: Use acceleration as bonus signal if present, but don't require it
        # Acceleration takes time to build (20+ ticks) - was rejecting early entries
        
        # ✅ FIXED: Removed backwards order flow logic that was BLOCKING bullish signals
        # ❌ BUG WAS: Block entry if Order Flow Bullish (backwards logic!)
        # ✅ FIX: Order flow should SUPPORT bullish entries, not block them
        # If order flow is bullish, it CONFIRMS the entry
        
        # ========== DUAL VALIDATION: OR LOGIC ==========
        # Enter if EITHER predictive OR confirmatory passes
        final_valid = predictive_valid or confirmatory_valid
        
        # Determine which system triggered entry
        trigger_system = ""
        if predictive_valid and confirmatory_valid:
            trigger_system = "BOTH (Predictive + Confirmatory)"
        elif predictive_valid:
            trigger_system = "PREDICTIVE"
        elif confirmatory_valid:
            trigger_system = "CONFIRMATORY"
        else:
            trigger_system = "NONE"
        
        # Prepare momentum data for database logging
        # Store BOTH Predictive AND Confirmatory checks for comprehensive analysis
        
        # ✅ FIX 3: Calculate entry_velocity for ALL entry types (not just Price Observer)
        # This enables velocity decay exit to work for Trend Continuation + NO-WICK entries
        entry_velocity = 0.0
        history = self.strategy.data_manager.price_history.get(symbol, [])
        if len(history) >= 2:
            # Calculate velocity as price change per tick (same logic as Price Observer)
            recent_prices = [p for ts, p in history[-6:]]  # Last 6 ticks
            if len(recent_prices) >= 2:
                import numpy as np
                velocity = np.diff(recent_prices)  # Price changes between ticks
                entry_velocity = float(velocity[-1]) if len(velocity) > 0 else 0.0
        
        momentum_data = {
            # CONFIRMATORY checks (lagging indicators - confirm momentum already happening)
            'momentum_price_rising': int(price_rising),           # Confirmatory: Price Rising (4 ticks)
            'momentum_accelerating': int(is_accelerating),        # Confirmatory: Accelerating (1.3x)
            'momentum_volume_surge': int(volume_surge),           # Confirmatory: Volume Surge
            'momentum_index_sync': int(confirmatory_valid),       # Confirmatory system passed (2/3)
            'momentum_checks_passed': confirmatory_passed,        # Confirmatory checks passed (0-3)
            # PREDICTIVE checks (leading indicators - predict momentum before it happens)
            'predictive_order_flow': int(order_flow_bullish),     # Predictive: Order Flow Bullish
            'predictive_divergence': int(has_positive_divergence),# Predictive: Positive Divergence
            'predictive_structure': int(has_structure_break),     # Predictive: Structure Break
            'predictive_checks_passed': predictive_passed,        # Predictive checks passed (0-3)
            'trigger_system': trigger_system,                     # Which system triggered entry
            'entry_velocity': entry_velocity                      # ✅ FIX 3: Velocity for ALL entries
        }
        
        # ⚡ THROTTLE: Only log momentum results once per second per symbol
        log_key = f"momentum_{symbol}_{final_valid}"
        if current_time - self._last_momentum_log.get(log_key, 0) >= 1.0:
            await self.strategy._log_debug("Dual Momentum", 
                f"{'✅' if final_valid else '❌'} {symbol} [{trigger_system}] | "
                f"Predictive: {predictive_passed}/3 ({', '.join([f'{n}={r}' for n, r in predictive_checks])}) | "
                f"Confirmatory: {confirmatory_passed}/3 ({', '.join([f'{n}={r}' for n, r in confirmatory_checks])})")
            self._last_momentum_log[log_key] = current_time
        
        return (final_valid, momentum_data)
    
    def _check_index_momentum_sync(self, option_symbol):
        """Check if index and option momentum are synchronized."""
        # Get index price trend
        index_history = self.strategy.data_manager.price_history.get(self.strategy.index_symbol, [])
        option_history = self.strategy.data_manager.price_history.get(option_symbol, [])
        
        if len(index_history) < 3 or len(option_history) < 3:
            return True  # Default to true if insufficient data
        
        # Check last 3 ticks trend for both
        index_prices = [p[1] for p in index_history[-3:]]
        option_prices = [p[1] for p in option_history[-3:]]
        
        index_rising = index_prices[-1] > index_prices[0]
        option_rising = option_prices[-1] > option_prices[0]
        
        # For CE trades, both should be rising; for PE trades, we're more flexible
        return index_rising and option_rising
    
    async def _check_order_flow_imbalance(self, symbol):
        """
        🔮 PREDICTIVE: Order Flow Imbalance Detection
        
        Predicts price movement by analyzing bid-ask dynamics BEFORE price changes.
        
        How it works:
        - Fetches live order book (market depth)
        - Compares buy-side liquidity vs sell-side liquidity
        - Imbalance towards buyers = price will rise (even if not moving yet)
        - This is what institutional traders watch
        
        Returns: True if buyers dominating order book (bullish imbalance)
        """
        try:
            # Fetch real-time market depth
            from .broker_factory import broker as kite
            full_symbol = f"{self.strategy.exchange}:{symbol}"
            quote = await kite.quote([full_symbol])
            
            if not quote or full_symbol not in quote:
                return False
            
            depth = quote[full_symbol].get('depth', {})
            buy_orders = depth.get('buy', [])
            sell_orders = depth.get('sell', [])
            
            if not buy_orders or not sell_orders:
                return False
            
            # Calculate buy-side pressure (top 5 levels)
            buy_volume = sum(level.get('quantity', 0) for level in buy_orders[:5])
            buy_value = sum(level.get('quantity', 0) * level.get('price', 0) for level in buy_orders[:5])
            
            # Calculate sell-side pressure (top 5 levels)
            sell_volume = sum(level.get('quantity', 0) for level in sell_orders[:5])
            sell_value = sum(level.get('quantity', 0) * level.get('price', 0) for level in sell_orders[:5])
            
            # Order flow imbalance ratio
            if sell_volume == 0:
                return True
            
            volume_ratio = buy_volume / sell_volume
            value_ratio = buy_value / sell_value if sell_value > 0 else 1.0
            
            # Bullish imbalance: Buyers > 1.2x sellers (20% more buying pressure)
            # This predicts upward price movement before it happens
            return volume_ratio > 1.2 and value_ratio > 1.15
            
        except Exception:
            return False  # If depth unavailable, skip this check
    
    def _check_tick_momentum_divergence(self, symbol):
        """
        🔮 PREDICTIVE: Tick Momentum Divergence (catches turns early)
        
        Identifies momentum shifts BEFORE they show up in price.
        
        How it works:
        - Compares recent tick velocity vs older tick velocity
        - If velocity INCREASING while price flat = accumulation = breakout coming
        - If velocity DECREASING while price rising = exhaustion = reversal coming
        
        This catches the shift from accumulation to breakout phase.
        
        Returns: True if tick momentum strengthening (positive divergence)
        """
        history = self.strategy.data_manager.price_history.get(symbol, [])
        if len(history) < 20:
            return False
        
        prices = [p[1] for p in history[-20:]]
        
        # Split into two segments
        old_prices = prices[:10]
        recent_prices = prices[10:]
        
        # Calculate velocity (price change per tick) for each segment
        old_velocity = np.mean(np.diff(old_prices))
        recent_velocity = np.mean(np.diff(recent_prices))
        
        # Positive divergence: Recent velocity > old velocity (momentum building)
        # This means ticks are getting stronger, predicting breakout
        if old_velocity <= 0:
            return recent_velocity > 0  # Turning bullish
        
        # Velocity increasing = momentum strengthening = continuation likely
        return recent_velocity > (old_velocity * 1.3)  # 30% velocity increase
    
    def _check_micro_structure_break(self, symbol):
        """
        🔮 PREDICTIVE: Micro-Structure Break (institutional buying)
        
        Detects when big money (institutions) are entering a position.
        
        How it works:
        - Looks for sudden absorption of resistance (breaking through sell walls)
        - Identifies "spring" pattern - quick dip then rapid recovery above previous high
        - These patterns precede major moves because institutions are accumulating
        
        This is the #1 signal that smart money is buying.
        
        Returns: True if institutional buying pattern detected
        """
        history = self.strategy.data_manager.price_history.get(symbol, [])
        if len(history) < 15:
            return False
        
        prices = [p[1] for p in history[-15:]]
        
        # Identify if there was a recent dip followed by strong recovery
        # This is "spring" pattern - institutions shaking out weak hands
        
        # Find recent low point (within last 8 ticks)
        recent_segment = prices[-8:]
        min_price = min(recent_segment)
        min_idx = recent_segment.index(min_price)
        
        # Check if price recovered strongly after the dip
        if min_idx < len(recent_segment) - 3:  # Dip must have occurred (not at end)
            prices_after_dip = recent_segment[min_idx:]
            current_price = prices[-1]
            
            # Structure break = price now higher than BEFORE the dip
            pre_dip_high = max(prices[:-8]) if len(prices) > 8 else max(prices[:8])
            
            # Strong recovery above previous resistance = institutional buying
            if current_price > pre_dip_high and current_price > min_price * 1.015:
                return True  # Broke above resistance after dip = bullish
        
        # Alternative pattern: Consistent higher lows (accumulation)
        lows = [min(prices[i:i+3]) for i in range(0, len(prices)-3, 3)]
        if len(lows) >= 3:
            # Each low higher than previous = accumulation phase = breakout imminent
            return lows[-1] > lows[-2] > lows[-3]
        
        return False
    
    def _calculate_index_volatility(self):
        """🆕 Calculate current index volatility for dynamic ATM spread"""
        history = self.strategy.data_manager.price_history.get(
            self.strategy.index_symbol, [])
        
        if len(history) < 30:
            return 0.005  # Default medium volatility
        
        recent_prices = [p[1] for p in history[-30:]]
        returns = np.diff(recent_prices) / np.array(recent_prices[:-1])
        volatility = np.std(returns)
        
        return volatility
    
    def _is_accelerating(self, symbol, lookback_ticks=20, acceleration_factor=1.3):
        """Check if option price is accelerating"""
        history = self.strategy.data_manager.price_history.get(symbol, [])
        if len(history) < lookback_ticks: 
            return False
        
        # Get prices only from (timestamp, price) tuples
        prices = [p for ts, p in history[-lookback_ticks:]]
        diffs = np.diff(prices)
        
        if len(diffs) < 2: 
            return False

        current_velocity = diffs[-1]
        avg_velocity = np.mean(diffs[:-1])

        if current_velocity <= 0: 
            return False
            
        # Reduced acceleration factor from 1.5 to 1.3 (30% vs 50% increase required)
        if avg_velocity > 0 and current_velocity > avg_velocity * acceleration_factor:
            return True
            
        return False
    
    def _check_volume_surge(self, symbol):
        """Check if trading volume is surging (confirmatory indicator)
        
        Compares recent volume to previous volume to detect increased activity.
        Volume surge often accompanies strong momentum moves.
        
        Returns:
            bool: True if recent volume > previous volume × 1.5
        """
        history = self.strategy.data_manager.price_history.get(symbol, [])
        if len(history) < 20:
            return False
        
        # Split into two segments: recent (last 10 ticks) vs previous (10 ticks before)
        recent_ticks = history[-10:]
        previous_ticks = history[-20:-10]
        
        # Count ticks as proxy for volume (more ticks = more activity)
        recent_volume = len(recent_ticks)
        previous_volume = len(previous_ticks)
        
        if previous_volume == 0:
            return recent_volume > 0
        
        # Volume surge: Recent activity 1.5x higher than previous
        volume_ratio = recent_volume / previous_volume
        return volume_ratio >= 1.5
    
    # --- NEW: Helper Methods for Enhanced Validation ---
    async def _validate_sustained_breakout_with_nowick_detection(self, symbol, option_candle, required_ticks=3):
        """
        ⚡ OPTIMIZED SUSTAINED BREAKOUT CHECK with Dynamic NO_WICK Detection
        
        This enhanced version checks for NO_WICK upgrade during tick collection,
        allowing early exit if candle quality improves to perfect (no lower wick).
        
        Args:
            symbol: Option symbol to check
            option_candle: Current candle data
            required_ticks: Number of consecutive ticks required (default 3)
        
        Returns:
            dict: {
                'valid': bool,
                'upgraded_to_nowick': bool,
                'upgrade_tick': int (tick number where upgrade happened)
            }
        """
        history = self.strategy.data_manager.price_history.get(symbol, [])
        
        if not option_candle or len(history) < required_ticks:
            return {'valid': False, 'upgraded_to_nowick': False}
        
        open_price = option_candle.get('open', 0)
        candle_low = option_candle.get('low', 0)
        candle_high = option_candle.get('high', 0)
        
        if open_price <= 0:
            return {'valid': False, 'upgraded_to_nowick': False}
        
        # Get last N tick prices
        recent_prices = [p[1] for p in history[-required_ticks:]]
        
        # Check if all ticks are above open (sustained breakout)
        all_above_open = all(price > open_price for price in recent_prices)
        
        if not all_above_open:
            return {'valid': False, 'upgraded_to_nowick': False}
        
        # ⚡ DYNAMIC NO_WICK DETECTION: Check if candle has become perfect during validation
        # This allows upgrading TREND → NO_WICK mid-validation for better performance
        current_price = self.strategy.data_manager.prices.get(symbol, 0)
        is_green = current_price > open_price
        has_no_lower_wick = abs(open_price - candle_low) < (open_price * 0.001)  # Within 0.1%
        
        if has_no_lower_wick and is_green and candle_high > open_price:
            # Candle upgraded to NO_WICK quality!
            return {
                'valid': True,
                'upgraded_to_nowick': True,
                'upgrade_tick': required_ticks
            }
        
        # Standard TREND validation passed
        return {'valid': True, 'upgraded_to_nowick': False}
    
    def _validate_sustained_breakout(self, symbol, required_ticks=4):
        """
        🆕 SUSTAINED BREAKOUT CHECK: Price must stay above open for multiple consecutive ticks.
        This prevents hasty early entries that happen in the first 1-2 seconds of candle formation.
        
        Args:
            symbol: Option symbol to check
            required_ticks: Number of consecutive ticks required (default 4 = ~2-3 seconds)
        
        Returns:
            bool: True if last N ticks are all above candle open, False otherwise
        """
        history = self.strategy.data_manager.price_history.get(symbol, [])
        option_candle = self.strategy.data_manager.option_candles.get(symbol)
        
        if not option_candle or len(history) < required_ticks:
            return False
        
        open_price = option_candle.get('open', 0)
        if open_price <= 0:
            return False
        
        # Get last N tick prices
        recent_prices = [p[1] for p in history[-required_ticks:]]
        
        # All recent ticks must be above open (sustained breakout)
        all_above_open = all(price > open_price for price in recent_prices)
        
        # ⚡ THROTTLE LOGS: Only log once per second per symbol
        import time
        current_time = time.time()
        if not hasattr(self, '_last_sustained_log'):
            self._last_sustained_log = {}
        
        log_key = f"sustained_{symbol}_{all_above_open}"
        if current_time - self._last_sustained_log.get(log_key, 0) >= 1.0:
            if all_above_open:
                asyncio.create_task(self.strategy._log_debug("Sustained Breakout", 
                    f"✅ {symbol}: {required_ticks} ticks above open (₹{open_price:.2f}) - {[f'₹{p:.2f}' for p in recent_prices]}"))
            else:
                below_count = sum(1 for p in recent_prices if p <= open_price)
                asyncio.create_task(self.strategy._log_debug("Sustained Breakout", 
                    f"❌ {symbol}: Only {required_ticks - below_count}/{required_ticks} ticks above open (₹{open_price:.2f})"))
            self._last_sustained_log[log_key] = current_time
        
        return all_above_open
    
    def _calculate_required_ticks(self, momentum_passed, volatility=None):
        """
        🚀 MOMENTUM-WEIGHTED TICKS: Adaptive tick requirement based on momentum strength
        
        Reduces 4-tick requirement to 2-3 ticks when momentum signals are strong.
        This saves ~1.0 second per trade while maintaining quality.
        
        Args:
            momentum_passed: Number of momentum checks passed (0-3)
            volatility: Optional volatility factor (not used currently, for future enhancement)
        
        Returns:
            int: Required number of ticks (2, 3, or 4)
        """
        if momentum_passed >= 2:
            # Strong momentum (2+ signals): Only need 2 ticks
            return 2
        elif momentum_passed == 1:
            # Moderate momentum (1 signal): Need 3 ticks for balance
            return 3
        else:
            # Weak momentum (0 signals): Need full 4 ticks for safety
            return 4
    
    def _is_price_actively_rising(self, symbol, ticks=3):
        """Checks if the price is actively rising with allowance for brief pauses."""
        history = self.strategy.data_manager.price_history.get(symbol, [])
        if len(history) < ticks:
            return False
        
        recent_prices = [p[1] for p in history[-ticks:]]
        
        # Count how many ticks show upward movement
        rising_count = 0
        equal_count = 0
        for i in range(1, len(recent_prices)):
            if recent_prices[i] > recent_prices[i-1]:
                rising_count += 1
            elif recent_prices[i] == recent_prices[i-1]:
                equal_count += 1
        
        # Allow 1 equal tick but require majority upward movement
        # For 2 ticks: need 1 rising (50%+)
        # For 3 ticks: need 2 rising (66%+)
        total_transitions = len(recent_prices) - 1
        required_rising = (total_transitions + 1) // 2  # At least half, rounded up
        
        return rising_count >= required_rising and equal_count <= 1
    
    def _get_price_from_history(self, symbol, lookback_minutes):
        """Get price from history based on lookback time."""
        history = self.strategy.data_manager.price_history.get(symbol, [])
        if not history:
            return None
        
        lookback_time = datetime.now().timestamp() - (lookback_minutes * 60)
        
        # Iterate backwards to find the first tick at or before the lookback time
        for tick_time, price in reversed(history):
            if tick_time <= lookback_time:
                return price
        
        # If no tick is old enough, return the oldest available tick
        return history[0][1] if history else None
    
    def _validate_price_momentum_observer(self, symbol):
        """
        📊 PRICE MOMENTUM OBSERVER: Pure price-driven entry validation
        
        Watches tick-by-tick price movement and enters based on velocity & acceleration,
        NOT candle timing. This captures moves by "observing the price" in real-time.
        
        Entry Criteria:
        1. Minimum ticks collected (8-10 ticks = ~8-10 seconds of data)
        2. Current velocity > threshold (₹0.05+ per tick minimum)
        3. Acceleration: current velocity > average velocity × 1.5
        4. Directional consistency: 80%+ of recent ticks are green
        
        Returns:
            dict: {
                'valid': bool,
                'entry_velocity': float,  # Velocity at entry (for exit tracking)
                'avg_velocity': float,
                'directional_pct': float,
                'tick_count': int
            }
        """
        if not self.strategy.params.get('price_observer_enabled', False):
            return {'valid': False}
        
        history = self.strategy.data_manager.price_history.get(symbol, [])
        min_ticks = self.strategy.params.get('price_observer_min_ticks', 8)
        
        if len(history) < min_ticks:
            return {'valid': False, 'reason': f'Insufficient ticks ({len(history)}/{min_ticks})'}
        
        # Extract prices from (timestamp, price) tuples
        lookback = self.strategy.params.get('price_observer_lookback_ticks', 6)
        recent_prices = [p for ts, p in history[-lookback:]]
        all_prices = [p for ts, p in history[-min_ticks:]]
        
        # 1. Calculate velocity (price change per tick)
        velocity = np.diff(all_prices)  # Price changes between consecutive ticks
        current_velocity = velocity[-1] if len(velocity) > 0 else 0
        avg_velocity = np.mean(velocity[:-1]) if len(velocity) > 1 else 0
        
        velocity_threshold = self.strategy.params.get('price_observer_velocity_threshold', 0.05)
        if current_velocity < velocity_threshold:
            return {
                'valid': False,
                'reason': f'Velocity too low ({current_velocity:.3f} < {velocity_threshold})'
            }
        
        # 2. Check acceleration (current velocity vs average)
        accel_factor = self.strategy.params.get('price_observer_accel_factor', 1.5)
        if avg_velocity > 0:
            if current_velocity < avg_velocity * accel_factor:
                return {
                    'valid': False,
                    'reason': f'Not accelerating ({current_velocity:.3f} < {avg_velocity:.3f} × {accel_factor})'
                }
        
        # 3. Check directional consistency (80% of ticks should be green)
        green_ticks = sum(1 for i in range(1, len(recent_prices)) if recent_prices[i] > recent_prices[i-1])
        total_transitions = len(recent_prices) - 1
        directional_pct = green_ticks / total_transitions if total_transitions > 0 else 0
        
        required_pct = self.strategy.params.get('price_observer_directional_pct', 0.80)
        if directional_pct < required_pct:
            return {
                'valid': False,
                'reason': f'Directional inconsistency ({green_ticks}/{total_transitions} = {directional_pct:.1%} < {required_pct:.0%})'
            }
        
        # All checks passed - this is a valid momentum entry
        return {
            'valid': True,
            'entry_velocity': current_velocity,
            'avg_velocity': avg_velocity,
            'directional_pct': directional_pct,
            'tick_count': len(history),
            'green_ticks': f'{green_ticks}/{total_transitions}'
        }
    
    async def _enhanced_validate_entry_conditions_with_candle_color(self, option, side, log=False, momentum_passed_override=None):
        """
        Enhanced validation that returns both validation result and pricing data.
        Used specifically for Red-Green Continuation Logic.
        
        Args:
            momentum_passed_override: If set, skip momentum validation and use this value (avoids duplicate checks)
        """
        symbol = option['tradingsymbol']
        current_price = self.strategy.data_manager.prices.get(symbol)
        
        if not current_price:
            return False, {}
        
        # � PRICE MOMENTUM OBSERVER: Priority 1 - Pure price-driven entry
        price_observer_result = self._validate_price_momentum_observer(symbol)
        if price_observer_result.get('valid', False):
            # 🛡️ SMART REVERSAL CHECK: Only reject SIGNIFICANT declines (>0.8%), allow minor pullbacks
            history = self.strategy.data_manager.price_history.get(symbol, [])
            if len(history) >= 3:
                recent_3_prices = [p for ts, p in history[-3:]]
                # Check if declining AND decline is significant
                if recent_3_prices[-1] < recent_3_prices[-2] and recent_3_prices[-2] < recent_3_prices[-3]:
                    # Calculate total decline percentage
                    decline_pct = ((recent_3_prices[-1] - recent_3_prices[-3]) / recent_3_prices[-3]) * 100
                    # Only reject if decline > 0.8% (allows minor noise, rejects true reversals)
                    if decline_pct < -0.8:
                        if log:
                            asyncio.create_task(self.strategy._log_debug("🔴 ENTRY REJECTED", 
                                f"❌ PRICE OBSERVER: {symbol} declining {decline_pct:.2f}% ({recent_3_prices[-3]:.2f} → {recent_3_prices[-2]:.2f} → {recent_3_prices[-1]:.2f}) - significant reversal"))
                        return False, {}
                    # else: Minor decline (<0.8%), allow entry - could be pullback in trend
            
            # Price momentum observer passed AND not falling - accept entry
            if log:
                asyncio.create_task(self.strategy._log_debug("📊 PRICE OBSERVER ENTRY", 
                    f"⚡ {symbol}: Velocity={price_observer_result['entry_velocity']:.3f}, "
                    f"Accel={price_observer_result['avg_velocity']:.3f}×{self.strategy.params.get('price_observer_accel_factor', 1.5)}, "
                    f"Direction={price_observer_result['directional_pct']:.0%} ({price_observer_result['green_ticks']})"))
            
            # Get previous candle for validation data
            prev_candle = self.strategy.data_manager.previous_option_candles.get(symbol)
            prev_close = prev_candle.get('close', current_price) if prev_candle else current_price
            
            validation_data = {
                'prev_close': prev_close,
                'current_price': current_price,
                'symbol': symbol,
                'momentum_passed': 3,  # Mark as full momentum for logging
                'momentum_data': {
                    'price_observer': price_observer_result,
                    'entry_velocity': price_observer_result['entry_velocity']
                }
            }
            return True, validation_data
        elif self.strategy.params.get('price_observer_enabled', False) and log:
            # Log why price observer rejected
            reason = price_observer_result.get('reason', 'Unknown')
            asyncio.create_task(self.strategy._log_debug("📊 PRICE OBSERVER", 
                f"❌ {symbol}: {reason}"))
        
        # �🚀 NO-WICK BYPASS CHECK: Skip 4-tick validation if no-wick entry conditions met
        no_wick_bypass = False
        option_candle = self.strategy.data_manager.option_candles.get(symbol)
        
        if option_candle and self.strategy.params.get('enable_no_wick_entry', True):
            candle_open = option_candle.get('open', 0)
            candle_low = option_candle.get('low', 0)
            candle_high = option_candle.get('high', 0)
            candle_close = option_candle.get('close', 0)
            candle_start_time = option_candle.get('candle_start_time', 0)
            
            if candle_open > 0 and candle_low > 0:
                # 🛡️ SAFETY: Require minimum ticks and candle age to avoid false positives
                price_history = self.strategy.data_manager.price_history.get(symbol, [])
                tick_count = len(price_history)
                
                import time
                candle_age = time.time() - candle_start_time if candle_start_time > 0 else 0
                
                if tick_count >= 5 and candle_age >= 8.0:  # 🔧 LIVE MARKET: 8s minimum for stability
                    is_green = current_price > candle_open or candle_close > candle_open
                    has_no_lower_wick = abs(candle_open - candle_low) < (candle_open * 0.001)  # Within 0.1%
                    
                    if has_no_lower_wick and is_green and candle_high > candle_open:
                        # ✅ CHECK 1: GREEN CANDLE (already validated above)
                        
                        # ✅ CHECK 2: VELOCITY - Must be rising at least ₹0.05/s
                        velocity = self.strategy.calculate_price_velocity(symbol, lookback_seconds=1.5)
                        if velocity < 0.05:
                            if log:
                                asyncio.create_task(self.strategy._log_debug("🔴 NO-WICK REJECTED", 
                                    f"❌ {symbol}: Low velocity (₹{velocity:.3f}/s < ₹0.05/s)"))
                            # Don't set no_wick_bypass
                            pass
                        # ✅ CHECK 3: BREAKOUT - Must be above previous candle high
                        elif True:  # Execute breakout check
                            prev_candle = self.strategy.data_manager.previous_option_candles.get(symbol)
                            if prev_candle:
                                prev_high = prev_candle.get('high', 0)
                                if prev_high > 0 and current_price < prev_high * 0.998:  # Allow 0.2% below
                                    if log:
                                        asyncio.create_task(self.strategy._log_debug("🔴 NO-WICK REJECTED", 
                                            f"❌ {symbol}: No breakout (LTP: ₹{current_price:.2f} < Prev High: ₹{prev_high:.2f})"))
                                    # Don't set no_wick_bypass
                                    pass
                                else:
                                    # All checks passed - validate velocity/breakout with reversal check
                                    need_reversal_check = True
                            else:
                                # No previous candle data, skip breakout check but do reversal check
                                need_reversal_check = True
                            
                            if need_reversal_check:
                                # 🛡️ SMART REVERSAL CHECK: Only reject SIGNIFICANT declines (>0.8%)
                                history = self.strategy.data_manager.price_history.get(symbol, [])
                                if len(history) >= 3:
                                    recent_3_prices = [p for ts, p in history[-3:]]
                                    # Check if declining AND decline is significant
                                    if recent_3_prices[-1] < recent_3_prices[-2] and recent_3_prices[-2] < recent_3_prices[-3]:
                                        decline_pct = ((recent_3_prices[-1] - recent_3_prices[-3]) / recent_3_prices[-3]) * 100
                                        if decline_pct < -0.8:
                                            if log:
                                                asyncio.create_task(self.strategy._log_debug("🔴 ENTRY REJECTED", 
                                                    f"❌ NO-WICK: {symbol} declining {decline_pct:.2f}% ({recent_3_prices[-3]:.2f} → {recent_3_prices[-2]:.2f} → {recent_3_prices[-1]:.2f}) - significant reversal"))
                                            # Don't set no_wick_bypass, let normal validation handle it
                                        else:
                                            # Minor decline (<0.8%), safe to use NO-WICK bypass
                                            no_wick_bypass = True
                                            if log:
                                                asyncio.create_task(self.strategy._log_debug("🚀 NO-WICK BYPASS", 
                                                    f"⚡ {symbol} - GREEN + Velocity ₹{velocity:.3f}/s + Breakout + minor pullback {decline_pct:.2f}% OK (Ticks: {tick_count}, Age: {candle_age:.1f}s)"))
                                    else:
                                        # Price not falling, safe to use NO-WICK bypass
                                        no_wick_bypass = True
                                        if log:
                                            asyncio.create_task(self.strategy._log_debug("🚀 NO-WICK BYPASS", 
                                                f"⚡ {symbol} - GREEN + Velocity ₹{velocity:.3f}/s + Breakout confirmed (Ticks: {tick_count}, Age: {candle_age:.1f}s)"))
                                else:
                                    # Not enough price history, use NO-WICK anyway (first few ticks of candle)
                                    no_wick_bypass = True
                                    if log:
                                        asyncio.create_task(self.strategy._log_debug("🚀 NO-WICK BYPASS", 
                                            f"⚡ {symbol} - GREEN + Velocity ₹{velocity:.3f}/s + No lower wick (Ticks: {tick_count}, Age: {candle_age:.1f}s)"))
        
        # ⚡ OPTIMIZATION PHASE 2: Start ATM check BEFORE tick collection (runs in parallel)
        # ATM is independent and can start immediately - saves 0.3s per trade
        # 🔥 DISABLED: atm_task = asyncio.create_task(self._is_atm_confirming(side, is_reversal=False))
        atm_task = None  # ATM check disabled
        
        # ⚡ OPTIMIZATION PHASE 3: Enhanced tick validation with dynamic NO_WICK detection
        # Check for NO_WICK upgrade during tick collection - can save 0.5-1.0s for qualifying trades
        if not no_wick_bypass:
            # Start tick collection with dynamic NO_WICK detection
            tick_validation_result = await self._validate_sustained_breakout_with_nowick_detection(
                symbol, option_candle, required_ticks=3
            )
            
            if not tick_validation_result['valid']:
                return False, {}
            
            # 🆕 SMART REVERSAL CHECK: Verify price isn't in SIGNIFICANT decline NOW
            # Allows minor pullbacks (<0.8%), rejects true reversals (>0.8% decline)
            history = self.strategy.data_manager.price_history.get(symbol, [])
            if len(history) >= 3:
                recent_3_prices = [p for ts, p in history[-3:]]
                # Check if declining AND decline is significant
                if recent_3_prices[-1] < recent_3_prices[-2] and recent_3_prices[-2] < recent_3_prices[-3]:
                    decline_pct = ((recent_3_prices[-1] - recent_3_prices[-3]) / recent_3_prices[-3]) * 100
                    # Only reject if decline > 0.8% (true reversal)
                    if decline_pct < -0.8:
                        if log:
                            asyncio.create_task(self.strategy._log_debug("🔴 ENTRY REJECTED", 
                                f"❌ TREND: {symbol} declining {decline_pct:.2f}% ({recent_3_prices[-3]:.2f} → {recent_3_prices[-2]:.2f} → {recent_3_prices[-1]:.2f}) - significant reversal"))
                        return False, {}
                    # else: Minor decline (<0.8%), allow entry - likely just pullback
            
            # Check if trade was upgraded to NO_WICK during tick collection
            if tick_validation_result.get('upgraded_to_nowick', False):
                no_wick_bypass = True
                if log:
                    asyncio.create_task(self.strategy._log_debug("🚀 DYNAMIC NO-WICK UPGRADE", 
                        f"⚡ {symbol} upgraded to NO_WICK at tick {tick_validation_result.get('upgrade_tick', 0)}"))
        
        # Get previous candle data for ₹0.10 premium calculation
        prev_candle = self.strategy.data_manager.previous_option_candles.get(symbol)
        prev_close = prev_candle.get('close', current_price) if prev_candle else current_price
        
        # ⚡ PARALLEL VALIDATION: Start momentum validation while ATM completes
        momentum_task = asyncio.create_task(self._validate_momentum_conditions(option, side))
        
        # Wait for parallel tasks to complete
        momentum_valid, momentum_data = await momentum_task
        # 🔥 ATM DISABLED: atm_valid = await atm_task
        atm_valid = False  # ATM check disabled
        momentum_passed = momentum_data.get('momentum_checks_passed', 0)
        
        # Now check candle with momentum count for override (depends on momentum_passed)
        candle_valid = await self._validate_candle_conditions(option, side, is_reversal=False, momentum_passed=momentum_passed)
        
        # 🔥 MOMENTUM & ATM COMPLETELY DISABLED: Only candle validation active
        validation_result = candle_valid  # Only candle check
        
        validation_data = {
            'prev_close': prev_close,
            'current_price': current_price,
            'symbol': symbol,
            'momentum_passed': momentum_passed,  # Track count for logging
            'momentum_data': momentum_data  # Full momentum data with all signal flags
        }
        
        if log:
            asyncio.create_task(self.strategy._log_debug("Enhanced Validation", 
                f"{'✅' if validation_result else '❌'} {side} validation for {symbol}: "
                f"Current={current_price:.2f}, PrevClose={prev_close:.2f}"))
        
        return validation_result, validation_data


class V47TrendContinuationEngine:
    """V47.14 Priority 3: Trend Continuation Engine - MODIFIED TO USE OPTION MOMENTUM TRIGGER"""
    
    def __init__(self, strategy):
        # self.strategy is an instance of the main Strategy class
        self.strategy = strategy
    
    async def check_entry(self, log=False):
        """
        Checks for trend continuation signals if the overall trend is established
        AND the target ATM Option is showing initial momentum (a rising price).
        """
        dm = self.strategy.data_manager # Alias for cleaner code
        coord = self.strategy.v47_coordinator # ALIAS FOR V47 COORDINATOR (The method holder)
        
        # --- Standard Checks ---
        if not dm.trend_state or len(dm.data_df) < 1:
            return None, None, None, None
        
        # --- Trigger Logic: Based on Established Trend State Only (Supertrend Filter) ---
        
        if dm.trend_state == 'BULLISH':
            side = 'CE'
        elif dm.trend_state == 'BEARISH':
            side = 'PE'
        else:
            return None, None, None, None
        
        # 1. Get the Option based on the established trend side
        option = self.strategy.get_entry_option(side)
        if not option:
            if log:
                await self.strategy._log_debug("Trend Cont. Entry",
                    f"⏳ No {side} option found (expiry={self.strategy.last_used_expiry}, "
                    f"spot={self.strategy.data_manager.prices.get(self.strategy.index_symbol)})")
            return None, None, None, None
            
        # 2. Check Option's current price against its current candle's open price (Green Candle Pre-Filter)
        current_price = dm.prices.get(option['tradingsymbol'])
        option_candle = dm.option_candles.get(option['tradingsymbol'])
        
        if not current_price or not option_candle:
            if log:
                await self.strategy._log_debug("Trend Cont. Entry",
                    f"⏳ {option['tradingsymbol']}: No data yet (price={current_price}, candle={'✓' if option_candle else 'None'})")
            return None, None, None, None

        # ✅ CHECK 1: GREEN CANDLE - Must be green (LTP > Open)
        candle_open = option_candle.get('open', 0)
        if current_price <= candle_open:
            if log:
                await self.strategy._log_debug("Trend Cont. Entry", 
                    f"❌ {option['tradingsymbol']}: RED candle (LTP: ₹{current_price:.2f} ≤ Open: ₹{candle_open:.2f})")
            return None, None, None, None
        
        # ✅ CHECK 2: VELOCITY - Must be rising (positive velocity ≥ ₹0.05/s)
        velocity = self.strategy.calculate_price_velocity(option['tradingsymbol'], lookback_seconds=1.5)
        if velocity < 0.05:
            if log:
                await self.strategy._log_debug("Trend Cont. Entry", 
                    f"❌ {option['tradingsymbol']}: Low velocity (₹{velocity:.3f}/s < ₹0.05/s)")
            return None, None, None, None
        
        # ✅ CHECK 3: BREAKOUT - Must be above previous candle high (confirms strength)
        prev_candle = dm.previous_option_candles.get(option['tradingsymbol'])
        if prev_candle:
            prev_high = prev_candle.get('high', 0)
            if prev_high > 0 and current_price < prev_high * 0.998:  # Allow 0.2% below high
                if log:
                    await self.strategy._log_debug("Trend Cont. Entry", 
                        f"❌ {option['tradingsymbol']}: No breakout (LTP: ₹{current_price:.2f} < Prev High: ₹{prev_high:.2f})")
                return None, None, None, None
        
        if log:
            await self.strategy._log_debug("Trend Cont. Entry", 
                f"✅ {option['tradingsymbol']}: GREEN + Velocity ₹{velocity:.3f}/s + Breakout confirmed")
        
        # 🚀🚀 NO-WICK BYPASS CHECK: Skip 4-tick validation if no-wick entry conditions met
        no_wick_bypass = False
        if option_candle and self.strategy.params.get('enable_no_wick_entry', True):
            candle_open = option_candle.get('open', 0)
            candle_low = option_candle.get('low', 0)
            candle_high = option_candle.get('high', 0)
            candle_start_time = option_candle.get('candle_start_time', 0)
            
            if candle_open > 0 and candle_low > 0:
                # 🛡️ SAFETY: Require minimum ticks and candle age to avoid false positives
                price_history = self.strategy.data_manager.price_history.get(option['tradingsymbol'], [])
                tick_count = len(price_history)
                
                import time
                candle_age = time.time() - candle_start_time if candle_start_time > 0 else 0
                
                if tick_count >= 3 and candle_age >= 2.0:
                    # Already validated: GREEN + VELOCITY + BREAKOUT above (lines 1550-1570)
                    # Just check for no-wick condition
                    has_no_lower_wick = abs(candle_open - candle_low) < (candle_open * 0.001)  # Within 0.1%
                    
                    if has_no_lower_wick and candle_high > candle_open:
                        # 🛡️ FINAL CHECK: Ensure no significant reversal (>0.8% decline)
                        if tick_count >= 3:
                            recent_3_prices = [p for ts, p in price_history[-3:]]
                            # Check if declining AND decline is significant
                            if recent_3_prices[-1] < recent_3_prices[-2] and recent_3_prices[-2] < recent_3_prices[-3]:
                                decline_pct = ((recent_3_prices[-1] - recent_3_prices[-3]) / recent_3_prices[-3]) * 100
                                if decline_pct < -0.8:
                                    if log:
                                        await self.strategy._log_debug("🔴 ENTRY REJECTED", 
                                            f"❌ NO-WICK (Trend): {option['tradingsymbol']} declining {decline_pct:.2f}% ({recent_3_prices[-3]:.2f} → {recent_3_prices[-2]:.2f} → {recent_3_prices[-1]:.2f}) - significant reversal")
                                    # Don't set no_wick_bypass, continue to normal validation
                                else:
                                    # Minor decline (<0.8%), safe to use NO-WICK bypass
                                    no_wick_bypass = True
                                    if log:
                                        await self.strategy._log_debug("🚀 NO-WICK BYPASS", 
                                            f"⚡ {option['tradingsymbol']} - No wick, minor pullback {decline_pct:.2f}% OK (Ticks: {tick_count}, Age: {candle_age:.1f}s)")
                            else:
                                # Price not declining, safe to use NO-WICK bypass
                                no_wick_bypass = True
                                if log:
                                    await self.strategy._log_debug("🚀 NO-WICK BYPASS", 
                                        f"⚡ Skipping 4-tick check for {option['tradingsymbol']} - No wick (Ticks: {tick_count}, Age: {candle_age:.1f}s)")
                        else:
                            # Not enough ticks for falling check, use NO-WICK anyway
                            no_wick_bypass = True
                            if log:
                                await self.strategy._log_debug("🚀 NO-WICK BYPASS", 
                                    f"⚡ Skipping 4-tick check for {option['tradingsymbol']} - No wick (Ticks: {tick_count}, Age: {candle_age:.1f}s)")
        
        # 🚀 MOMENTUM-WEIGHTED TICKS: Adaptive tick requirement based on momentum strength
        # Early momentum validation to get momentum_passed for adaptive tick reduction
        momentum_passed_value = 0
        if not no_wick_bypass:
            momentum_valid_early, momentum_data_early = await coord._validate_momentum_conditions(option, side)
            momentum_passed_value = momentum_data_early.get('momentum_checks_passed', 0)
            
            # Calculate required ticks based on momentum strength
            required_ticks = coord._calculate_required_ticks(momentum_passed_value)
            
            if log:
                await self.strategy._log_debug("Momentum-Weighted Ticks", 
                    f"⚡ {option['tradingsymbol']}: Momentum={momentum_passed_value}/3, Required Ticks={required_ticks} (default: 4)")
            
            # Sustained breakout check with adaptive tick requirement
            if not coord._validate_sustained_breakout(option['tradingsymbol'], required_ticks=required_ticks):
                if log: await self.strategy._log_debug("Sustained Breakout", f"Trend Cont. for {side} blocked - insufficient {required_ticks}-tick confirmation")
                return None, None, None, None
            
        # 3. Validation: Use the Coordinator to perform gauntlet checks
        # 🔥 OPTIMIZATION: Pass momentum_passed to gauntlet to avoid duplicate momentum validation
        is_valid, validation_data = await coord._enhanced_validate_entry_conditions_with_candle_color(option, side, log, momentum_passed_override=momentum_passed_value)
        
        if is_valid:
            trigger = f'Trend_Continuation_Opt_{side}'
            return side, trigger, option, validation_data        
        return None, None, None, None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🆕 V47 ST MOMENTUM SYNC ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Note: This engine class was permanently removed - V47TrendContinuationEngine is the only active engine
