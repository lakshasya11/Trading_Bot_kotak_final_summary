"""
Automated Zerodha Login System
Handles automatic login with user_id, password, and TOTP generation
"""

import os
import json
import time
import pyotp
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from urllib.parse import urlparse, parse_qs


class ZerodhaAutoLogin:
    """Automated login handler for Zerodha Kite"""
    
    def __init__(self, user_profile):
        """
        Initialize auto-login with user credentials
        
        Args:
            user_profile (dict): User profile from user_profiles.json
        """
        self.user_id = user_profile.get("user_id", "").strip()
        self.password = user_profile.get("password", "").strip()
        self.totp_secret = user_profile.get("totp_secret", "").strip()
        self.api_key = user_profile.get("api_key", "").strip()
        
        self.driver = None
        self.headless = True  # Set to False for debugging
        
    def validate_credentials(self):
        """Check if all required credentials are present"""
        missing = []
        if not self.user_id:
            missing.append("user_id")
        if not self.password:
            missing.append("password")
        if not self.totp_secret:
            missing.append("totp_secret")
        if not self.api_key:
            missing.append("api_key")
            
        if missing:
            raise ValueError(f"Missing credentials in user_profiles.json: {', '.join(missing)}")
        
        return True
    
    def generate_totp(self):
        """Generate current TOTP code"""
        try:
            totp = pyotp.TOTP(self.totp_secret)
            code = totp.now()
            print(f"[TOTP] Generated code: {code}")
            return code
        except Exception as e:
            print(f"[ERROR] TOTP generation failed: {e}")
            raise
    
    def setup_driver(self):
        """Initialize Chrome WebDriver with options"""
        options = Options()
        
        if self.headless:
            options.add_argument("--headless=new")  # New headless mode
            options.add_argument("--disable-gpu")
        
        # Performance & stability options
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        # User agent to avoid bot detection
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        try:
            # Try using webdriver-manager first (recommended)
            try:
                from selenium.webdriver.chrome.service import Service
                from webdriver_manager.chrome import ChromeDriverManager
                
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=options)
                print("[OK] Chrome WebDriver initialized (via webdriver-manager)")
            except ImportError:
                # Fallback to regular Chrome driver
                self.driver = webdriver.Chrome(options=options)
                print("[OK] Chrome WebDriver initialized")
            
            # Fast but safe timeouts
            self.driver.set_page_load_timeout(30)
            # self.driver.implicitly_wait(3)  # ❌ REMOVED - was causing 18-24s delay in button search
            print("[INFO] Timeouts configured: page_load=30s, implicit_wait=DISABLED (for faster element search)")
        except Exception as e:
            print(f"[ERROR] Failed to initialize WebDriver: {e}")
            print("[INFO] Install with: pip install webdriver-manager")
            print("[INFO] Or make sure Chrome and ChromeDriver are installed")
            raise
    
    def login(self):
        """
        Perform automated login to Zerodha
        
        Returns:
            str: Request token extracted from redirect URL
        """
        try:
            self.validate_credentials()
            self.setup_driver()
            
            # Navigate to Kite login URL
            login_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={self.api_key}"
            print(f"[INFO] Navigating to: {login_url}")
            self.driver.get(login_url)
            
            # Step 1: Enter User ID (WebDriverWait handles page load - no extra sleep needed)
            print("[STEP 1] Entering user ID...")
            user_id_input = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.ID, "userid"))
            )
            user_id_input.clear()
            user_id_input.send_keys(self.user_id)
            
            # Step 2: Enter Password
            print("[STEP 2] Entering password...")
            password_input = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.ID, "password"))
            )
            password_input.clear()
            password_input.send_keys(self.password)
            
            # Step 3: Click Login Button
            print("[STEP 3] Clicking login button...")
            login_button = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
            )
            login_button.click()
            
            # Wait for TOTP page to load - verify we actually left the login page
            print("[INFO] Waiting for TOTP page...")
            
            # Step 3b: Verify login page transition happened
            # Wait for the password field to disappear (indicates page moved to TOTP step)
            totp_page_loaded = False
            for wait_attempt in range(30):  # Wait up to 15 seconds (0.5s intervals)
                time.sleep(0.5)
                try:
                    # Check if password field is still visible (means still on login page)
                    pwd_field = self.driver.find_elements(By.ID, "password")
                    if pwd_field and pwd_field[0].is_displayed():
                        # Still on login page - check for error messages
                        error_msgs = self.driver.find_elements(By.CSS_SELECTOR, ".error-message, .su-alert, .alert, .error, [class*='error'], [class*='invalid']")
                        for em in error_msgs:
                            if em.is_displayed() and em.text.strip():
                                raise Exception(f"Login failed - Zerodha error: {em.text.strip()}")
                        
                        if wait_attempt >= 20:
                            # Try to get page source for debugging
                            try:
                                page_text = self.driver.find_element(By.TAG_NAME, "body").text
                                if "invalid" in page_text.lower() or "incorrect" in page_text.lower():
                                    raise Exception(f"Login failed - credentials may be invalid. Page text: {page_text[:200]}")
                            except Exception as inner_e:
                                if "Login failed" in str(inner_e):
                                    raise
                            
                            # Save screenshot for debugging
                            try:
                                self.driver.save_screenshot("login_stuck.png")
                                print("[DEBUG] Screenshot saved: login_stuck.png")
                            except:
                                pass
                            raise Exception("Login page did not transition to TOTP page after 15 seconds. Credentials may be incorrect.")
                        
                        if wait_attempt % 4 == 0:  # Log every 2 seconds instead of every 0.5s
                            print(f"[INFO] Still on login page, waiting... ({wait_attempt * 0.5:.0f}s)")
                        continue
                    else:
                        # Password field gone - we moved past login
                        totp_page_loaded = True
                        print("[OK] Login page transition detected!")
                        break
                except Exception as e:
                    if "Login failed" in str(e) or "did not transition" in str(e):
                        raise
                    # Element lookup error - page might be transitioning
                    totp_page_loaded = True
                    print("[OK] Page transitioning...")
                    break
            
            # Brief wait for TOTP page DOM to settle
            time.sleep(0.5)
            
            # Step 4: Generate and Enter TOTP
            print("[STEP 4] Generating and entering TOTP...")
            totp_code = self.generate_totp()
            
            # Try multiple selectors for TOTP input (Zerodha changes these)
            # NOTE: Page transition already confirmed (password field gone), so
            # we trust we're on the TOTP page. Zerodha may reuse id="userid" on the TOTP input.
            totp_input = None
            totp_selectors = [
                (By.ID, "totp"),
                (By.CSS_SELECTOR, "input[type='number'][placeholder='••••••']"),
                (By.CSS_SELECTOR, "input[type='number']"),
                (By.CSS_SELECTOR, "input[pattern='[0-9]+']"),
                (By.CSS_SELECTOR, "input[type='tel']"),
                (By.XPATH, "//input[@maxlength='6']"),
                (By.XPATH, "//input[@type='number' and @minlength='6']"),
                (By.CSS_SELECTOR, "input.su-input[type='number']"),
                (By.CSS_SELECTOR, "input[label='External TOTP']"),
                (By.XPATH, "//label[contains(text(),'TOTP')]/following::input[1]"),
                (By.XPATH, "//label[contains(text(),'totp')]/following::input[1]"),
            ]
            
            for selector_type, selector_value in totp_selectors:
                try:
                    totp_input = WebDriverWait(self.driver, 1).until(
                        EC.presence_of_element_located((selector_type, selector_value))
                    )
                    # Verify it's visible and enabled
                    if totp_input.is_displayed() and totp_input.is_enabled():
                        field_id = totp_input.get_attribute("id") or ""
                        field_name = totp_input.get_attribute("name") or ""
                        # Only reject if it's the PASSWORD field (should not appear on TOTP page)
                        # Do NOT reject id="userid" — Zerodha reuses this ID on the TOTP input
                        if field_id == "password" or field_name == "password":
                            print(f"[WARNING] Skipping password field on TOTP page: id={field_id}, name={field_name}")
                            totp_input = None
                            continue
                        print(f"[INFO] Found TOTP input using: {selector_type}={selector_value} (id={field_id})")
                        break
                    else:
                        totp_input = None
                except Exception as e:
                    print(f"[DEBUG] Selector {selector_type}={selector_value} failed: {str(e)[:50]}")
                    continue
            
            # Last resort: find any visible input on the page that isn't password
            if not totp_input:
                print("[INFO] Trying last-resort: scanning all visible inputs...")
                all_inputs = self.driver.find_elements(By.TAG_NAME, "input")
                for inp in all_inputs:
                    try:
                        if inp.is_displayed() and inp.is_enabled():
                            inp_id = inp.get_attribute("id") or ""
                            inp_name = inp.get_attribute("name") or ""
                            inp_type = inp.get_attribute("type") or ""
                            if inp_id != "password" and inp_name != "password" and inp_type != "hidden":
                                print(f"[INFO] Found candidate input: id={inp_id}, name={inp_name}, type={inp_type}")
                                totp_input = inp
                                break
                    except:
                        continue
            
            if not totp_input:
                # Save screenshot for debugging
                try:
                    screenshot_path = "totp_page_error.png"
                    self.driver.save_screenshot(screenshot_path)
                    print(f"[DEBUG] Screenshot saved to {screenshot_path}")
                    # Save page source too
                    with open("totp_page_error.html", "w", encoding="utf-8") as f:
                        f.write(self.driver.page_source)
                    print("[DEBUG] Page source saved to totp_page_error.html")
                except:
                    pass
                raise Exception("Could not find TOTP input field on page. Login may have failed or page structure changed.")
            
            totp_input.clear()
            totp_input.send_keys(totp_code)
            
            # Zerodha typically auto-submits when 6 digits are entered
            print("[INFO] TOTP entered - checking for redirect (auto-submit expected)...")
            
            # Step 5: Check if redirect happened immediately (auto-submit)
            # Most of the time, Zerodha auto-submits and we can skip button clicking
            time.sleep(1.5)  # Give auto-submit a chance
            
            current_url = self.driver.current_url
            if "request_token" in current_url or "status" in current_url:
                print("[OK] Auto-submit detected - redirect already occurred!")
            else:
                # Still on TOTP page - try to find and click button (fast attempt)
                print("[STEP 5] Auto-submit didn't occur - searching for button (max 2s)...")
                
                button_selectors = [
                    (By.XPATH, "//button[@type='submit']"),
                    (By.CSS_SELECTOR, "button[type='submit']"),
                    (By.XPATH, "//button[contains(text(), 'Continue')]"),
                    (By.CSS_SELECTOR, ".button-orange"),
                ]
                
                button_clicked = False
                for selector_type, selector_value in button_selectors:
                    try:
                        # Very short timeout per selector (0.5s max)
                        continue_button = WebDriverWait(self.driver, 0.5).until(
                            EC.presence_of_element_located((selector_type, selector_value))
                        )
                        
                        # Use JavaScript click (more reliable)
                        self.driver.execute_script("arguments[0].click();", continue_button)
                        print(f"[OK] Button clicked: {selector_type}={selector_value}")
                        button_clicked = True
                        break
                    except:
                        continue
                
                if not button_clicked:
                    print("[WARNING] No button found - if TOTP was correct, redirect should happen soon")
            
            # Step 6: Wait for redirect and extract request_token
            print("[STEP 6] Waiting for redirect (max 10s)...")
            
            # Try to catch the redirect URL quickly before browser closes
            current_url = None
            last_error = None
            max_attempts = 20  # 10 seconds total (reduced from 25)
            check_interval = 0.5  # Check every 500ms for faster capture
            
            for attempt in range(max_attempts):
                try:
                    current_url = self.driver.current_url
                    
                    # Check if we have the token
                    if "request_token" in current_url:
                        print(f"[INFO] Redirect captured! URL: {current_url[:100]}...")
                        break
                    
                    # Check for errors in URL
                    if "error" in current_url or "message" in current_url:
                        parsed = urlparse(current_url)
                        params = parse_qs(parsed.query)
                        error = params.get("message", ["Unknown error"])[0]
                        last_error = f"Zerodha login error: {error}"
                        break
                    
                    # Progress indicator every 2 seconds
                    if attempt > 0 and attempt % 4 == 0:
                        print(f"[INFO] Still waiting for redirect... ({attempt * 0.5:.0f}s)")
                    
                    # Still on Zerodha page - keep waiting
                    time.sleep(check_interval)
                    
                except (ConnectionRefusedError, OSError) as conn_err:
                    # Connection lost - browser might have closed after redirect
                    # This is expected if Zerodha closes the window
                    print(f"[INFO] Connection lost - browser may have closed after redirect")
                    last_error = "connection_lost"
                    break
                    
                except Exception as e:
                    error_str = str(e)
                    last_error = error_str
                    if attempt < max_attempts - 1:
                        time.sleep(check_interval)
                        continue
                    else:
                        break
            
            # Check if we got the URL with token
            if current_url and "request_token" in current_url:
                # Success!
                pass  # Continue to extraction
            elif last_error == "connection_lost":
                # Browser closed - this might mean redirect happened but we didn't catch it
                # Check if we captured a URL before connection was lost
                if not current_url or "request_token" not in current_url:
                    raise Exception("Browser closed before we could capture the redirect URL. This may happen if Zerodha automatically closes the window after authentication.")
            elif last_error:
                raise Exception(f"Login failed: {last_error[:200]}")
            else:
                # Timeout - provide helpful error message
                try:
                    page_url = self.driver.current_url
                    if "kite.zerodha.com" in page_url:
                        # Still on Zerodha - likely TOTP didn't auto-submit
                        raise Exception(
                            f"Timeout waiting for redirect (10s). Still on: {page_url}. "
                            f"Possible causes: 1) TOTP auto-submit failed, 2) Slow connection, 3) Wrong TOTP code. "
                            f"Check login_error.png for screenshot."
                        )
                    else:
                        raise Exception(f"Timeout waiting for redirect. Last URL: {page_url[:100]}")
                except:
                    raise Exception("Timeout waiting for redirect (10s). Auto-login failed - check credentials and TOTP secret.")
            parsed = urlparse(current_url)
            params = parse_qs(parsed.query)
            request_token = params.get("request_token", [None])[0]
            
            if not request_token:
                # Try to get more info about what went wrong
                print(f"[ERROR] No request_token in URL: {current_url}")
                raise Exception(f"Request token not found. URL: {current_url[:100]}")
            
            print(f"[SUCCESS] ✅ Login successful! Request token: {request_token[:20]}...")
            return request_token
            
        except Exception as e:
            error_msg = str(e)
            print(f"[ERROR] ❌ Auto-login failed: {error_msg}")
            
            # Save debugging information
            if self.driver:
                try:
                    screenshot_path = "login_error.png"
                    self.driver.save_screenshot(screenshot_path)
                    print(f"[DEBUG] Screenshot saved: {screenshot_path}")
                    
                    # Save page source
                    with open("login_error.html", "w", encoding="utf-8") as f:
                        f.write(self.driver.page_source)
                    print(f"[DEBUG] Page source saved: login_error.html")
                    
                    # Save current URL for debugging
                    try:
                        current_url = self.driver.current_url
                        print(f"[DEBUG] Last URL: {current_url}")
                    except:
                        pass
                except:
                    pass
            
            # Re-raise with clearer message
            if "Timeout" in error_msg:
                raise Exception(f"Auto-login timeout. {error_msg}. Please check: 1) Credentials in user_profiles.json, 2) TOTP secret is correct, 3) Network connection")
            else:
                raise
            
        finally:
            # Cleanup
            if self.driver:
                self.driver.quit()
                print("[INFO] WebDriver closed")
    
    def login_and_generate_session(self, generate_session_callback):
        """
        Perform login and automatically generate session
        
        Args:
            generate_session_callback: Function to call with request_token
            
        Returns:
            tuple: (success, data) from session generation
        """
        try:
            request_token = self.login()
            print("[INFO] Generating session with request token...")
            return generate_session_callback(request_token)
        except Exception as e:
            return False, str(e)


def auto_login_for_user(user_profile):
    """
    Standalone function to perform auto-login
    
    Args:
        user_profile (dict): User profile from user_profiles.json
        
    Returns:
        str: Request token
    """
    auto_login = ZerodhaAutoLogin(user_profile)
    return auto_login.login()


def check_auto_login_available(user_profile):
    """
    Check if auto-login is configured for user
    
    Args:
        user_profile (dict): User profile
        
    Returns:
        bool: True if all credentials are present
    """
    required = ["user_id", "password", "totp_secret"]
    return all(user_profile.get(field, "").strip() for field in required)


# ===== TOTP SECRET SETUP GUIDE =====
def print_totp_setup_guide():
    """Print instructions for getting TOTP secret"""
    guide = """
    ╔═══════════════════════════════════════════════════════════════════════════╗
    ║                    HOW TO GET YOUR TOTP SECRET                            ║
    ╚═══════════════════════════════════════════════════════════════════════════╝
    
    METHOD 1: Extract from existing authenticator app
    ------------------------------------------------
    If you're using Google Authenticator or similar:
    
    1. Go to Zerodha account settings → Security → Two-factor authentication
    2. Click "Re-generate" to see QR code again
    3. Instead of scanning, click "Can't scan the QR code?"
    4. Copy the SECRET KEY (alphanumeric string like: ABCD1234EFGH5678)
    5. Paste this into user_profiles.json → totp_secret field
    6. Complete the setup with your authenticator app as usual
    
    METHOD 2: During new TOTP setup
    --------------------------------
    If setting up TOTP for the first time:
    
    1. Go to Zerodha account settings → Security
    2. Enable Two-factor authentication
    3. When QR code appears, click "Can't scan the QR code?"
    4. Copy the SECRET KEY
    5. Use this key in BOTH:
       - Your authenticator app (Google Authenticator, Authy, etc.)
       - user_profiles.json → totp_secret field
    
    SECURITY NOTES:
    ✅ Keep user_profiles.json secure (already in .gitignore)
    ✅ Never share your TOTP secret
    ✅ Backup this file safely
    ✅ Each user needs their own TOTP secret
    
    Example user_profiles.json entry:
    {
      "user_id": "AB1234",
      "password": "YourPassword123",
      "totp_secret": "JBSWY3DPEHPK3PXP"  ← Your TOTP secret here
    }
    """
    print(guide)


if __name__ == "__main__":
    print_totp_setup_guide()
