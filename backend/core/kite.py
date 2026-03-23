import os
import json
import socket
from datetime import datetime
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

load_dotenv()

# 🌐 DNS RESOLUTION HELPER WITH FALLBACK
def resolve_with_fallback(hostname):
    """
    Resolve hostname using multiple DNS servers as fallback.
    Handles cases where LAN/WiFi DNS blocks trading domains.
    """
    # Known Kite API IPs (Cloudflare CDN) - Updated Dec 2025
    fallback_ips = {
        "api.kite.trade": "104.16.33.50",
        "ws.kite.trade": "104.16.33.50"
    }
    
    # Try 1: System DNS
    try:
        ip = socket.gethostbyname(hostname)
        print(f"[DNS] Resolved {hostname} → {ip}")
        return ip
    except socket.gaierror as e:
        print(f"[DNS] System DNS failed for {hostname}: {e}")
    
    # Try 2: Use fallback IPs directly
    if hostname in fallback_ips:
        ip = fallback_ips[hostname]
        print(f"[DNS FALLBACK] Using hardcoded IP: {hostname} → {ip}")
        return ip
    
    # Last resort: return hostname as-is and let connection fail naturally
    print(f"[DNS WARNING] No fallback available for {hostname}")
    return hostname

# ===== MULTI-USER SUPPORT =====
def load_active_user():
    """Load active user from user_profiles.json"""
    # Try multiple paths for user_profiles.json
    possible_paths = [
        "user_profiles.json",  # If running from backend/
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "user_profiles.json"),  # ../user_profiles.json
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backend", "user_profiles.json")  # Root/backend/
    ]
    
    data = None
    for path in possible_paths:
        try:
            with open(path, "r") as f:
                data = json.load(f)
                break
        except FileNotFoundError:
            continue
    
    if not data:
        print("[WARNING] user_profiles.json not found. Using .env credentials as fallback.")
        return None
    
    try:
        
        active_user_id = data.get("active_user")
        users = data.get("users", [])
        
        # Find active user
        active_user = next((u for u in users if u["id"] == active_user_id), None)
        
        if active_user:
            print(f"[OK] Loading user profile: {active_user['name']} (ID: {active_user['id']})")
            return active_user
        else:
            print(f"[WARNING] Active user '{active_user_id}' not found in user_profiles.json")
            return None
    except FileNotFoundError:
        print("[WARNING] user_profiles.json not found. Using .env credentials as fallback.")
        return None
    except Exception as e:
        print(f"[WARNING] Error loading user profiles: {e}")
        return None

# 🌐 PATCH SOCKET FOR AUTOMATIC DNS FALLBACK
_original_getaddrinfo = socket.getaddrinfo

def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """
    Patched getaddrinfo that uses custom DNS fallback for Kite domains.
    This makes the bot work on any network (WiFi/LAN/Hotspot).
    """
    if host in ["api.kite.trade", "ws.kite.trade"]:
        try:
            # Try original resolution first
            result = _original_getaddrinfo(host, port, family, type, proto, flags)
            print(f"[DNS] System DNS successful for {host}")
            return result
        except socket.gaierror as e:
            # DNS failed, use our fallback
            print(f"[DNS FALLBACK] System DNS failed for {host}, using hardcoded IP...")
            resolved_ip = resolve_with_fallback(host)
            if resolved_ip != host:  # Successfully resolved
                try:
                    return _original_getaddrinfo(resolved_ip, port, family, type, proto, flags)
                except Exception as e2:
                    print(f"[DNS ERROR] Fallback IP connection failed: {e2}")
                    raise e  # Re-raise original error
            raise e  # Re-raise if fallback also failed
    else:
        return _original_getaddrinfo(host, port, family, type, proto, flags)

# Apply the patch
socket.getaddrinfo = patched_getaddrinfo
print("[OK] DNS fallback enabled for Kite API domains (no external dependencies)")

# Try to load active user, fallback to .env
active_user = load_active_user()

if active_user:
    # Use credentials from user_profiles.json
    API_KEY = active_user.get("api_key")
    API_SECRET = active_user.get("api_secret")
    ACTIVE_USER_ID = active_user.get("id")
    ACTIVE_USER_NAME = active_user.get("name")
    print(f"[INFO] Using API credentials from user profile: {active_user['name']}")
else:
    # Fallback to .env file (backward compatibility)
    API_KEY = os.getenv("API_KEY")
    API_SECRET = os.getenv("API_SECRET")
    ACTIVE_USER_ID = None
    ACTIVE_USER_NAME = "default"
    print("[INFO] Using API credentials from .env file")

# ===== CONFIGURE KITE WITH RETRY LOGIC AND TIMEOUTS =====
# This prevents "Max retries exceeded" errors on slow/unstable networks
kite = KiteConnect(api_key=API_KEY)

# Configure retry strategy: retry on connection errors, timeouts, and 5xx errors
retry_strategy = Retry(
    total=5,  # Retry up to 5 times
    backoff_factor=1,  # Wait 1s, 2s, 4s, 8s, 16s between retries
    status_forcelist=[429, 500, 502, 503, 504],  # Retry on these HTTP status codes
    allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]  # Retry on all methods
)

# Create custom session with retry logic
session = requests.Session()
adapter = HTTPAdapter(
    max_retries=retry_strategy,
    pool_connections=10,
    pool_maxsize=20
)
session.mount("http://", adapter)
session.mount("https://", adapter)

# Set default timeout for all requests (connect timeout, read timeout)
session.request = lambda *args, **kwargs: requests.Session.request(
    session, *args, **{**kwargs, 'timeout': kwargs.get('timeout', (10, 30))}
)

# Inject the custom session into KiteConnect
kite.session = session
print("[OK] KiteConnect configured with retry logic (5 retries, exponential backoff) and 30s timeout")

access_token = None

DEV_MODE_ENABLED = False
DEV_ACCESS_TOKEN = "PASTE_YOUR_VALID_ACCESS_TOKEN_HERE" 

def get_access_token_file():
    """Get the access token file path for the current user"""
    if ACTIVE_USER_ID:
        return f"access_token_{ACTIVE_USER_ID}.json"
    else:
        return "access_token.json"  # Fallback for .env users

def save_access_token(session_data):
    """Save session data for the current user"""
    token_file = get_access_token_file()
    
    # Create a copy and add metadata (don't modify the original session object)
    data_to_save = {}
    for key, value in session_data.items():
        # Convert datetime objects to strings
        if hasattr(value, 'isoformat'):
            data_to_save[key] = value.isoformat()
        else:
            data_to_save[key] = value
    
    data_to_save["date"] = datetime.now().strftime("%Y-%m-%d")
    data_to_save["user_id"] = ACTIVE_USER_ID
    data_to_save["user_name"] = ACTIVE_USER_NAME

    with open(token_file, "w") as f:
        json.dump(data_to_save, f, indent=2)
    print(f"[OK] Session data saved for user: {ACTIVE_USER_NAME} ({token_file})")

def load_session_data():
    """Load session data for the current user"""
    token_file = get_access_token_file()
    try:
        with open(token_file, "r") as f:
            data = json.load(f)
            stored_user_id = data.get("user_id")
            
            # Verify the session belongs to the active user
            if stored_user_id == ACTIVE_USER_ID or (stored_user_id is None and ACTIVE_USER_ID is None):
                print(f"[OK] Loaded session data for user: {ACTIVE_USER_NAME} ({token_file})")
                return data
            else:
                print(f"[WARNING] Session file is for a different user. Expected: {ACTIVE_USER_ID}, Found: {stored_user_id}")
    except FileNotFoundError:
        print(f"[INFO] No session file found for user: {ACTIVE_USER_NAME} ({token_file})")
    except Exception as e:
        print(f"[WARNING] Error loading session data: {e}")
    return None

def set_access_token(token):
    global access_token
    if not token: 
        access_token = None
        return False, "Token is null or empty."
    try:
        # Use the original kite instance for synchronous authentication
        _original_kite.set_access_token(token)
        profile = _original_kite.profile()
        access_token = token
        print(f"Kite connection verified for user: {profile['user_id']}")
        return True, profile
    except Exception as e:
        error_message = f"Error setting access token: {e}"
        print(error_message)
        access_token = None
        return False, str(e)

def generate_session_and_set_token(request_token):
    try:
        # Use the original kite instance for synchronous authentication
        session = _original_kite.generate_session(request_token, api_secret=API_SECRET)
        save_access_token(session)  # Save the entire session
        
        # Set the access token for the current instance
        token = session.get("access_token")
        return set_access_token(token)
    except Exception as e:
        error_message = f"Authentication failed: {e}"
        print(error_message)
        return False, str(e)

# --- AUTO-LOGIN INTEGRATION ---
def attempt_auto_login():
    """
    Attempt automatic login using stored credentials
    Returns: (success, message)
    """
    try:
        from .auto_login import check_auto_login_available, ZerodhaAutoLogin
        
        if not active_user:
            print("[INFO] No active user profile found. Skipping auto-login.")
            return False, "No user profile"
        
        if not check_auto_login_available(active_user):
            print("[INFO] Auto-login not configured. Missing credentials in user_profiles.json")
            print("[INFO] Please fill user_id, password, and totp_secret for automatic login.")
            return False, "Missing credentials"
        
        print(f"[AUTO-LOGIN] Starting automatic login for {active_user['name']}...")
        auto_login = ZerodhaAutoLogin(active_user)
        success, data = auto_login.login_and_generate_session(generate_session_and_set_token)
        
        if success:
            print(f"[AUTO-LOGIN] ✅ Successfully logged in as {active_user['name']}")
            return True, "Auto-login successful"
        else:
            print(f"[AUTO-LOGIN] ❌ Failed: {data}")
            return False, data
            
    except ImportError as e:
        print(f"[AUTO-LOGIN] Cannot import auto_login module: {e}")
        print("[INFO] Run: pip install selenium pyotp")
        return False, "Missing dependencies"
    except Exception as e:
        print(f"[AUTO-LOGIN] Error: {e}")
        return False, str(e)

# --- NEW REUSABLE FUNCTION ---
def re_initialize_session_from_file():
    """
    Loads session data from the file, validates the access token,
    and attempts to refresh it if it has expired.
    If no valid session exists, attempts auto-login if configured.
    """
    print("--- Attempting to initialize session from file... ---")
    session_data = load_session_data()
    
    if not session_data:
        print("--- No session file found. ---")
        print("--- Attempting auto-login... ---")
        success, message = attempt_auto_login()
        if success:
            return  # Auto-login successful, session is set
        else:
            print(f"--- Auto-login failed: {message} ---")
            print("--- Please log in manually to generate session. ---")
            return

    access_token = session_data.get("access_token")
    refresh_token = session_data.get("refresh_token")
    
    if not access_token:
        print("--- Session file is invalid (missing access_token). Please log in again. ---")
        return

    try:
        # 1. Try to use the existing access token
        print("[INFO] Validating stored access token...")
        _original_kite.set_access_token(access_token)
        profile = _original_kite.profile()  # This call will fail if the token is invalid
        print(f"[OK] Access token is valid. Welcome, {profile['user_name']}!")
        # The global access_token is already set by set_access_token,
        # but we ensure it's explicitly updated here for clarity.
        set_access_token(access_token)
        return

    except TokenException as e:
        print(f"[WARNING] Access token has expired or is invalid: {e}. Attempting to refresh...")
        
        if not refresh_token:
            print("[INFO] No refresh_token found. Attempting auto-login...")
            success, message = attempt_auto_login()
            if success:
                print("[OK] Auto-login successful (no refresh token)")
            else:
                print(f"[ERROR] Auto-login failed: {message}")
                print("[ACTION] Please perform a full manual login.")
            return
            
        try:
            # 2. If it fails, use the refresh token to get a new session
            print("[INFO] Attempting to generate a new session using refresh_token...")
            new_session = _original_kite.generate_session(refresh_token, api_secret=API_SECRET)
            
            # 3. Save the new session data
            save_access_token(new_session)
            
            # 4. Set the new access token for the current instance
            new_access_token = new_session.get("access_token")
            set_access_token(new_access_token)
            print("[OK] Successfully generated and set a new access token.")

        except Exception as refresh_e:
            print(f"[ERROR] Failed to refresh token: {refresh_e}")
            print("[ACTION] Attempting auto-login as fallback...")
            success, message = attempt_auto_login()
            if success:
                print("[OK] Auto-login successful after refresh failure")
            else:
                print(f"[ERROR] Auto-login also failed: {message}")
                print("[ACTION] Please perform a full manual login.")
            
    except Exception as ex:
        print(f"[ERROR] An unexpected error occurred during session initialization: {ex}")
        print("[ACTION] Attempting auto-login as fallback...")
        success, message = attempt_auto_login()
        if success:
            print("[OK] Auto-login successful after error")
        else:
            print(f"[ERROR] Auto-login also failed: {message}")
            print("[ACTION] Please perform a full manual login.")


# ===== RATE LIMITED KITE WRAPPER (ZERODHA COMPLIANCE) =====
import asyncio
from .rate_limiter import api_rate_limiter, order_rate_limiter

class RateLimitedKite:
    """
    Wrapper around KiteConnect that enforces Zerodha API rate limits.
    
    Automatically rate-limits ALL Kite API calls to comply with:
    - 3 requests per second (general API)
    - 10 orders per second (order placement)
    
    Your existing code works unchanged - just uses 'kite' as normal.
    """
    
    def __init__(self, kite_instance):
        self._kite = kite_instance
        self._shutting_down = False
    
    async def _call_api(self, method, *args, **kwargs):
        """Rate-limited general API call (3 req/s) with timeout and cancellation protection"""
        try:
            # Only acquire rate limit if not shutting down
            if not self._shutting_down:
                await api_rate_limiter.acquire()
            
            # Execute with timeout
            result = await asyncio.wait_for(
                asyncio.to_thread(method, *args, **kwargs),
                timeout=30.0  # 30 second timeout
            )
            return result
            
        except asyncio.CancelledError:
            print("API call cancelled during shutdown")
            raise
        except asyncio.TimeoutError:
            print(f"API call timeout: {method.__name__}")
            raise Exception(f"API timeout: {method.__name__}")
    
    async def _call_order_api(self, method, *args, **kwargs):
        """Rate-limited order API call (10 orders/s) with timeout and cancellation protection"""
        try:
            # Only acquire rate limit if not shutting down
            if not self._shutting_down:
                await order_rate_limiter.acquire()
            
            # Execute with timeout
            result = await asyncio.wait_for(
                asyncio.to_thread(method, *args, **kwargs),
                timeout=30.0  # 30 second timeout
            )
            return result
            
        except asyncio.CancelledError:
            print("Order API call cancelled during shutdown")
            raise
        except asyncio.TimeoutError:
            print(f"Order API call timeout: {method.__name__}")
            raise Exception(f"Order API timeout: {method.__name__}")

    async def shutdown(self):
        """Mark kite as shutting down"""
        self._shutting_down = True
    
    # Wrap commonly used methods with rate limiting
    async def positions(self):
        return await self._call_api(self._kite.positions)
    
    async def profile(self):
        return await self._call_api(self._kite.profile)
    
    async def margins(self):
        return await self._call_api(self._kite.margins)
    
    async def orders(self):
        return await self._call_api(self._kite.orders)
    
    async def order_history(self, order_id):
        return await self._call_api(self._kite.order_history, order_id=order_id)
    
    async def place_order(self, **kwargs):
        return await self._call_order_api(self._kite.place_order, **kwargs)
    
    async def modify_order(self, variety, order_id, **kwargs):
        return await self._call_order_api(self._kite.modify_order, variety, order_id, **kwargs)
    
    async def cancel_order(self, variety, order_id):
        return await self._call_order_api(self._kite.cancel_order, variety, order_id)
    
    async def ltp(self, instruments):
        return await self._call_api(self._kite.ltp, instruments)
    
    async def quote(self, instruments):
        return await self._call_api(self._kite.quote, instruments)
    
    async def instruments(self, exchange=None):
        return await self._call_api(self._kite.instruments, exchange)
    
    # Synchronous passthrough for historical_data (used in bootstrap)
    def historical_data(self, *args, **kwargs):
        """Synchronous method for historical data - used in run_in_executor"""
        return self._kite.historical_data(*args, **kwargs)
    
    def instruments_sync(self, exchange=None):
        """Synchronous instruments call - used in __init__ and sync contexts"""
        return self._kite.instruments(exchange)
    
    # Passthrough for synchronous methods (used in authentication)
    def set_access_token(self, token):
        return self._kite.set_access_token(token)
    
    def login_url(self):
        return self._kite.login_url()
    
    def generate_session(self, request_token, api_secret):
        return self._kite.generate_session(request_token, api_secret)
    
    # Fallback for any method not explicitly wrapped
    def __getattr__(self, name):
        return getattr(self._kite, name)

# Replace the global kite instance with rate-limited version
_original_kite = kite
kite = RateLimitedKite(_original_kite)

print("[OK] Rate limiting enabled: 3 API req/s, 10 orders/s (Zerodha compliant)")

# --- INITIAL STARTUP CALL ---
# The application will now call this function when it first starts.
re_initialize_session_from_file()

# --- USER SWITCHING SUPPORT ---
async def reload_user_session():
    """
    Reload KiteConnect with a different user's credentials.
    Called when switching users via the web UI.
    Returns True if successful, False otherwise.
    """
    global kite, _original_kite, API_KEY, API_SECRET, ACTIVE_USER_ID, ACTIVE_USER_NAME, access_token
    
    try:
        # Load the new active user from updated config
        new_user = load_active_user()
        
        if not new_user:
            print("[RELOAD] No user found in user_profiles.json")
            return False
        
        # Update global variables
        API_KEY = new_user.get("api_key")
        API_SECRET = new_user.get("api_secret")
        ACTIVE_USER_ID = new_user.get("id")
        ACTIVE_USER_NAME = new_user.get("name")
        
        # Reset access token (user must re-authenticate)
        access_token = None
        
        # Create new KiteConnect instance with new credentials
        _original_kite = KiteConnect(api_key=API_KEY)
        
        # Apply same retry logic
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        _original_kite.session.mount("http://", adapter)
        _original_kite.session.mount("https://", adapter)
        _original_kite.session.timeout = 30
        
        # Wrap with rate limiting
        kite = RateLimitedKite(_original_kite)
        
        # Try to load existing token for this user
        user_token_file = f"access_token_{ACTIVE_USER_ID}.json"
        if os.path.exists(user_token_file):
            try:
                with open(user_token_file, "r") as f:
                    session = json.load(f)
                    if "access_token" in session:
                        kite.set_access_token(session["access_token"])
                        access_token = session["access_token"]
                        print(f"[RELOAD] ✓ Loaded existing token for {ACTIVE_USER_NAME}")
                        return True
            except Exception as e:
                print(f"[RELOAD] Could not load existing token: {e}")
        
        print(f"[RELOAD] ✓ Switched to user: {ACTIVE_USER_NAME} (will require authentication)")
        return True
        
    except Exception as e:
        print(f"[RELOAD] ❌ Error reloading user session: {e}")
        import traceback
        traceback.print_exc()
        return False
