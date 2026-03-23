# backend/core/broker_factory.py
"""
Broker Factory — Central Entry Point for Broker & Ticker Instances

Reads ``broker_config.json`` (or falls back to Kite) and exposes:

    broker                        — BrokerInterface instance (Kite or Kotak)
    access_token                  — Current broker session token
    generate_session_and_set_token — Auth helper (broker-agnostic)
    re_initialize_session_from_file — Startup session restore
    create_ticker(strategy, loop)  — Creates the right ticker for the active broker
    BROKER_NAME                    — "kite" or "kotak"

All consuming modules import from here instead of from kite.py directly.
"""

import os
import json

# ── Determine Active Broker ─────────────────────────────────────────────

def _load_broker_config() -> dict:
    """Load broker_config.json from the backend directory."""
    possible_paths = [
        "broker_config.json",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "broker_config.json"),
    ]
    for path in possible_paths:
        try:
            with open(path, "r") as f:
                config = json.load(f)
                # If multi-user format, extract active user's config
                if "users" in config and "active_user" in config:
                    active_user_id = config["active_user"]
                    user_config = config["users"].get(active_user_id, {})
                    # Merge broker type with user-specific config
                    return {
                        "broker": config.get("broker", "kite"),
                        "kotak_user_name": user_config.get("name", ""),
                        **{k: v for k, v in user_config.items() if k != "name"}
                    }
                return config
        except FileNotFoundError:
            continue
    # No config file → default to Kite
    return {"broker": "kite"}


_broker_config = _load_broker_config()
BROKER_NAME: str = _broker_config.get("broker", "kite").lower().strip()
# Get the unique identifier for the active user (UCC for Kotak, User ID for Kite)
ACTIVE_UCC: str = _broker_config.get("kotak_ucc") or _broker_config.get("id") or "default"

print(f"[BROKER FACTORY] Active broker: {BROKER_NAME} | User: {ACTIVE_UCC}")

# ── Initialise the correct broker ───────────────────────────────────────

if BROKER_NAME == "kotak":
    # ── Kotak Neo path ──────────────────────────────────────────────────
    from .kotak_broker import KotakBroker as _BrokerClass

    _kotak_instance = _BrokerClass(_broker_config)
    broker = _kotak_instance

    # Access token is the session token after login
    access_token = _kotak_instance._session_token or _kotak_instance._access_token

    def generate_session_and_set_token(request_token_or_unused=None):
        """
        For Kotak the auth flow is consumer_key + login + 2FA.
        ``request_token`` is ignored; credentials come from broker_config.
        """
        try:
            session = _kotak_instance.generate_session("", "")
            global access_token
            access_token = session.get("access_token")
            return True, {"user_id": _broker_config.get("kotak_user_id", "KOTAK_USER")}
        except Exception as e:
            return False, str(e)

    def re_initialize_session_from_file():
        """Attempt to restore / create a Kotak session on startup."""
        print("--- Kotak: Attempting auto-login from broker_config.json ---")
        success, data = generate_session_and_set_token()
        if success:
            print(f"[OK] Kotak session established.")
        else:
            print(f"[ERROR] Kotak login failed: {data}")

    def clear_session():
        """Clear the Kotak session tokens for logout."""
        global access_token
        _kotak_instance._session_token = None
        _kotak_instance._session_sid = None
        _kotak_instance._base_url = None
        access_token = None
        print("[OK] Kotak session cleared for logout")

    def create_ticker(strategy_instance, main_loop):
        """Create a KotakTicker for real-time market data."""
        from .kotak_ticker import KotakTicker
        return KotakTicker(
            strategy_instance,
            main_loop,
            kotak_broker=_kotak_instance,
            config=_broker_config,
        )

    # Run initial auth — don't crash if it fails (user can retry)
    try:
        re_initialize_session_from_file()
    except Exception as e:
        print(f"[BROKER FACTORY] Kotak initial login failed: {e}")
        print(f"[BROKER FACTORY] Bot will start but trading requires login.")

else:
    # ── Kite (Zerodha) path — default ──────────────────────────────────
    # Import everything from the existing kite.py (which does its own
    # module-level init: loads user, patches DNS, creates KiteConnect, etc.)
    from . import kite as _kite_module
    from .kite_broker import KiteBroker as _BrokerClass

    # Wrap the existing RateLimitedKite in the BrokerInterface adapter
    broker = _BrokerClass(_kite_module.kite, _kite_module._original_kite)

    # Re-export the module-level variables the rest of the code needs
    access_token = _kite_module.access_token

    def generate_session_and_set_token(request_token):
        """Delegate to the existing kite.py helper."""
        result = _kite_module.generate_session_and_set_token(request_token)
        # Keep our module-level token in sync
        global access_token
        access_token = _kite_module.access_token
        return result

    def re_initialize_session_from_file():
        """Delegate to the existing kite.py helper."""
        _kite_module.re_initialize_session_from_file()
        global access_token
        access_token = _kite_module.access_token

    def create_ticker(strategy_instance, main_loop):
        """Create a KiteTickerManager for real-time market data."""
        from .kite_ticker_manager import KiteTickerManager
        return KiteTickerManager(strategy_instance, main_loop)

    # kite.py already calls re_initialize_session_from_file() at import time
    # so no additional call is needed here.
