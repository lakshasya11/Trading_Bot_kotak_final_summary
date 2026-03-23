# backend/core/broker_interface.py
"""
Broker Abstraction Layer — Interface Definition

Defines a standard interface that all broker implementations must follow.
This allows switching between brokers (Kite/Zerodha, Kotak Neo, etc.)
by changing a single config value.

All response formats are normalized to a common structure regardless of broker.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BrokerInterface(ABC):
    """
    Abstract base class for broker implementations.

    All broker adapters (Kite, Kotak, etc.) must implement these methods
    and return data in the normalized format described in each docstring.

    Constants are defined here with Kite-compatible values so that existing
    code using kite.TRANSACTION_TYPE_BUY etc. works without changes.
    """

    # ── Standard Constants ──────────────────────────────────────────────
    # These match Kite's constant values. Broker adapters translate internally.
    TRANSACTION_TYPE_BUY = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"
    ORDER_TYPE_MARKET = "MARKET"
    ORDER_TYPE_LIMIT = "LIMIT"
    ORDER_TYPE_SL = "SL"
    ORDER_TYPE_SLM = "SL-M"
    VARIETY_REGULAR = "regular"
    PRODUCT_MIS = "MIS"
    PRODUCT_CNC = "CNC"
    PRODUCT_NRML = "NRML"
    VALIDITY_DAY = "DAY"
    VALIDITY_IOC = "IOC"
    EXCHANGE_NSE = "NSE"
    EXCHANGE_BSE = "BSE"
    EXCHANGE_NFO = "NFO"
    EXCHANGE_BFO = "BFO"

    # ── Async API Methods ───────────────────────────────────────────────

    @abstractmethod
    async def positions(self) -> Dict[str, List[Dict]]:
        """
        Fetch open positions.

        Returns:
            {"net": [position_dict, ...], "day": [position_dict, ...]}

        Each position_dict must contain:
            tradingsymbol: str
            quantity: int
            buy_price: float
            average_price: float
            last_price: float
            product: str  ("MIS", "CNC", "NRML")
        """

    @abstractmethod
    async def margins(self, segment: str = "equity") -> Dict:
        """
        Fetch account margins / available balance.

        Returns dict with at least:
            available.cash: float  (nested or flattened depending on broker)
        """

    @abstractmethod
    async def orders(self) -> List[Dict]:
        """Fetch all orders for the day."""

    @abstractmethod
    async def order_history(self, order_id: str) -> List[Dict]:
        """
        Fetch history / status of a specific order.

        Each entry must contain:
            status: str           — "COMPLETE" | "REJECTED" | "CANCELLED" | "OPEN" | "TRIGGER PENDING" | "PENDING"
            average_price: float
            filled_quantity: int
            status_message: str
            order_timestamp: str/datetime
            transaction_type: str — "BUY" | "SELL"
        """

    @abstractmethod
    async def place_order(self, **kwargs) -> str:
        """
        Place an order. Returns order_id string.

        Expected kwargs:
            variety: str           — e.g. "regular"
            exchange: str          — e.g. "NFO"
            tradingsymbol: str
            transaction_type: str  — "BUY" | "SELL"
            quantity: int
            product: str           — "MIS" | "CNC" | "NRML"
            order_type: str        — "MARKET" | "LIMIT"
            price: float           — required for LIMIT orders
            validity: str          — "DAY" | "IOC"
            tag: str               — optional order tag
        """

    @abstractmethod
    async def modify_order(self, variety: str, order_id: str, **kwargs) -> str:
        """Modify an existing order. Returns order_id."""

    @abstractmethod
    async def cancel_order(self, variety: str, order_id: str) -> str:
        """Cancel a pending order. Returns order_id."""

    @abstractmethod
    async def quote(self, instruments: List[str]) -> Dict:
        """
        Get full quote with market depth.

        Args:
            instruments: ["EXCHANGE:SYMBOL", ...]

        Returns:
            {
                "EXCHANGE:SYMBOL": {
                    "last_price": float,
                    "depth": {
                        "buy": [{"price": float, "quantity": int}, ...],
                        "sell": [{"price": float, "quantity": int}, ...]
                    }
                }
            }
        """

    @abstractmethod
    async def ltp(self, instruments: List[str]) -> Dict:
        """
        Get last traded price.

        Returns:
            {"EXCHANGE:SYMBOL": {"last_price": float}}
        """

    @abstractmethod
    async def instruments(self, exchange: str = None) -> List[Dict]:
        """
        Fetch instrument master list.

        Each instrument dict must contain:
            instrument_token: int/str
            tradingsymbol: str
            lot_size: int
            expiry: date/str
            strike: float
            instrument_type: str  ("CE", "PE", "FUT", "EQ")
            name: str
            exchange: str
        """

    @abstractmethod
    async def profile(self) -> Dict:
        """
        Fetch user profile.

        Returns dict with at least:
            user_id: str
            user_name: str
        """

    # ── Synchronous Methods ─────────────────────────────────────────────

    @abstractmethod
    def historical_data(self, instrument_token, from_date, to_date, interval, **kwargs):
        """
        Fetch OHLC historical candle data (synchronous).

        Returns list of dicts: [{"date": ..., "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}, ...]
        """

    @abstractmethod
    def instruments_sync(self, exchange: str = None) -> List[Dict]:
        """Synchronous instruments call for init / bootstrap contexts."""

    @abstractmethod
    def set_access_token(self, token: str):
        """Set the broker access / session token."""

    @abstractmethod
    def login_url(self) -> str:
        """Get the login / authentication URL for OAuth or redirect-based auth."""

    @abstractmethod
    def generate_session(self, request_token: str, api_secret: str) -> Dict:
        """
        Exchange a request/auth token for a full session.

        Returns session dict with at least:
            access_token: str
        """

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def shutdown(self):
        """Graceful shutdown — override if the broker needs cleanup."""
        pass
