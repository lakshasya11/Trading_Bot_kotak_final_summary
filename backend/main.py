import asyncio
import json
import pandas as pd
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from datetime import datetime
import os
import time
from collections import defaultdict
from fastapi.responses import RedirectResponse
import socket
import logging
import sys

# ===== WINDOWS UTF-8 ENCODING FIX =====
# Configure UTF-8 encoding for console output to handle emojis
if sys.platform == 'win32':
    try:
        import codecs
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'replace')
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')
        else:
            # Already wrapped or redirected (e.g., with Tee-Object)
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception as e:
        # Fallback: continue without UTF-8 reconfiguration
        print(f"[WARN] Could not configure UTF-8 encoding: {e}")

# ===== LOGGING SETUP =====
# Configure logging to display debug logs in console AND save to file
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s - %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),  # Console output
        logging.FileHandler('bot_debug.log', mode='a', encoding='utf-8')  # File output with UTF-8
    ]
)
logger = logging.getLogger(__name__)
logger.info("="*80)
logger.info("BOT STARTING - Debug logging enabled (console + bot_debug.log file)")
logger.info("="*80)

# ===== WINDOWS SOCKET COMPATIBILITY FIX =====
# Enable SO_REUSEADDR globally to handle lingering TIME_WAIT connections on Windows
def _socket_init_wrapper(original_socket_init):
    def new_init(self, *args, **kwargs):
        original_socket_init(self, *args, **kwargs)
        # Enable address reuse for Windows compatibility
        try:
            self.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except (OSError, AttributeError):
            pass
    return new_init

socket.socket.__init__ = _socket_init_wrapper(socket.socket.__init__)

from core.broker_factory import broker as kite, generate_session_and_set_token, access_token, BROKER_NAME
from core.websocket_manager import manager
from core.strategy import MARKET_STANDARD_PARAMS
from core.optimiser import OptimizerBot
from core.trade_logger import TradeLogger
from core.bot_service import TradingBotService, get_bot_service
from core.database import today_engine, all_engine, sql_text
from core.session_logger import SessionLogger
from core.email_notifier import EmailNotifier
from sqlalchemy import text

# ===== COOLDOWN MECHANISM =====
last_request_times = defaultdict(float)


def _get_active_user_info() -> dict:
    """Get active user info from broker config (Kotak) or user_profiles.json (Kite)."""
    if BROKER_NAME == "kotak":
        try:
            with open("broker_config.json", "r") as f:
                cfg = json.load(f)
            
            # Handle multi-user format
            if "users" in cfg:
                active_user_id = cfg.get("active_user", "user1")
                user_data = cfg["users"].get(active_user_id, {})
                return {
                    "id": user_data.get("kotak_ucc", active_user_id),
                    "name": user_data.get("name", "Kotak User"),
                    "description": f"Kotak UCC: {user_data.get('kotak_ucc', 'N/A')}",
                }
            else:
                # Old single-user format
                return {
                    "id": cfg.get("kotak_ucc", "kotak"),
                    "name": cfg.get("kotak_user_name", "Kotak User"),
                    "description": "Kotak Neo Trading Account",
                }
        except Exception as e:
            print(f"[WARNING] Failed to load Kotak user info: {e}")
            return {"id": "kotak", "name": "Kotak User", "description": ""}
    else:
        try:
            with open("user_profiles.json", "r") as f:
                data = json.load(f)
            active_id = data.get("active_user", "")
            user = next(
                (u for u in data.get("users", []) if u["id"] == active_id),
                None,
            )
            if user:
                return {
                    "id": user["id"],
                    "name": user["name"],
                    "description": user.get("description", ""),
                }
        except Exception:
            pass
        return {"id": "unknown", "name": "User", "description": ""}


def cooldown_check(endpoint: str, cooldown_seconds: float = 1.0):
    """
    Prevent rapid-fire requests to same endpoint.
    Protects against button spam and accidental double-clicks.
    """
    now = time.time()
    last_time = last_request_times[endpoint]
    
    if now - last_time < cooldown_seconds:
        remaining = cooldown_seconds - (now - last_time)
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {remaining:.1f} seconds before retrying"
        )
    
    last_request_times[endpoint] = now

# ===== AUTHENTICATION DEPENDENCY =====
def require_auth():
    """Dependency to ensure user is authenticated before trading operations"""
    from core.broker_factory import access_token as current_token, BROKER_NAME
    # Kotak uses auto-login via TOTP — session is established on start_bot
    if BROKER_NAME == "kotak":
        return True
    if not current_token:
        raise HTTPException(
            status_code=401, 
            detail="Authentication required. Please authenticate at /api/authenticate first."
        )
    return True

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Application startup...")
    TradeLogger.setup_databases()
    try:
        SessionLogger.create_table()
        print("[OK] bot_sessions table ready")
    except Exception as e:
        print(f"[ERROR] Failed to create bot_sessions table: {e}")
    
    # Cleanup any orphaned sessions from previous incomplete shutdowns
    try:
        print("Cleaning up orphaned sessions...")
        SessionLogger.cleanup_orphaned_sessions()
    except Exception as e:
        print(f"Error cleaning up orphaned sessions: {e}")
    
    # Reset bot state on startup
    service = await get_bot_service()
    service.is_running = False
    
    # Start the daily scheduled report worker (15:31 PM)
    if not service.daily_report_task:
        service.daily_report_task = asyncio.create_task(service.daily_report_worker())
        print("[OK] Daily report worker (15:31 PM) started")
    
    print("[OK] Bot ready (is_running = False)")

    # --- ADDED: Open Position Reconciliation Logic ---
    # No delay needed - WebSocket manager is ready at this point
    from core.broker_factory import access_token as current_token
    if current_token:
        try:
            print("Reconciling open positions...")
            positions = await kite.positions()  # Fixed: kite.positions() is already async
            net_positions = positions.get('net', [])
            open_mis_positions = [
                p['tradingsymbol'] for p in net_positions 
                if p.get('product') == 'MIS' and p.get('quantity') != 0
            ]
            if open_mis_positions:
                warning_message = f"Found open MIS positions at broker: {', '.join(open_mis_positions)}. Manual action may be required."
                print(f"WARNING: {warning_message}")
                # Broadcast a warning to any connected frontend
                await manager.broadcast({
                    "type": "system_warning", 
                    "payload": {
                        "title": "Open Positions Detected on Startup",
                        "message": warning_message
                    }
                })
            else:
                print("[OK] No open MIS positions found")
        except Exception as e:
            print(f"[INFO] Could not reconcile open positions (token may be invalid): {e}")
    else:
        print("[INFO] No access token - skipping position reconciliation")
    # --- END OF ADDED LOGIC ---

    yield
    print("Application shutdown...")
    
    # Stop bot if running and log logout + send email
    service = await get_bot_service()
    if service.strategy_instance or service.is_running:
        try:
            pnl = 0; total_trades = 0; wins = 0; losses = 0; gross_pnl = 0; charges = 0
            client_id = getattr(service, 'current_client_id', None) or _get_active_ucc_from_config() or 'Unknown'
            name = getattr(service, 'current_user_name', None) or _get_active_user_info().get('name', 'Unknown')
            login_time = getattr(service, 'bot_start_time', datetime.now())
            logout_time = datetime.now()
            mode = 'UNKNOWN'
            if service.strategy_instance:
                pnl = getattr(service.strategy_instance, 'daily_net_pnl', 0)
                stats = getattr(service.strategy_instance, 'performance_stats', {"winning_trades": 0, "losing_trades": 0})
                wins = stats.get("winning_trades", 0)
                losses = stats.get("losing_trades", 0)
                total_trades = wins + losses
                gross_pnl = getattr(service.strategy_instance, 'daily_gross_pnl', 0)
                charges = getattr(service.strategy_instance, 'total_charges', 0)
                trading_mode = service.strategy_instance.params.get('trading_mode', 'Paper Trading')
                mode = 'LIVE' if trading_mode == 'Live Trading' else 'PAPER'
            SessionLogger.log_logout(client_id, pnl, total_trades, wins, losses, gross_pnl, charges)
        except Exception as e:
            print(f"Failed to log/notify session end on shutdown: {e}")

    if service.ticker_manager_instance:
        try:
            await asyncio.wait_for(service.stop_bot(), timeout=15.0)
        except asyncio.TimeoutError:
            print("Bot shutdown timed out")
        except Exception as e:
            print(f"Error during bot shutdown: {e}")
    
    # Mark kite as shutting down
    if hasattr(kite, 'shutdown'):
        await kite.shutdown()
    
    print("Shutdown tasks complete.")

app = FastAPI(lifespan=lifespan)

# Add CORS middleware immediately after app creation
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/login")
async def login():
    """Redirects to Kite login page."""
    return RedirectResponse(url=kite.login_url())


class TokenRequest(BaseModel): request_token: str
class StartRequest(BaseModel): params: dict; selectedIndex: str
class WatchlistRequest(BaseModel): side: str; strike: int

@app.get("/api/health")
async def get_health():
    """🔥 Health check endpoint - returns bot status and db info"""
    service = await get_bot_service()
    
    db_status = "unknown"
    trades_count = 0
    try:
        with today_engine.connect() as conn:
            result = conn.execute(sql_text("SELECT COUNT(*) FROM trades"))
            trades_count = result.scalar()
        db_status = "healthy"
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"
    
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "bot_running": service.is_running,
        "bot_started": service.strategy_instance is not None,
        "database": {
            "status": db_status,
            "trades_today": trades_count
        },
        "uptime_seconds": time.time() - service.startup_time if hasattr(service, 'startup_time') else "unknown"
    }

@app.get("/api/status")
async def get_status():
    from core.broker_factory import access_token as current_token, BROKER_NAME
    
    # Kotak uses auto-login via TOTP — no request token flow needed
    if BROKER_NAME == "kotak":
        user_info = _get_active_user_info()
        return {"status": "authenticated", "user": user_info.get('id', 'Kotak User')}
    
    # Kite/Zerodha: check token and verify via profile call
    if current_token:
        try:
            profile = await kite.profile()
            return {"status": "authenticated", "user": profile.get('user_id')}
        except Exception:
            pass
    
    return {"status": "unauthenticated", "login_url": kite.login_url()}

@app.get("/api/debug_info")
async def get_debug_info():
    return {
        "cwd": os.getcwd(),
        "today_db": str(today_engine.url),
        "all_db": str(all_engine.url),
        "sys_path": sys.path[:5],
        "pid": os.getpid()
    }

@app.get("/api/diagnostics")
async def get_diagnostics(auth=Depends(require_auth)):
    """🔍 Diagnostic endpoint to check bot health and instrument status"""
    service = await get_bot_service()
    
    if not service.strategy_instance:
        return {
            "bot_running": False,
            "error": "Bot not started yet"
        }
    
    strategy = service.strategy_instance
    
    diagnostics = {
        "bot_running": service.is_running,
        "instruments_loaded": len(strategy.option_instruments),
        "last_used_expiry": str(strategy.last_used_expiry) if strategy.last_used_expiry else None,
        "initial_subscription_done": strategy.initial_subscription_done,
        "index_symbol": strategy.index_symbol,
        "index_price": strategy.data_manager.prices.get(strategy.index_symbol),
        "token_to_symbol_count": len(strategy.token_to_symbol),
        "lot_size": strategy.lot_size,
        "freeze_limit": strategy.freeze_limit,
        "strike_step": strategy.strike_step,
        "has_position": strategy.position is not None,
        "websocket_connected": service.ticker_manager_instance is not None and 
                              hasattr(service.ticker_manager_instance, 'ws') and 
                              service.ticker_manager_instance.ws is not None,
    }
    
    # Check if we can get strike pairs
    try:
        pairs = strategy.get_strike_pairs(count=3)
        diagnostics["strike_pairs_available"] = len(pairs)
        if pairs:
            diagnostics["sample_strike"] = pairs[0]["strike"]
            diagnostics["sample_ce_symbol"] = pairs[0]["ce"]["tradingsymbol"] if pairs[0]["ce"] else None
            diagnostics["sample_pe_symbol"] = pairs[0]["pe"]["tradingsymbol"] if pairs[0]["pe"] else None
    except Exception as e:
        diagnostics["strike_pairs_error"] = str(e)
    
    return diagnostics

@app.post("/api/authenticate")
async def authenticate(token_request: TokenRequest):
    success, data = generate_session_and_set_token(token_request.request_token)
    if success:
        return {"status": "success", "message": "Authentication successful.", "user": data.get('user_id')}
    raise HTTPException(status_code=400, detail=data)

def _get_active_ucc_from_config():
    """Get active user UCC from broker_config.json for API-level filtering."""
    try:
        with open("broker_config.json", "r") as f:
            cfg = json.load(f)
        if "users" in cfg:
            active_user_id = cfg.get("active_user", "user1")
            return cfg["users"].get(active_user_id, {}).get("kotak_ucc", active_user_id)
        return cfg.get("kotak_ucc", None)
    except Exception:
        return None

@app.get("/api/trade_history")
async def get_trade_history():
    """Get today's trades filtered by active user UCC."""
    def db_call():
        try:
            from datetime import datetime
            today_date = datetime.now().strftime("%Y-%m-%d")
            active_ucc = _get_active_ucc_from_config()
            
            with today_engine.connect() as conn:
                logger.info(f"🔍 Fetching trade history for UCC: {active_ucc}")
                if active_ucc:
                    query = sql_text("SELECT * FROM trades WHERE (ucc = :ucc OR ucc IS NULL) ORDER BY timestamp ASC")
                    df = pd.read_sql_query(query, conn, params={"ucc": active_ucc})
                else:
                    df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp ASC", conn)
                logger.info(f"✅ Found {len(df)} trades in today_engine.")
                
                if len(df) == 0:
                    logger.info(f"📋 today_engine is empty, checking all_engine for today's trades ({today_date})...")
                    with all_engine.connect() as all_conn:
                        if active_ucc:
                            query = sql_text("SELECT * FROM trades WHERE timestamp LIKE :ts AND (ucc = :ucc OR ucc IS NULL) ORDER BY timestamp ASC")
                            df = pd.read_sql_query(query, all_conn, params={"ts": f"{today_date}%", "ucc": active_ucc})
                        else:
                            query = sql_text("SELECT * FROM trades WHERE timestamp LIKE :ts ORDER BY timestamp ASC")
                            df = pd.read_sql_query(query, all_conn, params={"ts": f"{today_date}%"})
                        logger.info(f"✅ Found {len(df)} trades in all_engine for today.")
                
                records = df.to_dict('records')
                for r in records:
                    for k, v in r.items():
                        if pd.isna(v) or v == float('inf') or v == float('-inf'):
                            r[k] = None
                return records
        except Exception as e:
            logger.error(f"❌ [ERROR] Trade history fetch failed: {e}")
            return []
    return await asyncio.to_thread(db_call)

@app.get("/api/trade_history_all")
async def get_all_trade_history():
    def db_call():
        try:
            active_ucc = _get_active_ucc_from_config()
            with all_engine.connect() as conn:
                if active_ucc:
                    query = sql_text("SELECT * FROM trades WHERE (ucc = :ucc OR ucc IS NULL) ORDER BY timestamp ASC")
                    df = pd.read_sql_query(query, conn, params={"ucc": active_ucc})
                else:
                    df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp ASC", conn)
                records = df.to_dict('records')
                for r in records:
                    for k, v in r.items():
                        if pd.isna(v) or v == float('inf') or v == float('-inf'):
                            r[k] = None
                return records
        except Exception as e:
            print(f"⚠️ All-time trade history fetch: {e}")
            return []
    return await asyncio.to_thread(db_call)

@app.get("/api/performance")
async def get_performance(service: TradingBotService = Depends(get_bot_service)):
    """Get current daily performance stats (useful for manual refresh)"""
    if service.strategy_instance:
        trades_today = service.strategy_instance.performance_stats["winning_trades"] + service.strategy_instance.performance_stats["losing_trades"]
        return {
            "grossPnl": service.strategy_instance.daily_gross_pnl,
            "totalCharges": service.strategy_instance.total_charges,
            "netPnl": service.strategy_instance.daily_net_pnl,
            "net_pnl": service.strategy_instance.daily_net_pnl,  # Add snake_case alias for status bar
            "wins": service.strategy_instance.performance_stats["winning_trades"],
            "losses": service.strategy_instance.performance_stats["losing_trades"],
            "trades_today": trades_today
        }
    else:
        return {"grossPnl": 0, "totalCharges": 0, "netPnl": 0, "net_pnl": 0, "wins": 0, "losses": 0, "trades_today": 0}

@app.post("/api/optimize")
async def run_optimizer(service: TradingBotService = Depends(get_bot_service)):
    optimizer = OptimizerBot()
    new_params, justifications = await optimizer.find_optimal_parameters()
    if new_params:
        optimizer.update_strategy_file(new_params)
        if service.strategy_instance:
            await service.strategy_instance.reload_params()
            await service.strategy_instance._log_debug("Optimizer", "Live parameter reload successful.")
        return {"status": "success", "report": justifications}
    return {"status": "error", "report": justifications or ["Optimization failed."]}

@app.post("/api/reset_uoa_watchlist")
async def reset_uoa(service: TradingBotService = Depends(get_bot_service)):
    if not service.strategy_instance:
        raise HTTPException(status_code=400, detail="Bot is not running.")
    
    await service.strategy_instance.reset_uoa_watchlist()
    return {"status": "success", "message": "UOA Watchlist has been cleared."}

# --- THIS IS THE CORRECTED FUNCTION ---
@app.post("/api/update_strategy_params")
async def update_strategy_parameters(params: dict, service: TradingBotService = Depends(get_bot_service)):
    try:
        # CRITICAL FIX: Merge with existing params to preserve technical indicators
        try:
            with open("strategy_params.json", "r") as f:
                existing_params = json.load(f)
        except FileNotFoundError:
            existing_params = MARKET_STANDARD_PARAMS.copy()
        
        # Merge: Preserve existing technical params, update only what's sent from UI
        merged_params = {**MARKET_STANDARD_PARAMS, **existing_params, **params}
        
        # Step 1: Update the JSON file with merged parameters
        with open("strategy_params.json", "w") as f:
            json.dump(merged_params, f, indent=4)
        
        # Step 2: If the bot is running, tell it to reload its parameters from the file
        if service.strategy_instance:
            await service.strategy_instance.reload_params()
            await service.strategy_instance._log_debug("System", "Parameters have been updated from UI.")
            
        return {"status": "success", "message": "Parameters updated successfully.", "params": merged_params}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update parameters: {str(e)}")

@app.post("/api/reset_params")
async def reset_parameters(service: TradingBotService = Depends(get_bot_service)):
    try:
        # Step 1: Overwrite the JSON file with the market standard defaults.
        with open("strategy_params.json", "w") as f:
            json.dump(MARKET_STANDARD_PARAMS, f, indent=4)
        
        # Step 2: If the bot is running, tell it to reload its parameters from the file.
        if service.strategy_instance:
            await service.strategy_instance.reload_params()
            await service.strategy_instance._log_debug("System", "Parameters have been reset to market defaults.")
            
        return {"status": "success", "message": "Parameters reset.", "params": MARKET_STANDARD_PARAMS}
    except Exception as e:
        # The str(e) is included for better debugging if something else goes wrong.
        raise HTTPException(status_code=500, detail=f"Failed to reset parameters: {str(e)}")

@app.post("/api/start")
async def start_bot(req: StartRequest, service: TradingBotService = Depends(get_bot_service), authenticated: bool = Depends(require_auth)):
    cooldown_check("start", cooldown_seconds=2.0)
    result = await service.start_bot(req.params, req.selectedIndex)
    
    if result.get("status") == "success" and service.strategy_instance:
        try:
            user_info = _get_active_user_info()
            client_id = user_info.get('id', 'Unknown')
            name = user_info.get('name', 'Unknown User')
            service.current_client_id = client_id
            service.current_user_name = name
            service.bot_start_time = datetime.now()
            trading_mode = req.params.get('trading_mode', 'Paper Trading')
            mode = 'LIVE' if trading_mode == 'Live Trading' else 'PAPER'
            SessionLogger.log_login(client_id, name, mode)
        except Exception as e:
            print(f"Failed to log/notify session start: {e}")
    
    return result

@app.post("/api/stop")
async def stop_bot(service: TradingBotService = Depends(get_bot_service), authenticated: bool = Depends(require_auth)):
    cooldown_check("stop", cooldown_seconds=1.0)
    
    if service.strategy_instance:
        try:
            client_id = getattr(service, 'current_client_id', None) or _get_active_ucc_from_config() or 'Unknown'
            name = getattr(service, 'current_user_name', None) or _get_active_user_info().get('name', 'Unknown')
            kite_id = client_id
            trading_mode = service.strategy_instance.params.get('trading_mode', 'Paper Trading')
            mode = 'LIVE' if trading_mode == 'Live Trading' else 'PAPER'
            login_time = getattr(service, 'bot_start_time', datetime.now())
            logout_time = datetime.now()
            pnl = getattr(service.strategy_instance, 'daily_net_pnl', 0)
            wins = service.strategy_instance.performance_stats.get('winning_trades', 0)
            losses = service.strategy_instance.performance_stats.get('losing_trades', 0)
            total_trades = wins + losses
            gross_pnl = getattr(service.strategy_instance, 'daily_gross_pnl', 0)
            charges = getattr(service.strategy_instance, 'total_charges', 0)
            SessionLogger.log_logout(client_id, pnl, total_trades, wins, losses, gross_pnl, charges)
        except Exception as e:
            print(f"Failed to log/notify session end: {e}")
    
    return await service.stop_bot()

@app.post("/api/pause")
async def pause_bot(service: TradingBotService = Depends(get_bot_service), authenticated: bool = Depends(require_auth)):
    cooldown_check("pause", cooldown_seconds=1.0)
    return await service.pause_bot()

@app.post("/api/unpause")
async def unpause_bot(service: TradingBotService = Depends(get_bot_service), authenticated: bool = Depends(require_auth)):
    cooldown_check("unpause", cooldown_seconds=1.0)
    return await service.unpause_bot()

@app.post("/api/manual_exit")
async def manual_exit(service: TradingBotService = Depends(get_bot_service), authenticated: bool = Depends(require_auth)):
    cooldown_check("manual_exit", cooldown_seconds=3.0)
    return await service.manual_exit_trade()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, service: TradingBotService = Depends(get_bot_service)):
    conn_start_time = asyncio.get_event_loop().time()
    await manager.connect(websocket)
    print("Client connected. Synchronizing state...")
    
    try:
        # Send active user info immediately
        try:
            active_user_info = _get_active_user_info()
            await websocket.send_json({
                "type": "active_user_update",
                "payload": active_user_info
            })
        except Exception:
            pass

        # Send initial state synchronization
        if service.strategy_instance:
            await service.strategy_instance._update_ui_status()
            # CRITICAL FIX: Always send performance data even if zero (ensures UI shows correct state)
            await service.strategy_instance._update_ui_performance()
            await service.strategy_instance._update_ui_trade_status()
            print("State synchronization complete.")
        else:
            # Send initial state to this specific connection (not broadcast)
            try:
                await websocket.send_json({"type": "status_update", "payload": {
                    "connection": "DISCONNECTED", "mode": "NOT STARTED", "is_running": False,
                    "indexPrice": 0, "trend": "---", "indexName": "INDEX"
                }})
                print("Initial state sent to new client.")
            except Exception as send_err:
                print(f"[WARNING] Failed to send initial state: {send_err}")

        # Main message loop
        while True:
            try:
                # Set a longer timeout to accommodate trade execution (which can take 1-2 seconds)
                # Plus some buffer for network delays
                data = await asyncio.wait_for(websocket.receive_text(), timeout=300.0)
                message = json.loads(data)
                
                if message.get("type") == "ping":
                    # Update ping metadata
                    manager.update_ping_metadata(websocket)
                    # Send pong response
                    await websocket.send_text('{"type": "pong"}')
                    continue
                
                if message.get("type") == "add_to_watchlist":
                    payload = message.get("payload", {})
                    if service.strategy_instance:
                        await service.strategy_instance.add_to_watchlist(payload.get("side"), payload.get("strike"))
            
            except asyncio.TimeoutError:
                # No message received for 300 seconds - send a ping to check if client is alive
                try:
                    await asyncio.wait_for(websocket.send_text('{"type": "ping"}'), timeout=5.0)
                except asyncio.TimeoutError:
                    print("[WARNING] Ping send timeout, closing connection")
                    break
                except Exception:
                    # If we can't send ping, connection is dead
                    print("⚠️ Failed to send ping, closing connection")
                    break
            except json.JSONDecodeError as e:
                print(f"[WARNING] Invalid JSON received: {e}")
                continue
    
    except WebSocketDisconnect:
        duration = asyncio.get_event_loop().time() - conn_start_time
        print(f"WebSocket disconnected normally (duration: {duration:.1f}s)")
        await manager.disconnect(websocket)
    except RuntimeError as e:
        if "not connected" in str(e).lower():
            print(f"WebSocket closed by client")
        else:
            print(f"⚠️ WebSocket runtime error: {e}")
        await manager.disconnect(websocket)
    except Exception as e:
        duration = asyncio.get_event_loop().time() - conn_start_time
        print(f"❌ Error in websocket endpoint (duration: {duration:.1f}s): {e}")
        await manager.disconnect(websocket)

# ===== OPTION EXPIRY ENDPOINTS =====
@app.get("/api/expiries/{index_name}")
async def get_available_expiries(index_name: str, service: TradingBotService = Depends(get_bot_service)):
    """Get all available expiries for the selected index"""
    try:
        # Validate index name
        if index_name not in ['NIFTY', 'BANKNIFTY', 'SENSEX']:
            raise HTTPException(status_code=400, detail=f"Invalid index: {index_name}")
        
        from datetime import date
        from core.broker_factory import broker as kite
        import asyncio
        
        # Determine exchange
        exchange = "NFO" if index_name in ["NIFTY", "BANKNIFTY"] else "BFO"
        
        try:
            # Strategy 1: Try to use cached instruments from running strategy instance
            if service.strategy_instance and hasattr(service.strategy_instance, 'option_instruments'):
                instruments = service.strategy_instance.option_instruments
                
                # If loaded instruments are for a different index, we need to fetch fresh
                if instruments and len(instruments) > 0:
                    first_inst_index = instruments[0].get('name', '')
                    if first_inst_index == index_name:
                        # We can use these cached instruments
                        today = date.today()
                        expiries = set()
                        
                        for inst in instruments:
                            if inst.get('expiry') and inst['expiry'] >= today:
                                expiries.add(inst['expiry'])
                        
                        sorted_expiries = sorted(list(expiries))
                        formatted_expiries = [exp.strftime('%Y-%m-%d') for exp in sorted_expiries]
                        
                        return {
                            "index": index_name,
                            "expiries": formatted_expiries,
                            "count": len(formatted_expiries),
                            "source": "cached"
                        }
            
            # Strategy 2: Fetch fresh from Kite API with extended timeout
            try:
                instruments = await asyncio.wait_for(
                    kite.instruments(exchange),
                    timeout=150.0  # Extended timeout for initial load (Kotak scripmaster can be huge)
                )
                
                # Filter for the selected index and get unique expiries
                today = date.today()
                expiries = set()
                
                for inst in instruments:
                    if inst['name'] == index_name and inst.get('expiry'):
                        expiry_date = inst['expiry']
                        # Only include future expiries
                        if expiry_date >= today:
                            expiries.add(expiry_date)
                
                # Sort expiries and format as strings
                sorted_expiries = sorted(list(expiries))
                formatted_expiries = [exp.strftime('%Y-%m-%d') for exp in sorted_expiries]
                
                return {
                    "index": index_name,
                    "expiries": formatted_expiries,
                    "count": len(formatted_expiries),
                    "source": "fresh"
                }
                
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=504,
                    detail=f"Instrument loading timed out. This can happen when the broker API is slow. Try starting the bot first (it caches instruments) or try again in a moment."
                )
                
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch instruments: {str(e)}")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting expiries: {str(e)}")

# ===== USER MANAGEMENT ENDPOINTS =====
@app.get("/api/users")
async def get_users():
    """Get list of all users (without sensitive credentials)"""
    try:
        # Check broker type and load appropriate config
        if BROKER_NAME == "kotak":
            with open("broker_config.json", "r") as f:
                data = json.load(f)
            
            # Handle multi-user format
            if "users" in data:
                users = [
                    {
                        "id": user_id,
                        "name": user_data.get("name", user_id),
                        "description": f"Kotak UCC: {user_data.get('kotak_ucc', 'N/A')}"
                    }
                    for user_id, user_data in data["users"].items()
                ]
                return {
                    "users": users,
                    "active_user": data.get("active_user", "user1")
                }
            else:
                # Single user format - return as single user
                return {
                    "users": [{
                        "id": "kotak",
                        "name": data.get("kotak_user_name", "Kotak User"),
                        "description": f"Kotak UCC: {data.get('kotak_ucc', 'N/A')}"
                    }],
                    "active_user": "kotak"
                }
        else:
            # Kite/Zerodha users
            with open("user_profiles.json", "r") as f:
                data = json.load(f)
            
            users = [
                {
                    "id": u["id"],
                    "name": u["name"],
                    "description": u.get("description", "")
                }
                for u in data.get("users", [])
            ]
            
            return {
                "users": users,
                "active_user": data.get("active_user")
            }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config file not found for {BROKER_NAME} broker")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading users: {str(e)}")

@app.post("/api/users/switch/{user_id}")
async def switch_user(user_id: str):
    """Switch to a different user (requires bot restart to apply)"""
    cooldown_check("user_switch", cooldown_seconds=2.0)
    try:
        if BROKER_NAME == "kotak":
            # Handle Kotak broker config
            with open("broker_config.json", "r") as f:
                data = json.load(f)
            
            # Verify user exists
            if "users" not in data or user_id not in data["users"]:
                raise HTTPException(status_code=404, detail=f"User '{user_id}' not found in broker_config.json")
            
            user_name = data["users"][user_id].get("name", user_id)
            
            # Update active user
            data["active_user"] = user_id
            
            # Save changes
            with open("broker_config.json", "w") as f:
                json.dump(data, f, indent=2)
            
            # Broadcast user change to all connected clients
            await manager.broadcast({
                "type": "active_user_update",
                "payload": {
                    "id": user_id,
                    "name": user_name,
                    "description": f"Kotak UCC: {data['users'][user_id].get('kotak_ucc', 'N/A')}"
                }
            })
            
            return {
                "success": True,
                "message": f"User switched to: {user_name}. Please restart the bot to apply changes.",
                "active_user": user_id,
                "restart_required": True
            }
        else:
            # Handle Kite/Zerodha users
            with open("user_profiles.json", "r") as f:
                data = json.load(f)
            
            # Verify user exists
            user_exists = any(u["id"] == user_id for u in data.get("users", []))
            if not user_exists:
                raise HTTPException(status_code=404, detail=f"User '{user_id}' not found in user_profiles.json")
            
            # Get user name for response
            user = next((u for u in data["users"] if u["id"] == user_id), None)
            user_name = user["name"] if user else user_id
            
            # Update active user
            data["active_user"] = user_id
            
            # Save changes
            with open("user_profiles.json", "w") as f:
                json.dump(data, f, indent=2)
            
            return {
                "success": True,
                "message": f"User switched to: {user_name}",
                "active_user": user_id,
                "restart_required": True
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error switching user: {str(e)}")

@app.get("/api/users/active")
async def get_active_user():
    """Get details of currently active user"""
    try:
        if BROKER_NAME == "kotak":
            with open("broker_config.json", "r") as f:
                data = json.load(f)
            
            if "users" in data:
                active_user_id = data.get("active_user", "user1")
                user_data = data["users"].get(active_user_id)
                
                if not user_data:
                    raise HTTPException(status_code=404, detail="Active user not found in broker_config.json")
                
                return {
                    "id": active_user_id,
                    "name": user_data.get("name", active_user_id),
                    "description": f"Kotak UCC: {user_data.get('kotak_ucc', 'N/A')}"
                }
            else:
                # Single user format
                return {
                    "id": "kotak",
                    "name": data.get("kotak_user_name", "Kotak User"),
                    "description": f"Kotak UCC: {data.get('kotak_ucc', 'N/A')}"
                }
        else:
            with open("user_profiles.json", "r") as f:
                data = json.load(f)
            
            active_user_id = data.get("active_user")
            users = data.get("users", [])
            
            active_user = next((u for u in users if u["id"] == active_user_id), None)
            
            if not active_user:
                raise HTTPException(status_code=404, detail="Active user not found in user_profiles.json")
            
            return {
                "id": active_user["id"],
                "name": active_user["name"],
                "description": active_user.get("description", "")
            }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config file not found for {BROKER_NAME} broker")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting active user: {str(e)}")

# ===== DATABASE VIEWER FUNCTIONS =====
@app.get("/db_viewer.html")
@app.get("/db_viewer")
async def db_viewer_page():
    from fastapi.responses import FileResponse
    file_path = os.path.join(os.path.dirname(__file__), "db_viewer.html")
    return FileResponse(file_path, media_type="text/html")

@app.get("/api/sessions")
async def get_sessions_api(service: TradingBotService = Depends(get_bot_service)):
    active_ucc = _get_active_ucc_from_config()

    # If bot is running, refresh the active session's live stats before returning
    if service.is_running and active_ucc:
        await asyncio.to_thread(SessionLogger.update_active_session, active_ucc)

    def db_call():
        try:
            with today_engine.connect() as conn:
                if active_ucc:
                    query = text("SELECT * FROM bot_sessions WHERE client_id = :ucc ORDER BY login_time DESC")
                    df_sessions = pd.read_sql_query(query, conn, params={"ucc": active_ucc})
                else:
                    df_sessions = pd.read_sql_query("SELECT * FROM bot_sessions ORDER BY login_time DESC", conn)

                sessions_data = []
                for record in df_sessions.to_dict('records'):
                    clean = {}
                    for k, v in record.items():
                        try:
                            if v is None or (isinstance(v, float) and (v != v or v == float('inf') or v == float('-inf'))):
                                clean[k] = None
                            else:
                                clean[k] = v
                        except Exception:
                            clean[k] = None
                    sessions_data.append(clean)

                return sessions_data
        except Exception as e:
            print(f"Error fetching session/trade data: {e}")
            return []

    sessions = await asyncio.to_thread(db_call)

    for session in sessions:
        mode = session.get('mode')
        if not mode or mode in ['None', 'null', 'UNKNOWN']:
            session['mode'] = 'PAPER'
        else:
            session['mode'] = mode

    return sessions

@app.get("/api/signup_data")
async def get_signup_data():
    def db_call():
        try:
            with today_engine.connect() as conn:
                df = pd.read_sql_query("SELECT * FROM signup_data ORDER BY created_at DESC", conn)
                df = df.replace({float('nan'): None, float('inf'): None, float('-inf'): None})
                return df.to_dict('records')
        except Exception as e:
            print(f"Error fetching signup data: {e}")
            return []
    return await asyncio.to_thread(db_call)

# ===== KILL SWITCH STATUS ENDPOINT =====
@app.get("/api/kill_switch_status")
async def get_kill_switch_status():
    """Get current kill switch status for monitoring system health"""
    from core.kill_switch import kill_switch
    return kill_switch.get_status()

@app.post("/api/kill_switch_reset")
async def reset_kill_switch():
    """Manually reset the kill switch (use after fixing the underlying issue)"""
    from core.kill_switch import kill_switch
    kill_switch.manual_reset()
    return {"success": True, "message": "Kill switch has been manually reset"}

@app.post("/api/logout")
async def logout(service: TradingBotService = Depends(get_bot_service)):
    """Logout endpoint that stops bot, clears session, and disconnects all clients"""
    cooldown_check("logout", cooldown_seconds=2.0)
    
    try:
        print("Logout initiated - stopping bot and clearing session...")
        
        if service.ticker_manager_instance or service.strategy_instance or service.is_running:
            try:
                if service.ticker_manager_instance:
                    await service.ticker_manager_instance.stop()
                
                if service.strategy_instance and service.strategy_instance.position:
                    try:
                        await asyncio.wait_for(
                            service.strategy_instance.exit_position("User Logout"),
                            timeout=8.0
                        )
                    except asyncio.TimeoutError:
                        print("Position exit timed out during logout")
                    except Exception as e:
                        print(f"Error during position exit on logout: {e}")
                
                if service.uoa_scanner_task and not service.uoa_scanner_task.done():
                    service.uoa_scanner_task.cancel()
                if service.continuous_monitor_task and not service.continuous_monitor_task.done():
                    service.continuous_monitor_task.cancel()
                
                if service.strategy_instance:
                    try:
                        client_id = getattr(service, 'current_client_id', None) or _get_active_ucc_from_config() or 'Unknown'
                        name = getattr(service, 'current_user_name', None) or _get_active_user_info().get('name', 'Unknown')
                        trading_mode = service.strategy_instance.params.get('trading_mode', 'Paper Trading')
                        mode = 'LIVE' if trading_mode == 'Live Trading' else 'PAPER'
                        login_time = getattr(service, 'bot_start_time', datetime.now())
                        logout_time = datetime.now()
                        pnl = service.strategy_instance.daily_net_pnl
                        wins = service.strategy_instance.performance_stats.get("winning_trades", 0)
                        losses = service.strategy_instance.performance_stats.get("losing_trades", 0)
                        total_trades = wins + losses
                        gross_pnl = service.strategy_instance.daily_gross_pnl
                        charges = service.strategy_instance.total_charges
                        SessionLogger.log_logout(client_id, pnl, total_trades, wins, losses, gross_pnl, charges)
                    except Exception as e:
                        print(f"Failed to log/notify session end during logout: {e}")

                await service._cleanup_bot_state()
                service.is_running = False
            except Exception as e:
                print(f"Error stopping bot during logout: {e}")
        
        from core.broker_factory import clear_access_token
        try:
            clear_access_token()
        except Exception as e:
            print(f"Error clearing access token: {e}")
        
        await manager.broadcast({
            "type": "logout_notification",
            "payload": {"message": "Bot stopped - User logged out", "redirect_url": "http://localhost:3001"}
        })
        await manager.broadcast({
            "type": "status_update",
            "payload": {"connection": "DISCONNECTED", "mode": "NOT STARTED", "is_running": False,
                        "is_paused": False, "indexPrice": 0, "trend": "---", "indexName": "INDEX"}
        })
        
        await asyncio.sleep(1.0)
        await manager.disconnect_all()
        
        return {"status": "success", "message": "Bot stopped - Logout successful", "redirect_url": "http://localhost:3001"}
        
    except Exception as e:
        print(f"Error during logout: {e}")
        try:
            from core.broker_factory import clear_access_token
            clear_access_token()
            await manager.disconnect_all()
        except:
            pass
        return {"status": "error", "message": f"Logout completed with errors: {str(e)}", "redirect_url": "http://localhost:3001"}

if __name__ == "__main__":
    import sys
    import signal
    
    # Prevent FastAPI from reading stdin which causes premature exit
    sys.stdin = open(os.devnull, 'r')
    
    # Add signal handlers to prevent unexpected shutdown
    def signal_handler(signum, frame):
        print(f"\n[DEBUG] Received signal {signum}")
        if signum == signal.SIGINT:
            print("[INFO] Ctrl+C pressed, shutting down...")
            sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal_handler)
    
    print("[DEBUG] Starting Uvicorn server...")
    try:
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=8000,
            reload=False,
            access_log=True
        )
    except KeyboardInterrupt:
        print("[INFO] Server stopped by user")
    except Exception as e:
        print(f"[ERROR] Server crashed: {e}")
        import traceback
        traceback.print_exc()
