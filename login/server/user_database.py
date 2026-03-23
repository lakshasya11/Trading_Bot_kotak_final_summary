import json
import os

# Use absolute path relative to this script file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_FILE = os.path.join(BASE_DIR, 'users.json')

def load_users():
    if os.path.exists(DATABASE_FILE):
        with open(DATABASE_FILE, 'r') as f:
            return json.load(f)
    return []

def save_users(users):
    with open(DATABASE_FILE, 'w') as f:
        json.dump(users, f, indent=2)

def has_existing_users():
    users = load_users()
    return len(users) > 0

def add_user(user_data):
    import pyotp
    
    # Generate TOTP secret for 2FA
    secret = pyotp.random_base32()
    user_data['totp_secret'] = secret
    
    # Ensure client_id is saved (handle both camelCase from frontend and snake_case)
    if 'client_id' not in user_data and 'clientId' in user_data:
        user_data['client_id'] = user_data['clientId']
    
    # Generate random username (as requested for QR code)
    import random
    import string
    username = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    user_data['username'] = username
    
    users = load_users()
    users.append(user_data)
    save_users(users)
    return secret, user_data.get('client_id'), username

def check_aadhar_exists(aadhar):
    users = load_users()
    return any(user.get('aadhar') == aadhar for user in users)

def check_email_exists(email):
    users = load_users()
    return any(user.get('email') == email for user in users)

def check_client_id_exists(client_id):
    users = load_users()
    return any(user.get('client_id') == client_id for user in users)

def get_all_users():
    return load_users()

def update_user_password(query_id, new_password):
    """Update password using email or client_id"""
    users = load_users()
    for user in users:
        if user.get('email') == query_id or user.get('client_id') == query_id:
            user['password'] = new_password
            save_users(users)
            return True
    return False

def verify_login(client_id, password):
    users = load_users()
    for user in users:
        # Check against client_id
        if user.get('client_id') == client_id and user['password'] == password:
            return user
    return None

def verify_totp(client_id, totp_code):
    import pyotp
    users = load_users()
    for user in users:
        if user.get('client_id') == client_id:
            totp = pyotp.TOTP(user['totp_secret'])
            return totp.verify(totp_code, valid_window=2)
    return False

def get_user_by_client_id(client_id):
    """Get user details by client_id"""
    users = load_users()
    for user in users:
        if user.get('client_id') == client_id:
            return user
    return None

def get_user_by_email(email):
    """Get user details by email"""
    users = load_users()
    for user in users:
        if user.get('email') == email:
            return user
    return None

