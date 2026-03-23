# backend/core/ticker_interface.py
"""
Ticker / WebSocket Abstraction Layer — Interface Definition

Defines a standard interface for real-time market data WebSocket connections.
All ticker implementations (Kite, Kotak, etc.) must conform to this interface.

Tick data is normalized to: [{"instrument_token": int, "last_price": float}, ...]
"""

import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional


class TickerInterface(ABC):
    """
    Abstract base class for market-data ticker implementations.

    Subclasses must implement start(), stop(), and subscribe().
    The callbacks (on_ticks, on_connect, on_disconnect) are routed
    through the strategy instance that is passed at construction time.
    """

    def __init__(self, strategy_instance, main_loop):
        self.strategy = strategy_instance
        self.main_loop = main_loop
        self.is_connected: bool = False
        self.is_reconnecting: bool = False
        self.manual_stop: bool = False

        # Events for synchronization
        self.connected_event = asyncio.Event()
        self.disconnected_event = asyncio.Event()

        # Health monitoring state
        self.last_tick_time: Optional[float] = None
        self.health_check_task: Optional[asyncio.Future] = None
        self.consecutive_tick_failures: int = 0
        self.reconnection_count: int = 0
        self._last_market_sync_second = None

    # ── Abstract Methods ────────────────────────────────────────────────

    @abstractmethod
    def start(self):
        """
        Start the WebSocket connection in a background thread.
        Must be non-blocking.
        """

    @abstractmethod
    async def stop(self):
        """Stop the WebSocket connection and clean up resources."""

    @abstractmethod
    def subscribe(self, tokens: List[int]):
        """
        Subscribe to a list of instrument tokens for real-time data.

        Ticks must be forwarded to ``self.strategy.handle_ticks_async(ticks)``
        with each tick normalized to at least::

            {"instrument_token": <int>, "last_price": <float>}
        """
