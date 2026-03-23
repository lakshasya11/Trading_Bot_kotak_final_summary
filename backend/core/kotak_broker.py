# backend/core/kotak_broker.py
"""
Kotak Neo Broker Adapter — v2 REST API (Direct HTTP)

Implements BrokerInterface for Kotak Securities Neo Trade API v2.
Uses direct REST calls (requests / urllib) — no third-party SDK needed.

Authentication flow (two-step):
    1. TOTP Login  → POST /login/1.0/tradeApiLogin   → viewToken + viewSid
    2. MPIN Validate → POST /login/1.0/tradeApiValidate → session token (Auth),
       session sid (Sid), and dynamic baseUrl for all subsequent API calls.

Configuration (in broker_config.json):
    {
        "broker": "kotak",
        "kotak_access_token": "<from Neo dashboard>",
        "kotak_mobile": "+91XXXXXXXXXX",
        "kotak_ucc": "<client_code>",
        "kotak_totp_secret": "<TOTP secret from authenticator setup>",
        "kotak_mpin": "1234"
    }
"""

import asyncio
import csv
import io
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, date, timezone, timedelta
from typing import Dict, List, Optional

import logging
_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)

from .broker_interface import BrokerInterface
from .rate_limiter import api_rate_limiter, order_rate_limiter

# ── Fixed Login Endpoints ───────────────────────────────────────────────
_LOGIN_URL = "https://mis.kotaksecurities.com/login/1.0/tradeApiLogin"
_VALIDATE_URL = "https://mis.kotaksecurities.com/login/1.0/tradeApiValidate"

# ── Exchange Segment Mapping ────────────────────────────────────────────
_EXCHANGE_TO_SEGMENT = {
    "NSE": "nse_cm",
    "BSE": "bse_cm",
    "NFO": "nse_fo",
    "BFO": "bse_fo",
    "CDS": "cde_fo",
    "MCX": "mcx_fo",
}
_SEGMENT_TO_EXCHANGE = {v: k for k, v in _EXCHANGE_TO_SEGMENT.items()}

# ── Transaction Type Mapping ────────────────────────────────────────────
_TXN_TYPE_TO_KOTAK = {"BUY": "B", "SELL": "S"}
_TXN_TYPE_FROM_KOTAK = {"B": "BUY", "S": "SELL"}

# ── Order Type Mapping ──────────────────────────────────────────────────
_ORDER_TYPE_TO_KOTAK = {
    "MARKET": "MKT",
    "LIMIT": "L",
    "SL": "SL",
    "SL-M": "SL-M",
}
_ORDER_TYPE_FROM_KOTAK = {v: k for k, v in _ORDER_TYPE_TO_KOTAK.items()}

# ── Order Status Mapping ────────────────────────────────────────────────
_STATUS_FROM_KOTAK = {
    "complete": "COMPLETE",
    "completed": "COMPLETE",
    "traded": "COMPLETE",
    "rejected": "REJECTED",
    "cancelled": "CANCELLED",
    "open": "OPEN",
    "pending": "PENDING",
    "trigger pending": "TRIGGER PENDING",
    "not modified": "OPEN",
    "modified": "OPEN",
    "after market order req received": "PENDING",
}


def _generate_totp(secret: str) -> str:
    """Generate a 6-digit TOTP code from a base32 secret (RFC 6238)."""
    import base64
    import hmac
    import hashlib
    import struct

    # Decode base32 secret (strip spaces, uppercase)
    key = base64.b32decode(secret.upper().replace(" ", ""), casefold=True)
    # Time step
    counter = int(time.time()) // 30
    # HOTP
    msg = struct.pack(">Q", counter)
    hmac_digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = hmac_digest[-1] & 0x0F
    code = struct.unpack(">I", hmac_digest[offset:offset + 4])[0]
    code = (code & 0x7FFFFFFF) % 1_000_000
    return str(code).zfill(6)


class KotakBroker(BrokerInterface):
    """
    Broker adapter for Kotak Securities Neo Trade API v2.

    Uses direct REST calls. All response data is normalized to Kite-compatible
    format so the rest of the codebase works without changes.
    """

    def __init__(self, config: dict):
        self._config = config
        self._access_token: str = config.get("kotak_access_token", "")
        self._session_token: Optional[str] = None  # Auth header
        self._session_sid: Optional[str] = None     # Sid header
        self._base_url: Optional[str] = None        # Dynamic, from MPIN validate
        self._shutting_down = False

        # Cache for instrument master
        self._instrument_cache: Dict[str, List[Dict]] = {}
        self._scrip_to_symbol: Dict[str, str] = {}
        self._symbol_to_scrip: Dict[str, str] = {}

        if not self._access_token:
            raise ValueError(
                "kotak_access_token is required. Get it from Neo app → "
                "Invest → TradeAPI → API Dashboard."
            )

    # ── HTTP Helpers ────────────────────────────────────────────────────

    def _http_request(self, method: str, url: str, headers: dict,
                      body: Optional[str] = None) -> dict:
        """Synchronous HTTP request using urllib (no external deps)."""
        data = body.encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            try:
                return json.loads(error_body)
            except json.JSONDecodeError:
                raise Exception(f"HTTP {e.code}: {error_body[:300]}")

    def _post_login_headers(self) -> dict:
        """Headers for post-login APIs (Orders, Reports, Portfolio, etc.)."""
        return {
            "accept": "application/json",
            "Auth": self._session_token or "",
            "Sid": self._session_sid or "",
            "neo-fin-key": "neotradeapi",
        }

    def _quote_headers(self) -> dict:
        """Headers for Quotes & Scripmaster (only access token, no Auth/Sid)."""
        return {
            "Authorization": self._access_token,
            "Content-Type": "application/json",
        }

    def _ensure_logged_in(self):
        """Raise if session is not active."""
        if not self._base_url or not self._session_token:
            raise Exception(
                "Kotak session not active. Login first "
                "(TOTP → MPIN validate) before calling APIs."
            )

    def _post_jdata(self, endpoint: str, jdata: dict) -> dict:
        """POST with application/x-www-form-urlencoded jData body."""
        self._ensure_logged_in()
        url = f"{self._base_url}{endpoint}"
        headers = self._post_login_headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = "jData=" + urllib.parse.quote(json.dumps(jdata))
        return self._http_request("POST", url, headers, body)

    def _get_api(self, endpoint: str) -> dict:
        """GET request to a post-login API endpoint."""
        self._ensure_logged_in()
        url = f"{self._base_url}{endpoint}"
        headers = self._post_login_headers()
        return self._http_request("GET", url, headers)

    async def _async_post_jdata(self, endpoint: str, jdata: dict) -> dict:
        """Rate-limited async POST with jData."""
        if not self._shutting_down:
            await api_rate_limiter.acquire()
        return await asyncio.to_thread(self._post_jdata, endpoint, jdata)

    async def _async_order_post(self, endpoint: str, jdata: dict) -> dict:
        """Rate-limited async POST for order operations."""
        if not self._shutting_down:
            await order_rate_limiter.acquire()
        return await asyncio.to_thread(self._post_jdata, endpoint, jdata)

    async def _async_get(self, endpoint: str) -> dict:
        """Rate-limited async GET."""
        if not self._shutting_down:
            await api_rate_limiter.acquire()
        return await asyncio.to_thread(self._get_api, endpoint)

    async def _async_quote_get(self, url: str) -> dict:
        """Rate-limited async GET for quotes (different auth)."""
        if not self._shutting_down:
            await api_rate_limiter.acquire()
        return await asyncio.to_thread(
            self._http_request, "GET", url, self._quote_headers()
        )

    # ── Normalization Helpers ───────────────────────────────────────────

    @staticmethod
    def _normalize_position(pos: dict) -> dict:
        buy_qty = int(pos.get("flBuyQty", 0) or pos.get("buyQty", 0) or 0)
        sell_qty = int(pos.get("flSellQty", 0) or pos.get("sellQty", 0) or 0)
        return {
            "tradingsymbol": pos.get("trdSym") or pos.get("tradingSymbol") or pos.get("tsym", ""),
            "quantity": buy_qty - sell_qty,
            "buy_price": float(pos.get("buyAveragePrice") or pos.get("avgBuyPrice") or 0),
            "average_price": float(pos.get("averagePrice") or pos.get("netAveragePrice") or 0),
            "last_price": float(pos.get("ltp") or pos.get("lastTradedPrice") or 0),
            "product": pos.get("product") or pos.get("pc", "MIS"),
            "exchange": _SEGMENT_TO_EXCHANGE.get(
                pos.get("exchange_segment") or pos.get("es", ""),
                pos.get("exchange", ""),
            ),
        }

    @staticmethod
    def _normalize_order(order: dict) -> dict:
        raw_status = str(order.get("ordSt") or order.get("orderStatus") or
                         order.get("sts") or "").lower().strip()
        return {
            "order_id": str(order.get("nOrdNo") or order.get("orderId") or
                           order.get("order_id", "")),
            "status": _STATUS_FROM_KOTAK.get(raw_status, raw_status.upper()),
            "average_price": float(order.get("avgPrc") or
                                   order.get("averagePrice") or 0),
            "filled_quantity": int(order.get("fldQty") or
                                   order.get("filledQuantity") or 0),
            "quantity": int(order.get("qty") or order.get("quantity") or 0),
            "status_message": (order.get("rejReason") or
                               order.get("statusMessage") or ""),
            "order_timestamp": (order.get("exchOrdTm") or
                                order.get("orderTimestamp") or ""),
            "exchange_update_timestamp": order.get("exchOrdTm") or "",
            "transaction_type": _TXN_TYPE_FROM_KOTAK.get(
                order.get("trnsTp") or order.get("tt", ""),
                order.get("transactionType", ""),
            ),
            "tradingsymbol": (order.get("trdSym") or
                              order.get("tradingSymbol") or ""),
        }

    @staticmethod
    def _normalize_instrument(inst: dict) -> dict:
        expiry_raw = inst.get("expiry") or inst.get("dExp") or ""
        if isinstance(expiry_raw, str) and expiry_raw:
            for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
                try:
                    expiry_raw = datetime.strptime(expiry_raw, fmt).date()
                    break
                except ValueError:
                    continue

        return {
            "instrument_token": (inst.get("pScripRefKey") or
                                 inst.get("token") or
                                 inst.get("instrument_token", "")),
            "tradingsymbol": (inst.get("pTrdSymbol") or
                              inst.get("tradingSymbol") or
                              inst.get("tsym", "")),
            "lot_size": int(inst.get("pLotSize") or inst.get("lotSize") or 1),
            "expiry": expiry_raw,
            "strike": float(inst.get("dStrikePrice") or
                            inst.get("strikePrice") or 0),
            "instrument_type": (inst.get("pInstType") or
                                inst.get("optionType") or ""),
            "name": (inst.get("pSymbolName") or
                     inst.get("symbolName") or ""),
            "exchange": inst.get("pExchange") or inst.get("exchange") or "",
            "freeze_quantity": int(inst.get("pFreezeQty") or
                                   inst.get("freezeQty") or 0),
        }

    def _exchange_segment(self, exchange: str) -> str:
        return _EXCHANGE_TO_SEGMENT.get(exchange, exchange)

    # ── Async API Methods ───────────────────────────────────────────────

    async def positions(self):
        raw = await self._async_get("/quick/user/positions")
        pos_list = raw.get("data", []) if isinstance(raw, dict) else (
            raw if isinstance(raw, list) else []
        )
        normalized = [self._normalize_position(p) for p in pos_list]
        return {"net": normalized, "day": []}

    async def margins(self, segment="equity"):
        raw = await self._async_post_jdata(
            "/quick/user/limits",
            {"seg": "ALL", "exch": "ALL", "prod": "ALL"},
        )
        if isinstance(raw, dict):
            data = raw.get("data", raw)
            if isinstance(data, list) and data:
                data = data[0]
            cash_value = float(
                data.get("Net", 0) or
                data.get("cash", 0) or
                data.get("marginAvailable", 0) or 0
            )
            return {
                "equity": {
                    "available": {
                        "cash": cash_value,
                        "live_balance": cash_value,
                    }
                }
            }
        return {"equity": {"available": {"cash": 0, "live_balance": 0}}}

    async def orders(self):
        raw = await self._async_get("/quick/user/orders")
        order_list = raw.get("data", []) if isinstance(raw, dict) else (
            raw if isinstance(raw, list) else []
        )
        return [self._normalize_order(o) for o in order_list]

    async def order_history(self, order_id):
        raw = await self._async_post_jdata(
            "/quick/order/history",
            {"nOrdNo": str(order_id)},
        )
        hist_list = raw.get("data", []) if isinstance(raw, dict) else (
            raw if isinstance(raw, list) else []
        )
        normalized = [self._normalize_order(o) for o in hist_list]
        return normalized if normalized else [{
            "status": "PENDING", "average_price": 0,
            "filled_quantity": 0, "status_message": "",
        }]

    async def place_order(self, **kwargs):
        jdata = {
            "am": "NO",
            "dq": "0",
            "es": self._exchange_segment(kwargs.get("exchange", "NFO")),
            "mp": "0",
            "pc": kwargs.get("product", "MIS"),
            "pf": "N",
            "pr": str(kwargs.get("price", "0")),
            "pt": _ORDER_TYPE_TO_KOTAK.get(
                kwargs.get("order_type", "MARKET"), "MKT"
            ),
            "qt": str(kwargs.get("quantity", 0)),
            "rt": kwargs.get("validity", "DAY"),
            "tp": str(kwargs.get("trigger_price", "0")),
            "ts": kwargs.get("tradingsymbol", ""),
            "tt": _TXN_TYPE_TO_KOTAK.get(
                kwargs.get("transaction_type", "BUY"), "B"
            ),
        }

        raw = await self._async_order_post(
            "/quick/order/rule/ms/place", jdata
        )

        if isinstance(raw, dict):
            order_id = (raw.get("nOrdNo") or raw.get("orderId") or
                        raw.get("order_id", ""))
            if raw.get("stat") == "Ok" or order_id:
                return str(order_id)
            error = (raw.get("errMsg") or raw.get("message") or
                     raw.get("error") or str(raw))
            raise Exception(f"Order rejected: {error}")
        return str(raw)

    async def modify_order(self, variety, order_id, **kwargs):
        jdata = {
            "no": str(order_id),
            "am": "NO",
            "dq": "0",
            "es": self._exchange_segment(kwargs.get("exchange", "NFO")),
            "mp": "0",
            "pc": kwargs.get("product", "MIS"),
            "pf": "N",
            "pr": str(kwargs.get("price", "0")),
            "pt": _ORDER_TYPE_TO_KOTAK.get(
                kwargs.get("order_type", ""), ""
            ),
            "qt": str(kwargs.get("quantity", "0")),
            "rt": kwargs.get("validity", "DAY"),
            "tp": str(kwargs.get("trigger_price", "0")),
            "ts": kwargs.get("tradingsymbol", ""),
            "tt": _TXN_TYPE_TO_KOTAK.get(
                kwargs.get("transaction_type", ""), ""
            ),
        }
        await self._async_order_post("/quick/order/vr/modify", jdata)
        return str(order_id)

    async def cancel_order(self, variety, order_id):
        await self._async_order_post(
            "/quick/order/cancel",
            {"on": str(order_id), "am": "NO"},
        )
        return str(order_id)

    # Index pSymbol → display name (for matching quote response)
    # These are the exact exchange_token values returned by the Kotak API
    _INDEX_PTOKEN_TO_NAME = {
        "26000": "Nifty 50",
        "26009": "Nifty Bank",
        "1": "SENSEX",
        "NIFTY": "Nifty 50",       # name-based lookup
        "BANKNIFTY": "Nifty Bank",  # name-based lookup
        "SENSEX": "SENSEX",         # name-based lookup
    }

    # Index symbol → correct Kotak neosymbol pScripRefKey
    # Verified via direct API testing:
    #   nse_cm|Nifty 50  → works (NIFTY)
    #   nse_cm|Nifty Bank → works (BANKNIFTY)
    #   bse_cm|SENSEX    → works (SENSEX)
    #   nse_cm|NIFTY     → FAULT (invalid!)
    _INDEX_SCRIP_REF = {
        "26000": "Nifty 50",       # NSE NIFTY (by pSymbol)
        "NIFTY": "Nifty 50",       # NSE NIFTY (by name from strategy)
        "26009": "Nifty Bank",     # NSE BANK NIFTY (by pSymbol)
        "BANKNIFTY": "Nifty Bank", # NSE BANK NIFTY (by name from strategy)
        "1": "SENSEX",             # BSE SENSEX (by pSymbol)
        "SENSEX": "SENSEX",        # BSE SENSEX (by name from strategy)
    }

    async def quote(self, instruments):
        """Batch-fetch quotes for multiple instruments in a single API call.

        Per Kotak docs the URL format is:
            neosymbol/<exchange_segment>|<pSymbol>[,...]/<filter>
        pSymbol (numeric token) is used for all instruments.
        Response is a top-level JSON array.
        """
        if not instruments:
            return {}

        result = {
            inst: {"last_price": 0, "depth": {"buy": [], "sell": []}}
            for inst in instruments
        }

        # Build query parts and reverse-lookup map
        query_parts = []
        # Map "segment|identifier" → original "EXCHANGE:token" key
        reverse_map: Dict[str, str] = {}
        for inst_str in instruments:
            parts = inst_str.split(":")
            exchange = parts[0] if len(parts) > 1 else "NFO"
            symbol = parts[1] if len(parts) > 1 else parts[0]
            segment = self._exchange_segment(exchange)

            # Kotak neosymbol API format:
            #   Cash segment (index): pScripRefKey → bse_cm|SENSEX
            #   F&O segment (options): raw pSymbol → bse_fo|846144
            # Using pScripRefKey for F&O returns "Invalid neosymbol" fault!
            scrip_key = self._INDEX_SCRIP_REF.get(symbol)
            if scrip_key:
                # Index token → use pScripRefKey
                qkey = f"{segment}|{scrip_key}"
            else:
                # F&O token → use raw pSymbol (numeric)
                qkey = f"{segment}|{symbol}"

            query_parts.append(qkey)
            reverse_map[qkey] = inst_str
            # Register by raw token for response matching (exchange_token may differ)
            reverse_map[f"{segment}|{symbol}"] = inst_str
            name = self._INDEX_PTOKEN_TO_NAME.get(symbol)
            if name:
                reverse_map[f"{segment}|{name}"] = inst_str

        query_str = ",".join(query_parts)
        url = (f"{self._base_url}/script-details/1.0/quotes/"
               f"neosymbol/{urllib.parse.quote(query_str, safe='|,')}/all")

        # 🔍 DEBUG: Log when option tokens first appear
        if not hasattr(self, '_quote_option_debug_logged') and len(instruments) > 1:
            self._quote_option_debug_logged = True
            logger.debug(f"[KOTAK] Quote batch URL query: {query_str[:200]}...")
            logger.debug(f"[KOTAK] Scrip mappings count: {len(self._symbol_to_scrip)}")

        try:
            raw = await self._async_quote_get(url)

            # Response is a top-level JSON array [{...}, ...]
            items: list = []
            if isinstance(raw, list):
                items = raw
            elif isinstance(raw, dict):
                d = raw.get("data", raw)
                items = d if isinstance(d, list) else ([d] if d else [])

            # 🔍 DEBUG: Log first poll to help diagnose matching issues
            if not hasattr(self, '_quote_debug_logged'):
                self._quote_debug_logged = True
                if items:
                    sample = items[0] if items else {}
                    logger.debug(f"[KOTAK] Quote API sample response keys: {list(sample.keys())}")
                    logger.debug(f"[KOTAK] Quote API sample: token={sample.get('exchange_token', 'N/A')}, "
                          f"pSymbol={sample.get('pSymbol', 'N/A')}, "
                          f"exchange={sample.get('exchange', 'N/A')}, "
                          f"ltp={sample.get('ltp', 'N/A')}, "
                          f"trdSym={sample.get('trdSym', 'N/A')}")
                    logger.debug(f"[KOTAK] Reverse map keys: {list(reverse_map.keys())}")
                else:
                    logger.debug(f"[KOTAK] Quote API returned empty response. Raw: {str(raw)[:200]}")

            # 🔍 DEBUG: Log batch response details when options are first included
            if not hasattr(self, '_quote_batch_debug_logged') and len(instruments) > 1:
                self._quote_batch_debug_logged = True
                fault = None
                if isinstance(raw, dict):
                    fault = raw.get("fault") or raw.get("error")
                if isinstance(raw, list) and raw and isinstance(raw[0], dict):
                    fault = raw[0].get("fault")
                
                logger.debug(f"[KOTAK] Batch quote response: {len(items)} valid items from API, "
                      f"requested {len(instruments)} instruments")
                if fault:
                    logger.warning(f"[KOTAK] ⚠️ Batch fault detected: {str(fault)[:200]}")
                for item in items[:3]:
                    if isinstance(item, dict):
                        logger.debug(f"[KOTAK] Item: token={item.get('exchange_token','?')}, "
                              f"exchange={item.get('exchange','?')}, "
                              f"ltp={item.get('ltp','?')}")

            matched_count = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                ex_token = str(item.get("exchange_token", ""))
                ex_seg = item.get("exchange", "")

                # Match back to original instrument key
                inst_str = reverse_map.get(f"{ex_seg}|{ex_token}")
                if not inst_str:
                    # Fallback 1: match by token across all keys
                    for qk, ist in reverse_map.items():
                        if qk.endswith(f"|{ex_token}"):
                            inst_str = ist
                            break
                if not inst_str:
                    # Fallback 2: try pSymbol field (some Kotak responses use this)
                    p_symbol = str(item.get("pSymbol", ""))
                    if p_symbol:
                        inst_str = reverse_map.get(f"{ex_seg}|{p_symbol}")
                        if not inst_str:
                            for qk, ist in reverse_map.items():
                                if qk.endswith(f"|{p_symbol}"):
                                    inst_str = ist
                                    break
                if not inst_str:
                    continue

                ltp = float(item.get("ltp") or 0)
                matched_count += 1

                # Parse depth from nested objects per API docs
                depth_obj = item.get("depth", {})
                depth_buy = [
                    {"price": float(e.get("price", 0) or 0),
                     "quantity": int(e.get("quantity", 0) or 0)}
                    for e in (depth_obj.get("buy") or [])[:5]
                ]
                depth_sell = [
                    {"price": float(e.get("price", 0) or 0),
                     "quantity": int(e.get("quantity", 0) or 0)}
                    for e in (depth_obj.get("sell") or [])[:5]
                ]

                result[inst_str] = {
                    "last_price": ltp,
                    "depth": {"buy": depth_buy, "sell": depth_sell},
                }
                # Include daily OHLC from Kotak API (used for bootstrap seeding)
                ohlc_raw = item.get("ohlc")
                if isinstance(ohlc_raw, dict):
                    result[inst_str]["ohlc"] = {
                        "open": float(ohlc_raw.get("open", 0) or 0),
                        "high": float(ohlc_raw.get("high", 0) or 0),
                        "low": float(ohlc_raw.get("low", 0) or 0),
                        "close": float(ohlc_raw.get("close", 0) or 0),
                    }
            
            # 🔍 DEBUG: Log if no items matched (first 5 times only)
            if not hasattr(self, '_quote_nomatch_count'):
                self._quote_nomatch_count = 0
            if matched_count == 0 and items and self._quote_nomatch_count < 5:
                self._quote_nomatch_count += 1
                logger.warning(f"[KOTAK] ⚠️ Quote: {len(items)} items returned but 0 matched! "
                      f"Check token/segment mapping.")
        except Exception as e:
            logger.error(f"[KOTAK] Quote batch error: {e}")

        return result

    async def ltp(self, instruments):
        quote_data = await self.quote(instruments)
        return {k: {"last_price": v.get("last_price", 0)}
                for k, v in quote_data.items()}

    async def instruments(self, exchange=None):
        if exchange and exchange in self._instrument_cache:
            return self._instrument_cache[exchange]
        if not self._base_url:
            logger.info("[KOTAK] instruments: Not logged in yet, returning empty.")
            return []

        inst_list = await asyncio.to_thread(
            self._fetch_and_parse_scripmaster, exchange
        )

        if exchange:
            self._instrument_cache[exchange] = inst_list
        self._build_scrip_mappings(inst_list)
        return inst_list

    async def profile(self):
        return {
            "user_id": self._config.get("kotak_ucc",
                       self._config.get("kotak_user_id", "KOTAK_USER")),
            "user_name": self._config.get("kotak_user_name", "Kotak User"),
        }

    # ── Synchronous Methods ─────────────────────────────────────────────

    def historical_data(self, instrument_token, from_date, to_date,
                        interval, **kwargs):
        """Kotak Neo v2 has no historical-data endpoint.
        Bot will build indicators from live ticks instead."""
        return []

    def instruments_sync(self, exchange=None):
        if exchange and exchange in self._instrument_cache:
            return self._instrument_cache[exchange]
        if not self._base_url:
            logger.info("[KOTAK] instruments_sync: Not logged in yet, returning empty.")
            return []

        inst_list = self._fetch_and_parse_scripmaster(exchange)

        if exchange:
            self._instrument_cache[exchange] = inst_list
        self._build_scrip_mappings(inst_list)
        return inst_list

    # ── Scripmaster CSV Helpers ─────────────────────────────────────────

    _EXCHANGE_TO_CSV_SEGMENT = {
        "NFO": "nse_fo",
        "NSE": "nse_cm",
        "BSE": "bse_cm",
        "BFO": "bse_fo",
        "MCX": "mcx_fo",
        "CDS": "cde_fo",
    }

    def _fetch_and_parse_scripmaster(self, exchange=None) -> List[Dict]:
        """Download scripmaster CSV from Kotak and parse into Kite-format dicts.
        Uses disk cache (valid for the trading day) to avoid re-downloading."""
        import os
        import tempfile

        target_seg = self._EXCHANGE_TO_CSV_SEGMENT.get(exchange, "nse_fo")
        today_str = datetime.now().strftime("%Y%m%d")
        cache_file = os.path.join(tempfile.gettempdir(), f"kotak_scrip_{target_seg}_{today_str}.csv")

        # ── Use disk cache if available (same trading day) ──────────────
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8", errors="replace") as f:
                    csv_text = f.read()
                if csv_text.strip():
                    print(f"[KOTAK] Using cached scripmaster for {exchange} ({cache_file})")
                    return self._parse_scripmaster_csv(csv_text)
            except Exception as e:
                print(f"[KOTAK] Cache read failed, re-downloading: {e}")

        # Step 1: Get file paths
        url = f"{self._base_url}/script-details/1.0/masterscrip/file-paths"
        raw = self._http_request("GET", url, self._quote_headers())

        file_paths = []
        if isinstance(raw, dict):
            data = raw.get("data", raw)
            if isinstance(data, dict):
                file_paths = data.get("filesPaths", [])
            elif isinstance(data, list):
                file_paths = data

        if not file_paths:
            print("[KOTAK] No scripmaster file paths returned.")
            return []

        # Step 2: Find the right CSV URL for the exchange
        csv_url = None
        for fp in file_paths:
            if isinstance(fp, str) and target_seg in fp:
                csv_url = fp
                break

        if not csv_url:
            logger.warning(f"[KOTAK] No scripmaster CSV found for {exchange} (segment {target_seg}).")
            return []

        # Step 3: Download CSV with increased timeout
        logger.info(f"[KOTAK] Downloading scripmaster for {exchange} from: {csv_url}")
        req = urllib.request.Request(csv_url)
        csv_text = None
        for attempt, timeout in enumerate([120, 180], start=1):
            try:
                start_time = time.time()
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    csv_text = resp.read().decode("utf-8", errors="replace")
                duration = time.time() - start_time
                logger.info(f"[KOTAK] Scripmaster download successful ({len(csv_text)} bytes, {duration:.1f}s)")
                break
            except Exception as e:
                logger.error(f"[KOTAK] Scripmaster download attempt {attempt} failed (timeout={timeout}s): {e}")

        if not csv_text:
            logger.error("[KOTAK] All scripmaster download attempts failed.")
            return []

        # Step 4: Save to disk cache
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(csv_text)
            logger.info(f"[KOTAK] Scripmaster cached to {cache_file}")
        except Exception as e:
            logger.warning(f"[KOTAK] Could not cache scripmaster: {e}")

        # Step 5: Parse CSV
        logger.info(f"[KOTAK] Parsing scripmaster CSV ({exchange})...")
        return self._parse_scripmaster_csv(csv_text)

    def _parse_scripmaster_csv(self, csv_text: str) -> List[Dict]:
        """Parse Kotak scripmaster CSV into Kite-compatible instrument dicts."""
        reader = csv.DictReader(io.StringIO(csv_text))
        # Clean header names (strip whitespace, semicolons)
        if reader.fieldnames:
            reader.fieldnames = [
                h.strip().rstrip(";") for h in reader.fieldnames
            ]

        instruments = []
        for row in reader:
            try:
                # Parse expiry from timestamp
                # NOTE: Kotak NSE scripmaster uses epoch 1980-01-01 (315532800s offset
                # from Unix epoch), while BSE uses standard Unix epoch (1970-01-01).
                # We detect this by checking if the parsed date is unreasonably old.
                _EPOCH_1980_OFFSET = 315532800  # seconds between 1970-01-01 and 1980-01-01
                expiry_raw = row.get("lExpiryDate", "") or row.get("pExpiryDate", "")
                expiry_date = None
                if expiry_raw:
                    try:
                        ts = int(float(expiry_raw))
                        if ts > 0:
                            # Try both IST (Kotak default) and UTC (in case API returns UTC)
                            date_ist = datetime.fromtimestamp(ts, tz=_IST).date()
                            date_utc = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                            
                            # Prefer IST unless it's unreasonably old (before 2024)
                            if date_ist.year >= 2024:
                                expiry_date = date_ist
                            elif date_utc.year >= 2024:
                                expiry_date = date_utc
                            else:
                                # Both are old, try 1980-epoch offset
                                date_ist_1980 = datetime.fromtimestamp(
                                    ts + _EPOCH_1980_OFFSET, tz=_IST
                                ).date()
                                if date_ist_1980.year >= 2024:
                                    expiry_date = date_ist_1980
                                else:
                                    # Still old, default to IST
                                    expiry_date = date_ist
                    except (ValueError, OSError):
                        pass

                # Parse strike (Kotak stores as value * 100)
                strike_raw = row.get("dStrikePrice", "0") or "0"
                try:
                    strike_val = float(strike_raw) / 100.0
                except (ValueError, TypeError):
                    strike_val = 0.0

                # Parse freeze quantity
                freeze_raw = row.get("lFreezeQty", "0") or "0"
                try:
                    freeze_qty = int(float(freeze_raw))
                except (ValueError, TypeError):
                    freeze_qty = 0

                inst = {
                    "instrument_token": int(float(row.get("pSymbol", 0) or 0)),
                    "tradingsymbol": row.get("pTrdSymbol", "").strip(),
                    "lot_size": int(float(row.get("lLotSize", 1) or 1)),
                    "expiry": expiry_date,
                    "strike": strike_val,
                    "instrument_type": (row.get("pOptionType", "") or "").strip(),
                    "name": (row.get("pSymbolName", "") or "").strip(),
                    "exchange": (row.get("pExchange", "") or "").strip(),
                    "freeze_quantity": freeze_qty,
                    "exchange_segment": (row.get("pExchSeg", "") or "").strip(),
                    "_scrip_ref_key": (row.get("pScripRefKey", "") or "").strip(),
                }

                # Skip rows with empty tradingsymbol
                if inst["tradingsymbol"]:
                    instruments.append(inst)
            except Exception:
                continue

        print(f"[KOTAK] Parsed {len(instruments)} instruments from scripmaster.")
        return instruments

    def _build_scrip_mappings(self, inst_list: List[Dict]):
        """Build token→scrip and symbol→token lookups for quote API."""
        for inst in inst_list:
            token = str(inst.get("instrument_token", ""))
            symbol = inst.get("tradingsymbol", "")
            scrip_ref = inst.get("_scrip_ref_key", "")
            if token and scrip_ref:
                # token → scrip_ref_key used by quote() to build URL
                self._symbol_to_scrip[token] = scrip_ref
                self._scrip_to_symbol[scrip_ref] = token
            if token and symbol:
                # Also map tradingsymbol → token for reverse lookups
                self._symbol_to_scrip[symbol] = scrip_ref or token

    def set_access_token(self, token):
        self._access_token = token

    def login_url(self):
        return "https://mis.kotaksecurities.com/login/1.0/tradeApiLogin"

    def generate_session(self, request_token=None, api_secret=None):
        """
        Two-step Kotak Neo v2 login:
          1. TOTP Login  → viewToken + viewSid
          2. MPIN Validate → session token (Auth) + session sid (Sid) + baseUrl
        """
        mobile = self._config.get("kotak_mobile", "")
        ucc = self._config.get("kotak_ucc",
              self._config.get("kotak_user_id", ""))
        mpin = self._config.get("kotak_mpin", "")
        totp_secret = self._config.get("kotak_totp_secret", "")

        # ── Step 1: TOTP Login ──────────────────────────────────────────
        if totp_secret:
            totp_code = _generate_totp(totp_secret)
            print(f"[KOTAK] Step 1: TOTP login for {ucc} (auto-generated)...")
        else:
            print(f"\n{'='*60}")
            print(f"  KOTAK NEO LOGIN — Enter TOTP for {ucc}")
            print(f"{'='*60}")
            totp_code = input("  Enter 6-digit TOTP code: ").strip()
            if not totp_code or len(totp_code) != 6:
                raise Exception("Invalid TOTP code. Must be exactly 6 digits.")
            print(f"[KOTAK] Step 1: TOTP login for {ucc}...")

        login_headers = {
            "Authorization": self._access_token,
            "neo-fin-key": "neotradeapi",
            "Content-Type": "application/json",
        }
        login_body = json.dumps({
            "mobileNumber": mobile,
            "ucc": ucc,
            "totp": totp_code,
        })

        step1 = self._http_request("POST", _LOGIN_URL, login_headers, login_body)
        view_token = step1.get("data", {}).get("token") if isinstance(step1.get("data"), dict) else step1.get("token")
        view_sid = step1.get("data", {}).get("sid") if isinstance(step1.get("data"), dict) else step1.get("sid")

        if not view_token or not view_sid:
            error_msg = step1.get("message") or step1.get("error") or str(step1)
            raise Exception(f"TOTP login failed: {error_msg}")

        print("[KOTAK] Step 1 OK — viewToken + viewSid received.")

        # ── Step 2: MPIN Validate ───────────────────────────────────────
        print("[KOTAK] Step 2: MPIN validate...")

        validate_headers = {
            "Authorization": self._access_token,
            "neo-fin-key": "neotradeapi",
            "sid": view_sid,
            "Auth": view_token,
            "Content-Type": "application/json",
        }
        validate_body = json.dumps({"mpin": mpin})

        step2 = self._http_request(
            "POST", _VALIDATE_URL, validate_headers, validate_body
        )

        data = step2.get("data", step2) if isinstance(step2.get("data"), dict) else step2
        self._session_token = data.get("token") or data.get("Auth")
        self._session_sid = data.get("sid") or data.get("Sid")
        self._base_url = data.get("baseUrl") or data.get("redirectUrl")

        if not self._session_token or not self._session_sid or not self._base_url:
            error_msg = step2.get("message") or step2.get("error") or str(step2)
            raise Exception(f"MPIN validate failed: {error_msg}")

        # Strip trailing slash from baseUrl
        self._base_url = self._base_url.rstrip("/")

        print(f"[KOTAK] Step 2 OK — session active, baseUrl: {self._base_url}")
        return {
            "access_token": self._session_token,
            "sid": self._session_sid,
            "base_url": self._base_url,
        }

    @property
    def is_logged_in(self) -> bool:
        return bool(self._session_token and self._session_sid and self._base_url)

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def shutdown(self):
        self._shutting_down = True
