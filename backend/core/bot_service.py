import asyncio
from fastapi import HTTPException
from .strategy import Strategy
from .broker_factory import create_ticker, re_initialize_session_from_file, BROKER_NAME
from .websocket_manager import manager

_BROKER_LABEL = "Kotak" if BROKER_NAME == "kotak" else "Zerodha"

class TradingBotService:
    _instance = None

    def __init__(self):
        self.strategy_instance: Strategy | None = None
        self.ticker_manager_instance = None  # TickerInterface (Kite or Kotak)
        self.uoa_scanner_task: asyncio.Task | None = None
        self.continuous_monitor_task: asyncio.Task | None = None
        self.position_health_monitor_task: asyncio.Task | None = None  # 🔥 NEW: Position tick health monitor
        self.daily_report_task: asyncio.Task | None = None  # 🕒 NEW: Daily summary report task
        self.bot_lock = asyncio.Lock()
        self.scan_lock = asyncio.Lock()  # Prevent overlapping scans
        self.is_running = False

    @classmethod
    async def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def uoa_scanner_worker(self):
        while True:
            try:
                if self.strategy_instance and self.strategy_instance.params.get('auto_scan_uoa'):
                    await self.strategy_instance.scan_for_unusual_activity()
                await asyncio.sleep(300)
            except asyncio.CancelledError: break
            except Exception as e: print(f"Error in UOA scanner worker: {e}"); await asyncio.sleep(60)
    
    async def continuous_monitor_worker(self):
        """
        Independent background task that runs engine scans every 200ms.
        Fully async loop that ensures signal detection continues even if WebSocket stops.
        Runs independently and reliably regardless of tick arrival.
        """
        # Small initial delay to ensure strategy is fully initialized
        await asyncio.sleep(3)
        
        print("⚡ Continuous monitor worker started - scanning every 200ms (5x per second)")
        
        scan_count = 0
        
        while self.is_running:
            try:
                if self.strategy_instance:
                    # Try to acquire lock without blocking - skip if already scanning
                    if self.scan_lock.locked():
                        # Another scan is in progress, skip this cycle
                        if not hasattr(self, '_skip_count'):
                            self._skip_count = 0
                        self._skip_count += 1
                        if self._skip_count % 50 == 0:  # Log every 50 skips (10 seconds at 200ms)
                            print(f"⚠️ Scan lock contention: {self._skip_count} skips (scan taking too long)")
                        await asyncio.sleep(0.2)
                        continue
                    else:
                        if hasattr(self, '_skip_count'):
                            self._skip_count = 0  # Reset when lock is available
                    
                    async with self.scan_lock:
                        # Run engine scanning independently - fully async
                        try:
                            await asyncio.wait_for(
                                self.strategy_instance.check_trade_entry(),
                                timeout=2.0  # Increased from 0.9s to 2.0s to prevent gaps
                            )
                            scan_count += 1
                            
                            # Log every 1500 scans (once per 5 minutes at 5 scans/sec) to reduce spam
                            if scan_count % 1500 == 0:
                                print(f"✅ Continuous monitor: {scan_count} scans completed")
                                
                        except asyncio.TimeoutError:
                            scan_count += 1  # Count timeouts to track health
                            print(f"⚠️ Engine scan took >2s (timeout #{scan_count % 300}), continuing anyway")
                            # 🔥 CRITICAL FIX: Reset entry_in_progress if stuck after timeout
                            # When check_trade_entry() is cancelled mid-execution, entry_in_progress
                            # can be left as True, causing scan_for_signals() to silently skip forever
                            try:
                                if self.strategy_instance and self.strategy_instance.entry_in_progress:
                                    self.strategy_instance.entry_in_progress = False
                                    self.strategy_instance.entry_started_at = None
                                    print(f"🔧 Auto-reset entry_in_progress after scan timeout")
                            except Exception:
                                pass
                        except Exception as scan_error:
                            # Don't let individual scan errors stop the loop
                            error_msg = str(scan_error).lower()
                            if "paused" not in error_msg and "not running" not in error_msg:
                                print(f"⚠️ Scan error: {scan_error}")
                        
                        # Send periodic heartbeat to debug log (every 150 scans = 30 seconds)
                        if scan_count % 150 == 0 and scan_count > 0:
                            try:
                                await self.strategy_instance._log_debug(
                                    "Monitor",
                                    f"🔄 Active monitoring - {scan_count} scans completed"
                                )
                            except Exception:
                                pass  # Don't let log errors stop monitoring
                
                # Wait exactly 200ms before next scan
                await asyncio.sleep(0.2)
                
            except asyncio.CancelledError:
                print(f"🛑 Continuous monitor worker stopped ({scan_count} total scans)")
                break
            except Exception as e:
                print(f"❌ Critical error in monitor worker: {e}")
                await asyncio.sleep(2)  # Wait on critical error
    
    async def daily_report_worker(self):
        """
        Independent background task to send daily trade summary at precisely 15:31 PM.
        Runs as long as the backend server is up.
        """
        import pandas as pd
        from datetime import datetime
        from .database import today_engine, sql_text
        from .email_notifier import EmailNotifier
        import os
        
        # Helper to get info without cyclic imports if possible
        def _get_info():
            try:
                with open("broker_config.json", "r") as f:
                    import json
                    cfg = json.load(f)
                active_user_id = cfg.get("active_user", "user1")
                if "users" in cfg:
                    user_data = cfg["users"].get(active_user_id, {})
                    return user_data.get("kotak_ucc", active_user_id), user_data.get("name", "User")
                return cfg.get("kotak_ucc", "kotak"), cfg.get("kotak_user_name", "User")
            except Exception:
                return "Unknown", "User"

        last_sent_date = ""
        print("🕒 Daily report worker active - scheduled for 15:31 PM")
        
        while True:
            try:
                now = datetime.now()
                current_time = now.strftime("%H:%M")
                current_date = now.strftime("%Y-%m-%d")
                
                # Check if it's 3:31 PM (15:31) and we haven't sent a report today
                if current_time == "15:31" and last_sent_date != current_date:
                    print(f"⏰ Daily Scheduled Report - 15:31 reached. Generating report for {current_date}...")
                    
                    ucc, name = _get_info()
                    
                    trades_for_email = []
                    try:
                        with today_engine.connect() as conn:
                            # Try to fetch all today's trades
                            if ucc and ucc != "Unknown":
                                query_sql = sql_text("SELECT * FROM trades WHERE (ucc = :ucc OR ucc IS NULL) ORDER BY timestamp ASC")
                                trades_df = pd.read_sql_query(query_sql, conn, params={"ucc": ucc})
                            else:
                                trades_df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp ASC", conn)
                            
                            trades_for_email = trades_df.to_dict('records')
                            for r in trades_for_email:
                                for k, v in r.items():
                                    if pd.isna(v) or v == float('inf') or v == float('-inf'):
                                        r[k] = None
                    except Exception as db_err:
                        print(f"❌ Database error during scheduled report: {db_err}")
                    
                    # Basic summary calcs
                    total_trades = len(trades_for_email)
                    net_pnl = sum(t.get('net_pnl', 0) or 0 for t in trades_for_email)
                    
                    # Extract trading mode (LIVE/PAPER) if possible
                    mode = "REPORT"
                    if self.strategy_instance:
                        trading_mode = self.strategy_instance.params.get('trading_mode', 'Paper Trading')
                        mode = 'LIVE' if trading_mode == 'Live Trading' else 'PAPER'
                    
                    # Send the email!
                    if os.getenv('NOTIFICATION_EMAIL'):
                        wins_count = sum(1 for t in trades_for_email if (t.get('net_pnl') or 0) > 0)
                        losses_count = sum(1 for t in trades_for_email if (t.get('net_pnl') or 0) <= 0)
                        await asyncio.to_thread(
                            EmailNotifier.send_daily_summary,
                            ucc, name, ucc, mode, total_trades, net_pnl, current_date,
                            wins_count, losses_count, trades_for_email
                        )
                        print(f"✅ Daily 15:31 summary email sent to {os.getenv('NOTIFICATION_EMAIL')}")
                    else:
                        print("⚠️ No NOTIFICATION_EMAIL found - skipping daily scheduled report.")
                        
                    last_sent_date = current_date
                
                # Sleep exactly 30 seconds
                await asyncio.sleep(30)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Error in Daily Report Worker: {e}")
                await asyncio.sleep(60)
    
    async def websocket_health_worker(self):
        """
        Monitor WebSocket connection health and show prominent warnings if disconnected.
        This ensures the user is always aware if trading is not possible.
        """
        await asyncio.sleep(10)  # Initial delay to let bot stabilize
        
        print("🔍 WebSocket health monitor active - checking every 60 seconds")
        
        disconnected_count = 0
        
        while self.is_running:
            try:
                if self.ticker_manager_instance:
                    is_connected = self.ticker_manager_instance.is_connected
                    
                    if not is_connected:
                        disconnected_count += 1
                        
                        # Show increasingly urgent warnings
                        print("\n" + "="*70)
                        print(f"⚠️⚠️⚠️ WEBSOCKET DISCONNECTED ({disconnected_count} min) ⚠️⚠️⚠️")
                        print("="*70)
                        print("❌ BOT CANNOT TRADE WITHOUT LIVE MARKET DATA")
                        print("   Status: RUNNING but INACTIVE (no WebSocket ticks)")
                        print("")
                        
                        if disconnected_count == 1:
                            print("   → Auto-reconnection is active, please wait...")
                        elif disconnected_count <= 5:
                            print(f"   → Still trying to reconnect... ({disconnected_count} minutes)")
                        else:
                            print(f"   → Extended disconnection ({disconnected_count} minutes)")
                            print(f"   → Check network, firewall, or {_BROKER_LABEL} status")
                            print("   → Consider restarting the bot if issue persists")
                        
                        print("="*70 + "\n")
                        
                        # Also log to debug
                        if self.strategy_instance:
                            await self.strategy_instance._log_debug(
                                "WebSocket", 
                                f"⚠️ DISCONNECTED for {disconnected_count} min - Bot cannot trade!"
                            )
                    else:
                        # Connected - reset counter
                        if disconnected_count > 0:
                            print("\n" + "="*70)
                            print("✅ WEBSOCKET RECONNECTED - Trading Active")
                            print("="*70 + "\n")
                            disconnected_count = 0
                
                # Check every 60 seconds
                await asyncio.sleep(60)
                
            except asyncio.CancelledError:
                print("🛑 WebSocket health monitor stopped")
                break
            except Exception as e:
                print(f"❌ Error in WebSocket health monitor: {e}")
                await asyncio.sleep(60)

    async def start_bot(self, params, selected_index):
        async with self.bot_lock:
            if self.is_running:
                raise HTTPException(status_code=400, detail="Bot is already running.")
            
            # Clean up any stale instances before starting
            if self.ticker_manager_instance or self.strategy_instance:
                print("⚠️ Cleaning up stale bot instances before starting...")
                await self._cleanup_bot_state()
                self.is_running = False
            
            # CRITICAL FIX: Merge parameters with defaults and save to JSON file
            # This ensures technical indicator parameters are never lost
            import json
            from core.strategy import MARKET_STANDARD_PARAMS
            
            try:
                # Read existing params from file
                try:
                    with open("strategy_params.json", "r") as f:
                        existing_params = json.load(f)
                except FileNotFoundError:
                    existing_params = {}
                
                # Merge: Start with market defaults, overlay existing, then overlay new UI params
                merged_params = {**MARKET_STANDARD_PARAMS, **existing_params, **params}
                
                # Save merged parameters
                with open("strategy_params.json", "w") as f:
                    json.dump(merged_params, f, indent=4)
                print(f"✅ Parameters saved to file (Mode: {merged_params.get('trading_mode', 'Unknown')})")
                
                # Use merged params for bot creation
                params = merged_params
            except Exception as e:
                print(f"⚠️ Failed to save parameters to file: {e}")
            
            try:
                main_loop = asyncio.get_running_loop()
                self.strategy_instance = Strategy(params=params, manager=manager, selected_index=selected_index)
                
                # CRITICAL FIX: Reload instruments (async call, already loaded in __init__ but refresh here)
                try:
                    self.strategy_instance.option_instruments = await asyncio.wait_for(
                        self.strategy_instance.load_instruments(),
                        timeout=150.0  # 150s: allows 2 download attempts (90s + 120s) + parse
                    )
                except asyncio.TimeoutError:
                    raise Exception(f"Instrument loading timed out after 150 seconds. Check {_BROKER_LABEL} API connection.")
                
                # CRITICAL FIX: Set expiry from date string (format: YYYY-MM-DD)
                # Only accepts actual dates, not keywords like CURRENT_WEEK
                expiry_str = params.get('option_expiry_type', '')
                if expiry_str:
                    try:
                        from datetime import datetime
                        parsed_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                        self.strategy_instance.last_used_expiry = parsed_date
                        print(f"✅ Using expiry date: {expiry_str}")
                    except ValueError:
                        # Not a date — try as keyword (CURRENT_WEEK, NEXT_WEEK, MONTHLY)
                        auto_expiry = self.strategy_instance.get_selected_expiry()
                        if auto_expiry:
                            self.strategy_instance.last_used_expiry = auto_expiry
                            print(f"✅ Auto-detected expiry from '{expiry_str}': {auto_expiry}")
                        else:
                            print(f"❌ Could not resolve expiry from '{expiry_str}'. Option chain will be empty until instruments load.")
                            self.strategy_instance.last_used_expiry = None
                else:
                    print("⚠️ No expiry date provided (option_expiry_type parameter is empty)")
                    self.strategy_instance.last_used_expiry = None
                
                self.ticker_manager_instance = create_ticker(self.strategy_instance, main_loop)
                self.strategy_instance.ticker_manager = self.ticker_manager_instance
                
                # CRITICAL FIX: Add timeout to strategy.run() initialization
                try:
                    await asyncio.wait_for(self.strategy_instance.run(), timeout=8.0)  # Reduced from 10s to 8s
                except asyncio.TimeoutError:
                    raise Exception("Strategy initialization timed out after 8 seconds")

                # 🔥 CRITICAL: Start WebSocket with aggressive retry
                print(f"🚀 Starting {_BROKER_LABEL} WebSocket connection...")
                self.ticker_manager_instance.start()
                
                # Try to connect with multiple attempts
                connection_attempts = 0
                max_attempts = 3
                
                while connection_attempts < max_attempts:
                    connection_attempts += 1
                    print(f"⏳ Attempt {connection_attempts}/{max_attempts}: Waiting for WebSocket connection...")
                    
                    try:
                        await asyncio.wait_for(self.ticker_manager_instance.connected_event.wait(), timeout=10)
                        if self.ticker_manager_instance.is_connected:
                            print("✅ WebSocket connected successfully!")
                            break
                    except asyncio.TimeoutError:
                        print(f"⚠️ Attempt {connection_attempts} timed out after 10 seconds")
                        if connection_attempts < max_attempts:
                            print("   Retrying in 3 seconds...")
                            await asyncio.sleep(3)
                
                # Final check
                if not self.ticker_manager_instance.is_connected:
                    print("\n" + "="*70)
                    print("❌ CRITICAL ERROR: WebSocket Failed to Connect")
                    print("="*70)
                    print("The bot cannot trade without live market data!")
                    print("")
                    print("Possible causes:")
                    if BROKER_NAME == "kotak":
                        print("  1. Kotak Neo API server is down")
                        print("  2. Network/Firewall issue")
                    else:
                        print("  1. Zerodha WebSocket server is down")
                        print("  2. Network/Firewall blocking ws.kite.trade")
                    print("  3. Multiple connections from same account")
                    print("  4. Antivirus blocking WebSocket connections")
                    print("")
                    print("What to do:")
                    if BROKER_NAME == "kotak":
                        print("  • Check Kotak Neo service status")
                    else:
                        print("  • Check https://status.zerodha.com")
                    print("  • Restart your router")
                    print("  • Disable antivirus temporarily")
                    print("  • Check firewall settings")
                    print("="*70 + "\n")
                    raise Exception("WebSocket connection failed after 3 attempts. Bot cannot trade without live market data.")

                if not self.uoa_scanner_task or self.uoa_scanner_task.done():
                    self.uoa_scanner_task = asyncio.create_task(self.uoa_scanner_worker())
                
                # Start continuous monitoring background task (runs every 1 second independently)
                if not self.continuous_monitor_task or self.continuous_monitor_task.done():
                    self.continuous_monitor_task = asyncio.create_task(self.continuous_monitor_worker())
                    print("✅ Continuous monitoring task started")
                    print("   → Scans every 1 second (independent of WebSocket ticks)")
                    print("   → Ensures no signal detection gaps even if connection stutters")

                # 🔥 START POSITION TICK HEALTH MONITOR
                if not hasattr(self, 'position_health_monitor_task') or not self.position_health_monitor_task or self.position_health_monitor_task.done():
                    self.position_health_monitor_task = asyncio.create_task(
                        self.strategy_instance._monitor_position_tick_health()
                    )
                    print("✅ Position tick health monitor started")
                    print("   → Monitors that ticks are flowing for active positions")
                    print("   → Alerts if ticks stall for >2 seconds")
                
                # 🔥 START WEBSOCKET HEALTH MONITOR
                if not hasattr(self, 'websocket_health_monitor_task') or not self.websocket_health_monitor_task or self.websocket_health_monitor_task.done():
                    self.websocket_health_monitor_task = asyncio.create_task(self.websocket_health_worker())
                    print("✅ WebSocket health monitor started")
                    print("   → Checks WebSocket status every 60 seconds")
                    print("   → Shows prominent warnings if disconnected")

                await self.strategy_instance._update_ui_status()
                
                # Mark bot as running
                self.is_running = True
                
                print("Bot started successfully and ticker is connected.")
                return {"status": "success", "message": "Bot started and connected."}

            except asyncio.TimeoutError:
                await self._cleanup_bot_state()
                raise HTTPException(status_code=504, detail="Ticker connection timed out.")
            except Exception as e:
                await self._cleanup_bot_state()
                raise HTTPException(status_code=500, detail=str(e))

    async def stop_bot(self):
        async with self.bot_lock:
            # Allow stopping if any bot components exist (more lenient)
            if not self.ticker_manager_instance and not self.strategy_instance and not self.is_running:
                raise HTTPException(status_code=400, detail="Bot is not running.")

            try:
                print("Initiating graceful bot shutdown...")
                
                # 🛡️ WAIT FOR TRADE ENTRY: Block shutdown if trade entry is in progress
                if self.strategy_instance:
                    wait_attempts = 0
                    max_wait_attempts = 50  # Max 5 seconds (reduced from 10s)
                    while self.strategy_instance._trade_entry_in_progress and wait_attempts < max_wait_attempts:
                        await asyncio.sleep(0.1)
                        wait_attempts += 1
                    
                    if wait_attempts > 0:
                        print(f"⏳ Waited {wait_attempts * 100}ms for trade entry to complete")
                    
                    if self.strategy_instance._trade_entry_in_progress:
                        print("⚠️ Trade entry still in progress after 5s timeout - proceeding with shutdown anyway")
                
                # 🔥 CRITICAL: Flush pending broadcasts BEFORE stopping ticker
                # This ensures any in-flight trade broadcasts reach the UI
                if self.strategy_instance:
                    try:
                        await asyncio.wait_for(
                            self.strategy_instance.flush_pending_broadcasts(timeout_seconds=1.0),  # Reduced from 1.5s
                            timeout=1.5  # Reduced from 2.0s
                        )
                        print("Pending broadcasts flushed successfully.")
                    except asyncio.TimeoutError:
                        print("Broadcast flush timed out - some updates may not reach UI")
                    except Exception as e:
                        print(f"Error during broadcast flush: {e}")
                
                # 🔥 CRITICAL: Flush pending trades BEFORE stopping bot
                # This ensures all executed trades are saved to database before shutdown
                if self.strategy_instance:
                    try:
                        await asyncio.wait_for(
                            self.strategy_instance.flush_pending_trades(timeout_seconds=2.0),
                            timeout=2.5
                        )
                        print("Pending trades flushed to database successfully.")
                    except asyncio.TimeoutError:
                        print("Trade flush timed out - some trades may not be saved")
                    except Exception as e:
                        print(f"Error during trade flush: {e}")
                
                await asyncio.sleep(0.1)  # Brief delay to allow broadcasts to transmit (reduced from 0.3s)
                
                # 1. Stop ticker
                if self.ticker_manager_instance:
                    await self.ticker_manager_instance.stop()
                    print("Ticker stopped successfully.")
                
                # 2. Exit positions with timeout
                if self.strategy_instance and self.strategy_instance.position:
                    try:
                        await asyncio.wait_for(
                            self.strategy_instance.exit_position("Bot Stopped by User"),
                            timeout=8.0  # Reduced from 10s to 8s
                        )
                        print("Positions exited successfully.")
                    except asyncio.TimeoutError:
                        print("Position exit timed out after 8s - manual intervention may be required")
                    except Exception as e:
                        print(f"Error during position exit: {e}")
                
                # 3. Notify frontend clients before disconnect
                await manager.broadcast({
                    "type": "shutdown",
                    "payload": {"reason": "Bot stopped by user"}
                })
                await asyncio.sleep(0.5)  # Give clients time to process
                
                # 4. Send final disconnected status
                await manager.broadcast({"type": "status_update", "payload": {
                    "connection": "DISCONNECTED", "mode": "NOT STARTED", "is_running": False,
                    "is_paused": False, "indexPrice": 0, "trend": "---", "indexName": "INDEX"
                }})
                
                # 5. Close connections gracefully
                await manager.disconnect_all()
                
                # 6. Stop background tasks
                if self.uoa_scanner_task and not self.uoa_scanner_task.done():
                    self.uoa_scanner_task.cancel()
                    try:
                        await self.uoa_scanner_task
                    except asyncio.CancelledError:
                        pass
                    print("UOA scanner task stopped.")
                
                if self.continuous_monitor_task and not self.continuous_monitor_task.done():
                    self.continuous_monitor_task.cancel()
                    try:
                        await self.continuous_monitor_task
                    except asyncio.CancelledError:
                        pass
                    print("Continuous monitor task stopped.")
                
                # 🔥 Stop position health monitor task
                if self.position_health_monitor_task and not self.position_health_monitor_task.done():
                    self.position_health_monitor_task.cancel()
                    try:
                        await self.position_health_monitor_task
                    except asyncio.CancelledError:
                        pass
                    print("Position health monitor task stopped.")
                
                # 🔥 Stop websocket health monitor task
                if hasattr(self, 'websocket_health_monitor_task') and self.websocket_health_monitor_task and not self.websocket_health_monitor_task.done():
                    self.websocket_health_monitor_task.cancel()
                    try:
                        await self.websocket_health_monitor_task
                    except asyncio.CancelledError:
                        pass
                    print("WebSocket health monitor task stopped.")
                
                # 7. Cleanup bot state
                await self._cleanup_bot_state()
                
                # Reset state
                self.is_running = False
                
                print("Bot stopped successfully.")
                
                # --- NEW LINE ---
                # Proactively reload the token from the file to restore the session.
                re_initialize_session_from_file()

                return {"status": "success", "message": "Bot stopped successfully"}
                
            except Exception as e:
                print(f"Error during shutdown: {e}")
                # Force cleanup
                await self._cleanup_bot_state()
                self.is_running = False
                return {"status": "error", "message": f"Bot stopped with errors: {str(e)}"}

    async def pause_bot(self):
        if not self.strategy_instance:
            raise HTTPException(status_code=400, detail="Bot is not running.")
        
        self.strategy_instance.is_paused = True
        await self.strategy_instance._log_debug("System", "🚫 Bot paused. No new trades will be taken.")
        await self.strategy_instance._update_ui_status()
        return {"status": "success", "message": "Bot paused. No new trades will be taken."}

    async def unpause_bot(self):
        if not self.strategy_instance:
            raise HTTPException(status_code=400, detail="Bot is not running.")
        
        self.strategy_instance.is_paused = False
        await self.strategy_instance._log_debug("System", "✅ Bot resumed. Trading enabled.")
        await self.strategy_instance._update_ui_status()
        return {"status": "success", "message": "Bot resumed. Trading enabled."}

    async def manual_exit_trade(self):
        if not self.strategy_instance:
            raise HTTPException(status_code=400, detail="Bot is not running.")
        if not self.strategy_instance.position:
            raise HTTPException(status_code=400, detail="No active trade to exit.")
        
        await self.strategy_instance.exit_position("Manual Exit from UI")
        return {"status": "success", "message": "Manual exit signal sent."}

    async def add_to_watchlist(self, side, strike):
        if self.strategy_instance and side and strike is not None:
            await self.strategy_instance.add_to_watchlist(side, strike)

    async def _cleanup_bot_state(self):
        if self.ticker_manager_instance:
            await self.ticker_manager_instance.stop()
        if self.strategy_instance and self.strategy_instance.ui_update_task:
            self.strategy_instance.ui_update_task.cancel()
        if self.uoa_scanner_task:
            self.uoa_scanner_task.cancel()
        if self.continuous_monitor_task:
            self.continuous_monitor_task.cancel()
        # Cancel health monitor tasks to prevent leaked background tasks
        if hasattr(self, 'position_health_monitor_task') and self.position_health_monitor_task and not self.position_health_monitor_task.done():
            self.position_health_monitor_task.cancel()
        if hasattr(self, 'websocket_health_monitor_task') and self.websocket_health_monitor_task and not self.websocket_health_monitor_task.done():
            self.websocket_health_monitor_task.cancel()
        
        self.ticker_manager_instance = None
        self.strategy_instance = None
        self.uoa_scanner_task = None
        self.continuous_monitor_task = None
        self.position_health_monitor_task = None
        self.websocket_health_monitor_task = None

async def get_bot_service():
    return await TradingBotService.get_instance()