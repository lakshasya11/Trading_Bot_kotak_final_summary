import math
import asyncio

class RiskManager:
    """Handles position sizing and risk calculations."""
    def __init__(self, params, log_debug_func):
        self.params = params
        self.log_debug = log_debug_func
        self.pending_logs = []  # Store logs to be sent by caller

    def _queue_log(self, source, message):
        """Queue a log message to be sent by the caller (avoids duplicate asyncio.create_task issues)"""
        self.pending_logs.append((source, message))

    def calculate_trade_details(self, price, lot_size, available_cash=None, daily_pnl=0):
        """
        Hybrid Capital Logic: Combines live Zerodha capital with GUI threshold.
        
        Capital Selection Rules:
        1. Fetch live capital from Zerodha
        2. Use GUI threshold as maximum limit
        3. Use min(live_capital, gui_threshold) for position sizing
        4. If live capital drops below threshold, trade with lesser capital
        5. Apply Smart Capital rules for daily P&L adjustments
        """
        # Clear pending logs from previous calls
        self.pending_logs = []
        
        # Get GUI threshold from params (user's risk limit)
        gui_threshold = float(self.params.get("start_capital", 50000))
        
        # Determine capital source and whether to apply Smart Capital logic
        using_live_capital = False
        
        if available_cash is not None:
            # Live Trading: Use actual broker balance
            # Smart Capital should NOT apply here - losses already reflected in balance!
            if gui_threshold > available_cash:
                # GUI exceeds available funds - use available cash
                base_capital = available_cash
                using_live_capital = True
                capital_source = "Live Broker Balance"
                self._queue_log("Capital", 
                    f"⚠️ GUI threshold (₹{gui_threshold:.0f}) > Available (₹{available_cash:.0f}). Using ₹{available_cash:.0f}")
            else:
                # GUI within limits - use it (Paper Trading mode)
                base_capital = gui_threshold
                capital_source = "GUI Threshold (User Setting)"
                self._queue_log("Capital", 
                    f"💰 Using GUI capital: ₹{gui_threshold:.0f} (Available: ₹{available_cash:.0f})")
        else:
            # Broker API unavailable - use GUI setting only
            base_capital = gui_threshold
            capital_source = "GUI Threshold Only (Broker API unavailable)"
            self._queue_log("Capital", f"⚠️ Using GUI capital: ₹{gui_threshold:.0f} (Broker API unavailable)")
        
        # Apply V47.14 Smart Capital adjustments ONLY for GUI/Paper Trading
        # NEVER reduce live broker balance by daily P&L (that's double-counting losses!)
        if using_live_capital:
            # Live capital already reflects losses - use as-is
            capital_to_use = base_capital
            self._queue_log("Capital", 
                f"💡 Using live balance as-is: ₹{capital_to_use:.0f} (losses already reflected)")
        else:
            # Paper Trading: Apply Smart Capital de-leveraging on losses
            current_real_time_capital = base_capital + daily_pnl
            # Use minimum of base capital and current capital
            # - On profit: uses base capital (no compounding)
            # - On loss: uses reduced capital (de-leveraging)
            effective_capital = min(base_capital, current_real_time_capital)
            capital_to_use = effective_capital
            if daily_pnl < 0:
                self._queue_log("Capital", 
                    f"📉 Smart Capital applied: ₹{base_capital:.0f} → ₹{capital_to_use:.0f} (daily loss: ₹{daily_pnl:.0f})")
        
        sl_points = float(self.params.get("trailing_sl_points", 5.0))  # Default: 5 points
        sl_percent = float(self.params.get("trailing_sl_percent", 2.5))  # Default: 2.5%

        if price is None or price < 1.0 or lot_size is None:
            self._queue_log("Risk", f"Invalid price/lot_size: P={price}, L={lot_size}")
            return None, None

        initial_sl_price = max(price - sl_points, price * (1 - sl_percent / 100))
        risk_per_share = price - initial_sl_price

        if risk_per_share <= 0:
            self._queue_log("Risk", f"Cannot calculate quantity. Risk per share is zero or negative.")
            return None, None
        
        # Calculate lots based on effective capital
        value_per_lot = price * lot_size
        if value_per_lot <= 0:
            self._queue_log("Risk", "Trade Aborted. Invalid price or lot size.")
            return None, None
            
        max_lots_by_capital = math.floor(capital_to_use / value_per_lot)
        
        # 🔍 DEBUG: Log the exact calculation for troubleshooting
        self._queue_log("Risk-Calc", 
            f"Lots calculation: Capital=₹{capital_to_use:.0f} / ValuePerLot=₹{value_per_lot:.0f} (Price=₹{price:.2f} × LotSize={lot_size}) = {max_lots_by_capital} lots")
        
        # ⚡ CRITICAL FIX: Never reject a valid signal due to capital constraints
        # Risk management should NOT filter signals - only control position size
        # If insufficient capital for full position, take minimum 1 lot to capture the signal
        min_lots = int(self.params.get("min_position_lots", 1))  # Default: 1 lot minimum
        
        if max_lots_by_capital == 0:
            self._queue_log("Risk", 
                f"⚠️ Capital tight: Need ₹{value_per_lot:.0f}, have ₹{capital_to_use:.0f}. "
                f"Taking minimum {min_lots} lot(s) to capture signal.")
            final_num_lots = min_lots  # Force minimum position
        else:
            # Use capital-based calculation (maximize capital usage)
            final_num_lots = max(max_lots_by_capital, min_lots)  # At least min_lots
        
        # Calculate risk information for logging
        risk_per_lot = risk_per_share * lot_size
        total_risk_amount = final_num_lots * risk_per_lot
        # Calculate risk as percentage of the capital being used
        risk_percent_actual = (total_risk_amount / capital_to_use) * 100 if capital_to_use > 0 else 0
        
        # Log position information
        capital_status = "✅ Full" if max_lots_by_capital > 0 else "⚠️ Minimum (Capital Limited)"
        self._queue_log("Risk", 
            f"📊 Position: {final_num_lots} lots ({capital_status}) | "
            f"Capital: ₹{capital_to_use:.0f}, Risk: ₹{total_risk_amount:.0f} [{risk_percent_actual:.1f}%])")
            
        qty = final_num_lots * lot_size
        return qty, initial_sl_price
