"""
Rate Limiter for Broker API Compliance

Enforces broker-specific API rate limits to prevent API key bans
and order rejections due to exceeding rate limits.
"""

import asyncio
import time
from collections import deque
from typing import Optional


class RateLimiter:
    """
    Token bucket rate limiter for API calls.
    Ensures compliance with Zerodha's rate limits.
    """
    
    def __init__(self, max_requests: int = 3, time_window: float = 1.0):
        """
        Initialize rate limiter.
        
        Args:
            max_requests: Maximum number of requests allowed in time window
            time_window: Time window in seconds (default: 1.0)
        """
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = deque()
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        """
        Acquire permission to make a request.
        Blocks if rate limit would be exceeded, automatically waits.
        
        This method ensures your code never exceeds the configured rate limit.
        """
        try:
            async with self._lock:
                now = time.time()
                
                # Remove requests outside the time window
                while self.requests and self.requests[0] <= now - self.time_window:
                    self.requests.popleft()
                
                # If at limit, calculate how long to wait
                if len(self.requests) >= self.max_requests:
                    # Wait until oldest request expires
                    sleep_time = self.requests[0] + self.time_window - now
                    if sleep_time > 0:
                        await asyncio.sleep(sleep_time)
                        # Clean up again after waiting
                        now = time.time()
                        while self.requests and self.requests[0] <= now - self.time_window:
                            self.requests.popleft()
                
                # Record this request
                self.requests.append(now)
                
        except asyncio.CancelledError:
            # Handle cancellation gracefully during shutdown
            print("Rate limiter acquisition cancelled during shutdown")
            raise
    
    def get_remaining_requests(self) -> int:
        """Get number of requests available right now"""
        now = time.time()
        # Clean old requests
        while self.requests and self.requests[0] <= now - self.time_window:
            self.requests.popleft()
        return self.max_requests - len(self.requests)
    
    def get_stats(self) -> dict:
        """Get current rate limiter statistics"""
        return {
            "max_requests": self.max_requests,
            "time_window": self.time_window,
            "current_requests": len(self.requests),
            "remaining_requests": self.get_remaining_requests()
        }


# Global rate limiters — broker-aware
def _detect_broker():
    """Read broker name from config without importing broker_factory (avoids circular import)."""
    import os, json as _json
    for p in ["broker_config.json",
              os.path.join(os.path.dirname(os.path.dirname(__file__)), "broker_config.json")]:
        try:
            with open(p, "r") as f:
                return _json.load(f).get("broker", "kite").lower().strip()
        except FileNotFoundError:
            continue
    return "kite"

_BROKER = _detect_broker()

if _BROKER == "kotak":
    # Kotak quotes API allows higher throughput
    api_rate_limiter = RateLimiter(max_requests=10, time_window=1.0)
    order_rate_limiter = RateLimiter(max_requests=10, time_window=1.0)
else:
    # Zerodha: 3 req/s general, 10 orders/s
    api_rate_limiter = RateLimiter(max_requests=3, time_window=1.0)
    order_rate_limiter = RateLimiter(max_requests=10, time_window=1.0)
