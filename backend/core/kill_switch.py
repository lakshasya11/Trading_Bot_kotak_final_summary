"""
Kill Switch for Emergency System Failure Detection

Only monitors TECHNICAL failures, not trading outcomes.
Automatically stops bot when system issues are detected.

Does NOT interfere with:
- max_daily_loss parameter (handles trading losses)
- max_daily_profit parameter (handles profit targets)
- Strategy decisions (that's your strategy's job)
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Dict

# IST Timezone for consistent timing
IST = ZoneInfo("Asia/Kolkata")

def get_ist_time():
    """Get current time in IST timezone (UTC-based to avoid system clock drift)"""
    utc_now = datetime.now(timezone.utc)
    ist_now = utc_now.astimezone(IST)
    return ist_now


class KillSwitch:
    """
    Emergency stop mechanism for SYSTEM FAILURES only.
    
    Monitors:
    - Failed orders (API rejections, network issues, system errors)
    - WebSocket disconnections (prolonged)
    - API rate limit violations
    
    Does NOT monitor:
    - Trading losses (handled by max_daily_loss)
    - Consecutive losses (strategy decision, not system issue)
    - Profit targets (handled by max_daily_profit)
    """
    
    def __init__(self):
        # Technical failure tracking
        self.max_failed_orders = 10      # Stop after 10 order rejections
        self.max_api_failures = 5        # Stop after 5 consecutive API failures
        self.max_disconnect_time = 120   # Stop after 2 minutes disconnected
        
        # State tracking
        self.failed_orders_count = 0
        self.api_failure_count = 0
        self.disconnect_start_time = None
        self.is_active = False
        self.trigger_reason = None
        self.last_reset_date = get_ist_time().date()
    
    def check_failed_orders(self, order_status: str, error_message: str = "") -> Optional[str]:
        """
        Track order rejections - indicates SYSTEM issues.
        
        This catches:
        - API connection problems
        - Invalid instrument tokens (data issue)
        - Rate limit violations (too many requests)
        - Broker system errors
        
        Does NOT count:
        - Insufficient funds (capital management issue, not system error)
        
        Args:
            order_status: Status of the order ("REJECTED", "CANCELLED", "COMPLETE", etc.)
            error_message: Error message from the order rejection
        
        Returns:
            Trigger reason if kill switch activated, None otherwise
        """
        # Don't count insufficient funds as system failure
        if "insufficient funds" in error_message.lower():
            return None
        
        if order_status in ["REJECTED", "CANCELLED"]:
            self.failed_orders_count += 1
            if self.failed_orders_count >= self.max_failed_orders:
                self.is_active = True
                self.trigger_reason = (
                    f"KILL_SWITCH: {self.failed_orders_count} failed orders detected. "
                    f"System issue - check API connection, broker account, or instrument data."
                )
                return self.trigger_reason
        
        return None
    
    def should_block_trading(self) -> tuple[bool, Optional[str]]:
        """
        Check if trading should be blocked due to system issues.
        
        Returns:
            (should_block, reason) tuple
        """
        if self.is_active:
            return True, self.trigger_reason
        
        return False, None
    
    def reset_daily(self):
        """Reset counters at market open (called by strategy)"""
        current_date = get_ist_time().date()
        if current_date != self.last_reset_date:
            self.failed_orders_count = 0
            self.is_active = False
            self.trigger_reason = None
            self.last_reset_date = current_date
            print("🔄 Kill Switch: Daily reset complete")
    
    def get_status(self) -> Dict:
        """Get current kill switch status for UI display"""
        return {
            "is_active": self.is_active,
            "trigger_reason": self.trigger_reason,
            "failed_orders": self.failed_orders_count,
            "max_failed_orders": self.max_failed_orders
        }
    
    def check_api_failure(self) -> Optional[str]:
        """
        Track consecutive API failures.
        Reset count on any successful API call.
        """
        self.api_failure_count += 1
        if self.api_failure_count >= self.max_api_failures:
            self.is_active = True
            self.trigger_reason = (
                f"KILL_SWITCH: {self.api_failure_count} consecutive API failures. "
                f"Network or broker API issue detected."
            )
            return self.trigger_reason
        return None
    
    def reset_api_failure_count(self):
        """Reset API failure count after successful call"""
        self.api_failure_count = 0
    
    def check_websocket_disconnect(self, is_connected: bool) -> Optional[str]:
        """
        Track WebSocket disconnection time.
        """
        if not is_connected:
            if self.disconnect_start_time is None:
                self.disconnect_start_time = get_ist_time()
            else:
                disconnect_duration = (get_ist_time() - self.disconnect_start_time).total_seconds()
                if disconnect_duration >= self.max_disconnect_time:
                    self.is_active = True
                    self.trigger_reason = (
                        f"KILL_SWITCH: WebSocket disconnected for {disconnect_duration:.0f} seconds. "
                        f"Market data feed lost."
                    )
                    return self.trigger_reason
        else:
            # Connected - reset disconnect timer
            self.disconnect_start_time = None
        
        return None
    
    def configure(self, max_failed_orders: int = None, max_api_failures: int = None, max_disconnect_time: int = None):
        """Allow runtime configuration of limits"""
        if max_failed_orders is not None:
            self.max_failed_orders = max_failed_orders
        if max_api_failures is not None:
            self.max_api_failures = max_api_failures
        if max_disconnect_time is not None:
            self.max_disconnect_time = max_disconnect_time
    
    def manual_reset(self):
        """Manually reset kill switch (use carefully!)"""
        self.failed_orders_count = 0
        self.api_failure_count = 0
        self.disconnect_start_time = None
        self.is_active = False
        self.trigger_reason = None
        print("⚠️ Kill Switch: Manual reset performed")


# Global instance
kill_switch = KillSwitch()
