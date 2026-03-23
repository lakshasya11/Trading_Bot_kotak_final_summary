# backend/core/kite_broker.py
"""
Kite (Zerodha) Broker Adapter

Thin wrapper around the existing kite.py RateLimitedKite instance.
Implements BrokerInterface so the rest of the codebase can work through
the abstraction layer without any changes to how Kite API calls are made.

All actual networking, rate-limiting, retry logic, and DNS fallback
remains in kite.py — this module just delegates.
"""

from .broker_interface import BrokerInterface


class KiteBroker(BrokerInterface):
    """
    Adapter that delegates every call to the existing RateLimitedKite
    instance created in kite.py.  No data transformation is needed
    because the interface constants and response formats are already
    modelled after Kite's conventions.
    """

    def __init__(self, kite_instance, original_kite_instance):
        """
        Args:
            kite_instance:          The RateLimitedKite wrapper from kite.py
            original_kite_instance: The raw KiteConnect for sync auth methods
        """
        self._kite = kite_instance
        self._original = original_kite_instance

    # ── Async API Methods (pass-through) ────────────────────────────────

    async def positions(self):
        return await self._kite.positions()

    async def margins(self, segment="equity"):
        return await self._kite.margins()

    async def orders(self):
        return await self._kite.orders()

    async def order_history(self, order_id):
        return await self._kite.order_history(order_id)

    async def place_order(self, **kwargs):
        return await self._kite.place_order(**kwargs)

    async def modify_order(self, variety, order_id, **kwargs):
        return await self._kite.modify_order(variety, order_id, **kwargs)

    async def cancel_order(self, variety, order_id):
        return await self._kite.cancel_order(variety, order_id)

    async def quote(self, instruments):
        return await self._kite.quote(instruments)

    async def ltp(self, instruments):
        return await self._kite.ltp(instruments)

    async def instruments(self, exchange=None):
        return await self._kite.instruments(exchange)

    async def profile(self):
        return await self._kite.profile()

    # ── Synchronous Methods (pass-through) ──────────────────────────────

    def historical_data(self, instrument_token, from_date, to_date, interval, **kwargs):
        return self._kite.historical_data(instrument_token, from_date, to_date, interval, **kwargs)

    def instruments_sync(self, exchange=None):
        return self._kite.instruments_sync(exchange)

    def set_access_token(self, token):
        return self._kite.set_access_token(token)

    def login_url(self):
        return self._kite.login_url()

    def generate_session(self, request_token, api_secret):
        return self._kite.generate_session(request_token, api_secret)

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def shutdown(self):
        await self._kite.shutdown()

    # ── Fallback for any attribute not explicitly defined ────────────────
    # This ensures backward compatibility if any code accesses an
    # attribute on the broker that only KiteConnect provides.

    def __getattr__(self, name):
        return getattr(self._kite, name)
