# backend/core/kite_ticker_manager.py

import asyncio
import socket
import urllib.request
import websocket
from kiteconnect import KiteTicker
from core import kite as kite_api 
from typing import TYPE_CHECKING

# DNS NOTE: DNS fallback for Kite domains is handled globally by kite.py's
# patched_getaddrinfo (tries system DNS first, falls back to hardcoded IPs).
# No duplicate override here - avoids bypassing system DNS unnecessarily.

# 🔥 WEBSOCKET CONFIGURATION FOR RELIABILITY
# Increase timeout to handle slow DNS/network, especially on WiFi
websocket.setdefaulttimeout(30)  # 30 seconds instead of default 5 (critical for WiFi stability)

if TYPE_CHECKING:
    from core.strategy import Strategy

class KiteTickerManager:
    def __init__(self, strategy_instance: "Strategy", main_loop):
        print(">>> KITE TICKER MANAGER: New instance created.")
        
        # CRITICAL FIX: Don't initialize KiteTicker in __init__ - do it in start()
        # This ensures we always use the current valid access_token
        self.kws = None
        
        self.strategy = strategy_instance
        self.main_loop = main_loop 
        self.is_connected = False
        self.is_reconnecting = False  # Guard to prevent multiple reconnection attempts
        self.manual_stop = False  # Track if user manually stopped the bot
        
        # --- ADDED: Events to signal connection status ---
        self.connected_event = asyncio.Event()
        self.disconnected_event = asyncio.Event()
        
        # 🔥 NEW: Connection health monitoring
        self.last_tick_time = None
        self.health_check_task = None
        self.consecutive_tick_failures = 0
        self.reconnection_count = 0
        # Market time sync throttling - only send when second changes
        self._last_market_sync_second = None

    def on_ticks(self, ws, ticks):
        # 🔥 Update last tick time for health monitoring
        import time
        self.last_tick_time = time.time()
        self.consecutive_tick_failures = 0  # Reset on successful tick
        
        # 🎯 CLOCK SYNC DISABLED: Clock now comes from batch_frame_update (60 FPS)
        # market_time_sync removed to prevent competing clock sources
        # The frame-based update already includes exchange-accurate timestamps
        
        if self.strategy:
            asyncio.run_coroutine_threadsafe(self.strategy.handle_ticks_async(ticks), self.main_loop)
    def subscribe(self, tokens):
        """
        Subscribes to an additional list of instrument tokens without
        unsubscribing from the existing ones.
        """
        if self.is_connected and self.kws:
            print(f"Subscribing to {len(tokens)} additional tokens.")
            self.kws.subscribe(tokens)
            self.kws.set_mode(self.kws.MODE_FULL, tokens)  # Use MODE_FULL for real-time sync with Zerodha

    def on_connect(self, ws, response):
        print(">>> KITE TICKER MANAGER: 'on_connect' callback triggered.")
        self.is_connected = True
        self.disconnected_event.clear()
        self.reconnection_count += 1
        
        # 🔥 Reset health monitoring
        import time
        self.last_tick_time = time.time()
        self.consecutive_tick_failures = 0
        
        # --- ADDED: Signal that the connection is successful ---
        self.main_loop.call_soon_threadsafe(self.connected_event.set)
        
        # 🔥 Start health check task
        if not self.health_check_task or self.health_check_task.done():
            self.health_check_task = asyncio.run_coroutine_threadsafe(
                self._connection_health_monitor(),
                self.main_loop
            )
        
        print(f"Kite Ticker connected (reconnection #{self.reconnection_count}).")
        if self.strategy:
             asyncio.run_coroutine_threadsafe(self.strategy.on_ticker_connect(), self.main_loop)

    def on_close(self, ws, code, reason):
        print(f">>> KITE TICKER MANAGER: 'on_close' callback triggered. Code: {code}, Reason: {reason}")
        self.is_connected = False
        self.connected_event.clear()
        
        # --- UPDATED: Signal that the disconnection is complete ---
        self.main_loop.call_soon_threadsafe(self.disconnected_event.set)
        
        # Only auto-reconnect if NOT manually stopped and NOT already reconnecting
        if not self.manual_stop and not self.is_reconnecting:
            # Normal codes: 1000 (normal), 1001 (going away), 1006 (abnormal)
            # Only reconnect on abnormal closes
            if code in [1006, None] or code > 1001:
                print(f">>> KITE TICKER: Abnormal disconnect (code {code}). Scheduling reconnection...")
                self.main_loop.call_soon_threadsafe(self._schedule_reconnect)
            else:
                print(f">>> KITE TICKER: Normal disconnect (code {code}). No auto-reconnect.")
        elif self.manual_stop:
            print(">>> KITE TICKER: Manual stop - no reconnection")
        else:
            print(">>> KITE TICKER: Already reconnecting - ignoring duplicate close event")
        
        if self.strategy:
             asyncio.run_coroutine_threadsafe(self.strategy.on_ticker_disconnect(), self.main_loop)

    # In kite_ticker_manager.py, enhance error handling:
    def on_error(self, ws, code, reason):
        from datetime import datetime
        print("\n" + "="*70)
        print("⚠️ KITE WEBSOCKET ERROR")
        print("="*70)
        print(f"Code: {code}")
        print(f"Reason: {reason}")
        print(f"Time: {datetime.now().strftime('%H:%M:%S')}")
        
        # 🔥 CRITICAL: Detect WinError 10051 and similar network errors
        error_str = str(reason).lower()
        is_network_error = False
        
        if isinstance(reason, OSError):
            error_code = getattr(reason, 'winerror', None) or getattr(reason, 'errno', None)
            if error_code == 10051:
                print("🔴 WinError 10051: Network unreachable")
                print("   This means WiFi disconnected or network adapter changed")
                is_network_error = True
            elif error_code in [10053, 10054, 10060, 10061]:
                print(f"🔴 Network error {error_code}: Connection issue")
                is_network_error = True
        
        if 'network' in error_str or 'unreachable' in error_str or '10051' in error_str:
            is_network_error = True
            print("🔴 Network unreachable - will attempt reconnection")
        
        print("="*70 + "\n")
        
        self.is_connected = False
        self.connected_event.clear()
        
        # Check for authentication errors
        if reason and ("token" in str(reason).lower() or "auth" in str(reason).lower() or "401" in str(reason)):
            print("\n" + "="*70)
            print("🔴 AUTHENTICATION ERROR - TOKEN INVALID/EXPIRED")
            print("="*70)
            print("Your access token is no longer valid.")
            print("Please re-authenticate through the frontend UI.")
            print("")
            print("Auto-reconnection disabled for auth errors.")
            print("="*70 + "\n")
            # Don't auto-reconnect on auth errors
            return
        
        # 🔥 CRITICAL: For network errors, force immediate cleanup and reconnection
        if is_network_error:
            print("🔧 Network error detected - forcing WebSocket cleanup...")
            try:
                if self.kws:
                    self.kws.close()
            except:
                pass
            # Reset reconnecting flag to allow fresh attempt
            self.is_reconnecting = False
        
        # Only schedule reconnection if not already reconnecting and not manually stopped
        if not self.is_reconnecting and not self.manual_stop:
            print("🔄 Scheduling automatic reconnection...")
            self.main_loop.call_soon_threadsafe(self._schedule_reconnect)
        else:
            print("ℹ️ Reconnection already in progress or manual stop")

    async def _connection_health_monitor(self):
        """Monitor connection health and detect stale connections"""
        import time
        print(">>> KITE TICKER: Health monitor started (15s interval, 45s timeout)")
        
        stuck_reconnecting_count = 0
        
        while not self.manual_stop:
            try:
                await asyncio.sleep(15)  # ⚡ FASTER: Check every 15 seconds (was 30s)
                
                # 🔥 CRITICAL: Detect stuck in reconnecting state
                if self.is_reconnecting and not self.is_connected:
                    stuck_reconnecting_count += 1
                    if stuck_reconnecting_count >= 8:  # 8 * 15s = 2 minutes stuck
                        print(f"🔴 CRITICAL: Stuck in reconnecting state for 2+ minutes!")
                        print(f"   Forcing reconnection reset...")
                        # Reset the reconnecting flag to allow fresh reconnection
                        self.is_reconnecting = False
                        stuck_reconnecting_count = 0
                        # Trigger fresh reconnection
                        self.main_loop.call_soon_threadsafe(self._schedule_reconnect)
                        continue
                else:
                    stuck_reconnecting_count = 0
                
                if not self.is_connected:
                    continue
                
                # Check if we've received ticks recently
                current_time = time.time()
                if self.last_tick_time:
                    time_since_last_tick = current_time - self.last_tick_time
                    
                    # ⚡ FASTER: If no ticks for 45 seconds during market hours, connection is stale (was 90s)
                    from datetime import datetime, time as dt_time
                    now_time = datetime.now().time()
                    is_market_hours = dt_time(9, 15) <= now_time <= dt_time(15, 30)
                    
                    if is_market_hours and time_since_last_tick > 45:
                        self.consecutive_tick_failures += 1
                        print(f">>> KITE TICKER: ⚠️ No ticks for {time_since_last_tick:.0f}s (failure #{self.consecutive_tick_failures})")
                        
                        if self.consecutive_tick_failures >= 1:  # ⚡ FASTER: React after 1 failure (was 2)
                            print(">>> KITE TICKER: 🔄 Stale connection detected, forcing reconnection...")
                            if self.strategy:
                                asyncio.run_coroutine_threadsafe(
                                    self.strategy._log_debug("WebSocket", "⚠️ No ticks for 45s - forcing reconnection..."),
                                    self.main_loop
                                )
                            # Force reconnection
                            self.is_connected = False
                            self.consecutive_tick_failures = 0  # Reset counter
                            if self.kws:
                                try:
                                    self.kws.close()
                                except:
                                    pass
                            # Trigger immediate reconnection
                            self.main_loop.call_soon_threadsafe(self._schedule_reconnect)
                            break
            except Exception as e:
                print(f">>> KITE TICKER: Health monitor error: {e}")
                import traceback
                traceback.print_exc()
                # Don't break - keep monitoring
        
        print(">>> KITE TICKER: Health monitor stopped")
    
    def _schedule_reconnect(self):
        """Schedule reconnection with exponential backoff"""
        # Guard against multiple simultaneous reconnection attempts
        if self.is_reconnecting:
            print(">>> KITE TICKER: Reconnection already in progress, ignoring duplicate request")
            return
        
        if not self.is_connected and not self.manual_stop:
            print(">>> KITE TICKER: Scheduling reconnection...")
            self.is_reconnecting = True
            # Create reconnection task in the correct event loop
            asyncio.run_coroutine_threadsafe(self._reconnect_with_backoff(), self.main_loop)
        else:
            print(f">>> KITE TICKER: Skipping reconnection (connected={self.is_connected}, manual_stop={self.manual_stop})")
    
    def _check_internet_connection(self, timeout=2):
        """
        Check if internet connection is available by testing multiple reliable hosts.
        Returns True if internet is available, False otherwise.
        
        CRITICAL: Uses shorter timeout (2s) and more lenient checks to handle
        network adapter changes (WiFi <-> LAN switching).
        """
        # Method 1: Try DNS-based checks with very short timeout
        test_hosts = [
            ("8.8.8.8", 53),      # Google DNS
            ("1.1.1.1", 53),      # Cloudflare DNS
        ]
        
        for host, port in test_hosts:
            try:
                socket.setdefaulttimeout(timeout)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((host, port))
                sock.close()
                return True
            except (socket.error, socket.timeout, OSError):
                continue
        
        # Method 2: Try HTTP request (more reliable during adapter switching)
        try:
            urllib.request.urlopen('http://www.google.com', timeout=timeout)
            return True
        except:
            pass
        
        # Method 3: Try HTTPS to Kite API directly (most important check)
        try:
            urllib.request.urlopen('https://api.kite.trade', timeout=timeout)
            return True
        except:
            pass
        
        return False

    async def _reconnect_with_backoff(self):
        """Reconnect with exponential backoff - NEVER GIVES UP until connected"""
        base_delay = 1  # Faster initial retry (1s)
        consecutive_internet_failures = 0
        consecutive_connection_failures = 0
        attempt = 0
        
        print("\n" + "="*70)
        print("🔄 WEBSOCKET RECONNECTION STARTED")
        print("="*70)
        print("Will keep trying until connection is restored...")
        print("="*70 + "\n")
        
        while not self.is_connected and not self.manual_stop:
            attempt += 1
            try:
                # OPTIMIZATION: Skip internet check after 3 consecutive failures
                if consecutive_internet_failures < 3:
                    internet_available = await asyncio.to_thread(self._check_internet_connection)
                    
                    if not internet_available:
                        consecutive_internet_failures += 1
                        print(f"⚠️ Internet check failed ({consecutive_internet_failures}/3). Waiting 3s...")
                        await asyncio.sleep(3)
                        continue
                    else:
                        consecutive_internet_failures = 0
                        print(f"✓ Internet connection verified")
                
                # 🔥 CRITICAL: Force cleanup of stuck WebSocket after multiple failures
                if consecutive_connection_failures >= 3:
                    print(f"🔧 {consecutive_connection_failures} connection failures - forcing cleanup...")
                    try:
                        if self.kws:
                            self.kws.close()
                            await asyncio.sleep(1)
                    except:
                        pass
                    # Create fresh KiteTicker instance
                    self.kws = KiteTicker(kite_api.API_KEY, kite_api.access_token)
                    self.kws.reconnect_max_tries = 10
                    self.kws.reconnect_max_delay = 60
                    self.kws.on_ticks = self.on_ticks
                    self.kws.on_connect = self.on_connect
                    self.kws.on_close = self.on_close
                    self.kws.on_error = self.on_error
                    consecutive_connection_failures = 0
                    print(f"✅ Fresh KiteTicker instance created")
                
                # Exponential backoff with max 30 seconds
                if attempt == 1:
                    delay = 0  # Immediate first attempt
                else:
                    delay = min(base_delay * (2 ** (min(attempt - 1, 5))), 30)  # Max 30 seconds
                
                if delay > 0:
                    print(f"⏳ Reconnection attempt #{attempt} in {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    print(f"⏳ Reconnection attempt #{attempt} immediately...")
                
                # Attempt to reconnect
                if kite_api.access_token and not self.is_connected:
                    print(f"🔌 Connecting to Kite WebSocket...")
                    try:
                        self.kws.connect(threaded=True)
                        
                        # Wait to see if connection succeeded
                        await asyncio.sleep(2)
                        
                        if self.is_connected:
                            print("\n" + "="*70)
                            print(f"✅ RECONNECTION SUCCESSFUL after {attempt} attempt(s)")
                            print("="*70 + "\n")
                            self.is_reconnecting = False
                            consecutive_connection_failures = 0
                            if self.strategy:
                                asyncio.run_coroutine_threadsafe(
                                    self.strategy._log_debug("WebSocket", f"✅ Auto-reconnected after {attempt} attempt(s)"),
                                    self.main_loop
                                )
                            return
                        else:
                            consecutive_connection_failures += 1
                            print(f"❌ Attempt #{attempt} failed (failure #{consecutive_connection_failures})")
                    
                    except OSError as e:
                        # 🔥 CRITICAL: Handle WinError 10051 and similar network errors
                        error_code = getattr(e, 'winerror', None) or getattr(e, 'errno', None)
                        if error_code == 10051:
                            consecutive_connection_failures += 1
                            print(f"🔴 WinError 10051: Network unreachable (failure #{consecutive_connection_failures})")
                            print(f"   This usually means WiFi disconnected or network adapter changed")
                            print(f"   Waiting longer before retry...")
                            await asyncio.sleep(5)  # Wait longer for network to stabilize
                        elif error_code in [10060, 10061, 10053, 10054]:
                            consecutive_connection_failures += 1
                            print(f"🔴 Network error {error_code}: {e} (failure #{consecutive_connection_failures})")
                            await asyncio.sleep(3)
                        else:
                            consecutive_connection_failures += 1
                            print(f"❌ Connection attempt #{attempt} failed with OSError: {e}")
                    
            except Exception as e:
                consecutive_connection_failures += 1
                print(f"❌ Reconnection attempt #{attempt} failed: {type(e).__name__} - {e}")
        
        # If manual stop was triggered, exit
        print("\n⚠️ WebSocket reconnection stopped (manual stop triggered)\n")
        self.is_reconnecting = False

    def start(self):
        """
        Initiates the connection in a background thread. This method is non-blocking.
        """
        print("\n" + "="*70)
        print("🚀 STARTING KITE WEBSOCKET CONNECTION")
        print("="*70)
        print("This is CRITICAL for trading - bot cannot work without it!")
        print("Connecting to: wss://ws.kite.trade")
        print("="*70 + "\n")
        self.manual_stop = False  # Clear manual stop flag when starting
        
        if not self.is_connected and kite_api.access_token:
            # SKIP DIAGNOSTICS - they cause DNS issues in async context
            # KiteTicker has its own robust connection mechanism
            print("\n" + "="*70)
            print("🚀 KITE TICKER CONNECTION")
            print("="*70)
            
            # 🌐 PRE-CONNECTION NETWORK CHECK (Especially critical for WiFi)
            # Wait for network to be fully ready before attempting WebSocket connection
            print("🔍 Checking network connectivity before WebSocket connection...")
            network_ready = False
            for check_attempt in range(1, 4):  # 3 attempts
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(5)
                    result = sock.connect_ex(('ws.kite.trade', 443))
                    sock.close()
                    if result == 0:
                        network_ready = True
                        print(f"✅ Network ready for WebSocket connection")
                        break
                    else:
                        print(f"⚠️ Network not ready (error {result}), attempt {check_attempt}/3. Waiting 3s...")
                        import time
                        time.sleep(3)
                except Exception as e:
                    print(f"⚠️ Network check failed: {e}, attempt {check_attempt}/3. Waiting 3s...")
                    import time
                    time.sleep(3)
            
            if not network_ready:
                print("⚠️ Network check incomplete, but proceeding with WebSocket connection...")
            
            # CRITICAL FIX: Create fresh KiteTicker instance with current valid access_token
            # KiteTicker uses websocket-client library which has its own DNS resolution
            
            # 🔥 Pre-resolve WebSocket domain to ensure DNS fallback works
            try:
                ws_test = socket.getaddrinfo('ws.kite.trade', 443, socket.AF_INET, socket.SOCK_STREAM)
                print(f">>> KITE TICKER: WebSocket DNS resolved: {ws_test[0][4][0]}")
            except Exception as e:
                print(f">>> KITE TICKER: WebSocket DNS resolution issue: {e}")
                print(f">>> KITE TICKER: DNS fallback should activate automatically...")
            
            print(f">>> KITE TICKER MANAGER: Creating new KiteTicker with token: {kite_api.access_token[:20]}...")
            self.kws = KiteTicker(kite_api.API_KEY, kite_api.access_token)
            
            # 🔧 BALANCED RECONNECTION: Prevent rate limiting while allowing recovery
            # Too many attempts (50) triggers Zerodha's anti-spam protection (HTTP 429)
            # Balanced approach: 10 attempts with 60s max delay = up to 10 minutes recovery time
            self.kws.reconnect_max_tries = 10  # Was: 50 (caused rate limiting)
            self.kws.reconnect_max_delay = 60  # Max delay between retries (seconds) - spaced out to avoid spam
            
            # Set up callbacks
            self.kws.on_ticks = self.on_ticks
            self.kws.on_connect = self.on_connect
            self.kws.on_close = self.on_close
            self.kws.on_error = self.on_error
            
            # Clear the event before attempting to connect
            self.connected_event.clear()
            print(">>> KITE TICKER MANAGER: Calling connect(threaded=True)...")
            print(f">>> KITE TICKER MANAGER: WebSocket URL will be: wss://ws.kite.trade/?api_key={kite_api.API_KEY[:10]}...&access_token=***")
            
            try:
                # CRITICAL: Add debugging to see if connect() actually starts
                import threading
                active_threads_before = threading.active_count()
                print(f">>> KITE TICKER MANAGER: Active threads BEFORE connect: {active_threads_before}")
                
                self.kws.connect(threaded=True)
                
                # Give thread a moment to start
                import time
                time.sleep(0.5)
                
                active_threads_after = threading.active_count()
                print(f">>> KITE TICKER MANAGER: Active threads AFTER connect: {active_threads_after}")
                print(f">>> KITE TICKER MANAGER: connect() call completed")
                
                if active_threads_after <= active_threads_before:
                    print(f"⚠️  WARNING: No new thread created! KiteTicker may not be running.")
                else:
                    print(f"✅ New thread created for WebSocket connection")
                    
            except Exception as e:
                print(f">>> KITE TICKER MANAGER: EXCEPTION during connect(): {type(e).__name__} - {str(e)}")
                import traceback
                traceback.print_exc()
                self.is_connected = False
                self.connected_event.clear()
                raise
        else:
            if self.is_connected:
                print(">>> KITE TICKER MANAGER: Already connected, skipping start")
            elif not kite_api.access_token:
                print(">>> KITE TICKER MANAGER: ERROR - No access token available!")

    async def stop(self):
        """
        Stops the WebSocket connection and waits for confirmation of disconnection.
        """
        print(">>> KITE TICKER MANAGER: 'stop' method called.")
        self.manual_stop = True  # Set flag to prevent auto-reconnect
        
        # 🔥 Stop health monitor
        if self.health_check_task and not self.health_check_task.done():
            self.health_check_task.cancel()
            self.health_check_task = None
        
        if self.is_connected and self.kws:
            self.disconnected_event.clear()
            self.kws.close()
            try:
                print(">>> KITE TICKER MANAGER: Waiting for disconnection confirmation...")
                await asyncio.wait_for(self.disconnected_event.wait(), timeout=7.0)
                print(">>> KITE TICKER MANAGER: Disconnection confirmed by event.")
            except asyncio.TimeoutError:
                print(">>> KITE TICKER MANAGER: Warning: Timed out waiting for ticker to close.")
            finally:
                self.kws = None
        else:
            print(">>> KITE TICKER MANAGER: 'stop' called, but not connected.")
            
    def _prewarm_dns(self):
        """Pre-warm DNS cache to avoid connection failures"""
        import socket
        import time
        
        print("🔥 Pre-warming DNS cache...")
        
        # Wait a moment for network stack to be fully ready
        time.sleep(1.0)
        
        hosts_to_resolve = ["ws.kite.trade", "api.kite.trade", "kite.zerodha.com"]
        
        # Known fallback IPs for Kite WebSocket (Cloudflare CDN)
        fallback_ips = {
            "ws.kite.trade": ["104.16.33.50", "104.16.34.50"]
        }
        
        for host in hosts_to_resolve:
            resolved = False
            for attempt in range(5):  # More attempts
                try:
                    ip = socket.gethostbyname(host)
                    print(f"   ✅ {host} -> {ip}")
                    resolved = True
                    break
                except socket.gaierror:
                    if attempt < 4:
                        time.sleep(0.5)  # Longer delay between attempts
                    else:
                        if host in fallback_ips:
                            print(f"   ⚠️  {host} -> DNS failed, using fallback IP: {fallback_ips[host][0]}")
                        else:
                            print(f"   ⚠️  {host} -> DNS failed")
        
        # Final check: Ensure at least ws.kite.trade can be resolved or fallback is available
        try:
            socket.gethostbyname("ws.kite.trade")
        except socket.gaierror:
            print(f"   🔧 DNS still failing. KiteTicker will use its internal DNS resolution.")
        
        print()
    
    def _run_network_diagnostics(self):
        """Run comprehensive network diagnostics before connecting"""
        print("\n" + "="*70)
        print("NETWORK DIAGNOSTICS")
        print("="*70)
        
        # Check DNS resolution with retry and DNS flush
        try:
            import socket
            kite_ws_host = "ws.kite.trade"
            
            # Try DNS resolution with retry
            for attempt in range(3):
                try:
                    ip = socket.gethostbyname(kite_ws_host)
                    print(f"✅ DNS Resolution: {kite_ws_host} -> {ip}")
                    break
                except socket.gaierror as e:
                    if attempt < 2:
                        print(f"⚠️  DNS Resolution attempt {attempt + 1} failed, retrying...")
                        import time
                        time.sleep(0.5)
                    else:
                        print(f"❌ DNS Resolution FAILED after 3 attempts: {e}")
                        print(f"   Trying to flush DNS cache...")
                        # Attempt to use alternative DNS resolution
                        try:
                            import subprocess
                            subprocess.run(["ipconfig", "/flushdns"], 
                                         capture_output=True, timeout=5)
                            print(f"   DNS cache flushed. Retrying...")
                            ip = socket.gethostbyname(kite_ws_host)
                            print(f"✅ DNS Resolution (after flush): {kite_ws_host} -> {ip}")
                        except:
                            print(f"   ⚠️  Still failing. Using fallback IPs: 104.16.33.50 or 104.16.34.50")
        except Exception as e:
            print(f"❌ DNS Resolution check error: {e}")
        
        # Check proxy settings
        try:
            import os
            http_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
            https_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
            if http_proxy or https_proxy:
                print(f"⚠️  PROXY DETECTED:")
                if http_proxy:
                    print(f"   HTTP_PROXY: {http_proxy}")
                if https_proxy:
                    print(f"   HTTPS_PROXY: {https_proxy}")
                print(f"   WebSocket connections may fail with proxy!")
            else:
                print("✅ No proxy detected")
        except Exception as e:
            print(f"⚠️  Proxy check failed: {e}")
        
        # Test WebSocket connectivity
        try:
            import socket
            ws_host = "ws.kite.trade"
            ws_port = 443  # Kite uses WSS (WebSocket Secure)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((ws_host, ws_port))
            sock.close()
            if result == 0:
                print(f"✅ WebSocket Port {ws_port} is reachable")
            else:
                print(f"❌ WebSocket Port {ws_port} is BLOCKED (Error code: {result})")
                print(f"   This is why connection fails on WiFi/LAN!")
                print(f"   Check: Firewall, Router settings, Antivirus")
        except Exception as e:
            print(f"❌ WebSocket connectivity test FAILED: {e}")
        
        # Check active network adapter
        try:
            import psutil
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            print("\n📡 Active Network Adapters:")
            for interface_name, interface_addresses in addrs.items():
                if interface_name in stats and stats[interface_name].isup:
                    for address in interface_addresses:
                        if str(address.family) == 'AddressFamily.AF_INET':
                            print(f"   • {interface_name}: {address.address}")
        except ImportError:
            print("   (Install psutil for detailed network info: pip install psutil)")
        except Exception as e:
            print(f"   Network adapter check failed: {e}")
        
        print("="*70 + "\n")
    
    def resubscribe(self, tokens):
        """
        Subscribes to a list of instrument tokens.
        """
        if self.is_connected and self.kws:
            print(f"Resubscribing to {len(tokens)} instrument tokens.")
            self.kws.subscribe(tokens)
            self.kws.set_mode(self.kws.MODE_FULL, tokens)  # Use MODE_FULL for real-time sync with Zerodha