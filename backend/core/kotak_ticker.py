# backend/core/kotak_ticker.py
"""
Kotak Neo WebSocket Ticker Adapter — Direct Implementation

Implements TickerInterface for Kotak Securities Neo Trade API v2.
Uses polling via the Quotes REST endpoint as a fallback since the v2
WebSocket specification is not publicly documented.

Normalizes tick data to the same format the strategy expects from Kite:
    [{"instrument_token": int, "last_price": float}, ...]

No external SDK required — uses only stdlib + the KotakBroker REST methods.
"""

import asyncio
import time
from datetime import datetime, time as dt_time
from typing import TYPE_CHECKING, List

from .ticker_interface import TickerInterface

if TYPE_CHECKING:
    from .strategy import Strategy
    from .kotak_broker import KotakBroker


class KotakTicker(TickerInterface):
    """
    Tick data provider for Kotak Neo API.

    Uses high-frequency REST polling of the Quotes endpoint to deliver
    tick data in real-time. Polling interval is configurable (default 500ms).
    """

    def __init__(self, strategy_instance: "Strategy", main_loop,
                 kotak_broker: "KotakBroker", config: dict):
        super().__init__(strategy_instance, main_loop)
        self._broker = kotak_broker
        self._config = config
        self._subscribed_tokens: List[dict] = []   # [{instrument_token, exchange_segment}]
        self._subscribed_kite_tokens: List[int] = []  # raw int tokens
        self._poll_task: asyncio.Task = None
        self._poll_interval: float = float(config.get("kotak_poll_interval", 0.5))

    # ── Tick Polling ────────────────────────────────────────────────────

    async def _poll_quotes(self):
        """Continuously poll quotes and deliver ticks to strategy."""
        print(f">>> KOTAK TICKER: Polling started ({self._poll_interval}s interval)")
        self.is_connected = True
        self.connected_event.set()
        self.disconnected_event.clear()
        self.last_tick_time = time.time()
        self.reconnection_count += 1

        if self.strategy:
            await self.strategy.on_ticker_connect()

        while not self.manual_stop:
            try:
                if not self._subscribed_kite_tokens:
                    await asyncio.sleep(1)
                    continue

                # Build instrument strings for the broker's quote method
                instruments = []
                for tok_info in self._subscribed_tokens:
                    token = tok_info.get("instrument_token", "")
                    segment = tok_info.get("exchange_segment", "nse_fo")
                    exchange = self._SEGMENT_TO_EXCHANGE.get(segment, "NFO")
                    instruments.append(f"{exchange}:{token}")

                if instruments:
                    quote_data = await self._broker.quote(instruments)
                    ticks = []
                    zero_count = 0
                    for inst_key, data in quote_data.items():
                        ltp = data.get("last_price", 0)
                        if ltp > 0:
                            # Extract token from the key (format: "EXCHANGE:token")
                            parts = inst_key.split(":")
                            token_str = parts[1] if len(parts) > 1 else parts[0]
                            try:
                                ticks.append({
                                    "instrument_token": int(token_str),
                                    "last_price": float(ltp),
                                })
                            except (ValueError, TypeError):
                                continue
                        else:
                            zero_count += 1

                    # 🔍 DEBUG: Log tick stats once when options are subscribed
                    if not hasattr(self, '_tick_stats_logged') and len(instruments) > 1:
                        self._tick_stats_logged = True
                        print(f">>> KOTAK TICKER: Quote result — {len(ticks)} with LTP>0, "
                              f"{zero_count} with LTP=0, "
                              f"total keys in response: {len(quote_data)}")
                        if zero_count > 0 and len(ticks) <= 1:
                            print(f">>> KOTAK TICKER: ⚠️ Options have LTP=0 (market closed). "
                                  f"Option chain will populate during market hours.")
                        # Log tick tokens vs strategy mapping
                        if ticks and self.strategy:
                            mapped = 0
                            unmapped_tokens = []
                            for t in ticks:
                                tok = t["instrument_token"]
                                if tok in self.strategy.token_to_symbol:
                                    mapped += 1
                                else:
                                    unmapped_tokens.append(tok)
                            print(f">>> KOTAK TICKER: Token mapping — {mapped}/{len(ticks)} "
                                  f"tokens found in strategy.token_to_symbol")
                            if unmapped_tokens:
                                print(f">>> KOTAK TICKER: ⚠️ Unmapped tokens: {unmapped_tokens[:5]}")
                            # Also log sample of quote_data keys
                            print(f">>> KOTAK TICKER: Quote keys sample: {list(quote_data.keys())[:5]}")

                    if ticks:
                        self.last_tick_time = time.time()
                        self.consecutive_tick_failures = 0
                        if self.strategy:
                            await self.strategy.handle_ticks_async(ticks)

                await asyncio.sleep(self._poll_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f">>> KOTAK TICKER: Poll error: {e}")
                self.consecutive_tick_failures += 1
                if self.consecutive_tick_failures > 10:
                    print(">>> KOTAK TICKER: Too many failures, pausing 10s...")
                    await asyncio.sleep(10)
                    self.consecutive_tick_failures = 0
                else:
                    await asyncio.sleep(2)

        # Cleanup
        self.is_connected = False
        self.connected_event.clear()
        self.disconnected_event.set()
        if self.strategy:
            await self.strategy.on_ticker_disconnect()
        print(">>> KOTAK TICKER: Polling stopped.")

    # ── Public Interface ────────────────────────────────────────────────

    def start(self):
        """Start the Kotak quote polling."""
        print("\n" + "=" * 70)
        print("  STARTING KOTAK NEO TICKER (REST POLLING)")
        print("=" * 70)
        self.manual_stop = False

        if not self._broker.is_logged_in:
            print(">>> KOTAK TICKER: Broker not logged in! Cannot start.")
            return

        # Schedule polling coroutine
        self._poll_task = asyncio.run_coroutine_threadsafe(
            self._poll_quotes(), self.main_loop
        )
        print(f">>> KOTAK TICKER: Poll task scheduled "
              f"({self._poll_interval}s interval)")

    async def stop(self):
        """Stop the Kotak quote polling."""
        print(">>> KOTAK TICKER: Stop requested.")
        self.manual_stop = True

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                self._poll_task.result()
            except (asyncio.CancelledError, Exception):
                pass
            self._poll_task = None

        if self.health_check_task and not self.health_check_task.done():
            self.health_check_task.cancel()
            self.health_check_task = None

        self.is_connected = False
        print(">>> KOTAK TICKER: Stopped.")

    # Known index tokens that live on cash segment, not F&O
    _INDEX_TOKENS = {
        26000: "nse_cm",   # NIFTY
        26009: "nse_cm",   # BANKNIFTY
        1: "bse_cm",       # SENSEX
    }

    _SEGMENT_TO_EXCHANGE = {
        "nse_fo": "NFO", "nse_cm": "NSE",
        "bse_fo": "BFO", "bse_cm": "BSE",
        "mcx_fo": "MCX", "cde_fo": "CDS",
    }

    def subscribe(self, tokens: List[int]):
        """Subscribe to instrument tokens for real-time ticks."""
        # Determine the correct F&O segment from the strategy's exchange config
        # SENSEX -> BFO -> bse_fo, NIFTY/BANKNIFTY -> NFO -> nse_fo
        fo_segment = "nse_fo"  # default
        if self.strategy:
            exchange = getattr(self.strategy, 'exchange', 'NFO')
            _EXCHANGE_TO_SEGMENT = {
                "NFO": "nse_fo", "BFO": "bse_fo",
                "MCX": "mcx_fo", "CDS": "cde_fo",
            }
            fo_segment = _EXCHANGE_TO_SEGMENT.get(exchange, "nse_fo")

        for t in tokens:
            # Index tokens use cash segment, options use F&O segment
            segment = self._INDEX_TOKENS.get(t, fo_segment)
            kotak_token = {
                "instrument_token": str(t),
                "exchange_segment": segment,
            }
            if kotak_token not in self._subscribed_tokens:
                self._subscribed_tokens.append(kotak_token)
            if t not in self._subscribed_kite_tokens:
                self._subscribed_kite_tokens.append(t)

        print(f">>> KOTAK TICKER: Now tracking {len(self._subscribed_kite_tokens)} tokens")

    def resubscribe(self, tokens: List[int]):
        """Replace current subscriptions with a new set of tokens.
        
        Clears old subscriptions and subscribes to the new token list.
        Called by strategy when ATM strikes change or option chain refreshes.
        """
        self._subscribed_tokens.clear()
        self._subscribed_kite_tokens.clear()
        self.subscribe(tokens)
