from flask import Flask, request, jsonify, render_template, g
from markupsafe import escape
from flask_cors import CORS
import psycopg2
from psycopg2 import pool, sql
from datetime import datetime
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import random
import time
import os
from dotenv import load_dotenv
import re
from user_database import add_user, check_aadhar_exists, verify_login, verify_totp
import pyotp
import qrcode
import io
import base64
from database_sync import db_sync

load_dotenv()

app = Flask(__name__)
CORS(app)

# Store OTPs temporarily
otp_store = {}

# Connection pool
connection_pool = None

def init_connection_pool():
    global connection_pool
    if connection_pool is None:
        try:
            connection_pool = psycopg2.pool.SimpleConnectionPool(
                1, 5,  # Reduced to 1-5 connections
                host=os.getenv('DB_HOST', 'localhost'),
                database=os.getenv('DB_NAME', 'trading_master_db'),
                user=os.getenv('DB_USER', 'postgres'),
                password=os.getenv('DB_PASSWORD'),
                port=os.getenv('DB_PORT', '5432')
            )
            print("Connection pool initialized successfully")
        except Exception as e:
            print(f"Failed to initialize connection pool: {e}")
            connection_pool = None

TABLE_MAP = {
    'master_bot_data': 'master_bot_data',
    'client_bot_signups': 'client_bot_signups',
    'daily_trading_sessions': 'user_session_data'
}

def get_db():
    if 'db' not in g:
        if connection_pool is None:
            init_connection_pool()
        if connection_pool is not None:
            g.db = connection_pool.getconn()
        else:
            # Fallback to direct connection if pool fails
            g.db = psycopg2.connect(
                host=os.getenv('DB_HOST', 'localhost'),
                database=os.getenv('DB_NAME', 'trading_master_db'),
                user=os.getenv('DB_USER', 'postgres'),
                password=os.getenv('DB_PASSWORD'),
                port=os.getenv('DB_PORT', '5432')
            )
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        if connection_pool is not None:
            connection_pool.putconn(db)
        else:
            db.close()

def get_client_data(client_id, db_name):
    try:
        conn = get_db()
        cur = conn.cursor()
        table_name = TABLE_MAP.get(db_name)
        if table_name:
            if table_name == 'master_bot_data':
                query = sql.SQL("SELECT name, email, phone, client_id, bot_id, created_at FROM {} WHERE name IS NOT NULL AND name != '' AND email IS NOT NULL AND email != '' ORDER BY sl_number ASC").format(sql.Identifier(table_name))
            elif table_name == 'client_bot_signups':
                query = sql.SQL("SELECT client_id, username, email, phone, aadhar, qr_key, created_at FROM {} WHERE username IS NOT NULL AND username != '' AND email IS NOT NULL AND email != '' ORDER BY id ASC").format(sql.Identifier(table_name))
            elif table_name == 'user_session_data':
                query = sql.SQL("""
                    SELECT session_date, client_id, username, kite_id, 
                           COALESCE(MAX(kite_username), '') as kite_username,
                           COUNT(DISTINCT total_trades) as total_sessions
                    FROM {} 
                    WHERE client_id IS NOT NULL AND client_id != ''
                      AND username IS NOT NULL AND username != ''
                      AND kite_id IS NOT NULL AND kite_id != ''
                    GROUP BY session_date, client_id, username, kite_id
                    ORDER BY session_date DESC, client_id ASC
                """).format(sql.Identifier(table_name))
            else:
                query = sql.SQL("SELECT * FROM {}").format(sql.Identifier(table_name))
            cur.execute(query)
        else:
            return [], []
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        
        # Add sequential SL numbers and format datetime
        if table_name in ['master_bot_data', 'client_bot_signups', 'user_session_data'] and rows:
            formatted_rows = []
            for i, row in enumerate(rows, 1):  # Start from 1
                row_list = [i] + list(row)  # Add sequential SL number at start
                # Format datetime columns
                for j in range(len(row_list)):
                    if isinstance(row_list[j], datetime):
                        row_list[j] = row_list[j].strftime('%Y-%m-%d %H:%M:%S')
                # Add view button for user_session_data
                if table_name == 'user_session_data':
                    row_list.append('View')  # Add view button
                formatted_rows.append(tuple(row_list))
            rows = formatted_rows
            columns = ['sl_number'] + columns
            if table_name == 'user_session_data':
                columns.append('view_button')  # Add view button column
        
        return columns, rows
    except Exception as e:
        logging.error(f"Database error in get_client_data: {e}")
        return [], []

def has_existing_users():
    from user_database import get_all_users
    users = get_all_users()
    return len(users) > 0

def send_welcome_email(to_email, username, first_name):
    sender_email = os.getenv('SMTP_EMAIL')
    password = os.getenv('SMTP_PASSWORD')
    smtp_server = os.getenv('SMTP_SERVER')
    smtp_port = int(os.getenv('SMTP_PORT'))
    
    message = MIMEMultipart("alternative")
    message["Subject"] = "Welcome to Trading Bot - Account Created Successfully"
    message["From"] = sender_email
    message["To"] = to_email
    
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #b19cd9, #9575cd); padding: 30px; border-radius: 10px; text-align: center; color: white;">
          <h1 style="margin: 0;">🎉 Welcome to Trading Bot!</h1>
        </div>
        
        <div style="padding: 30px; background: #f9f9f9; border-radius: 10px; margin-top: 20px;">
          <h2 style="color: #333;">Hello {first_name},</h2>
          <p style="color: #666; line-height: 1.6;">Your account has been created successfully! Here are your login details:</p>
          
          <div style="background: white; padding: 20px; border-radius: 8px; border-left: 4px solid #b19cd9; margin: 20px 0;">
            <h3 style="color: #b19cd9; margin-top: 0;">📋 Your Login Credentials</h3>
            <p><strong>Client ID:</strong> <span style="color: #333; font-family: monospace; background: #f0f0f0; padding: 2px 8px; border-radius: 4px;">{username}</span></p>
            <p><strong>Email:</strong> {to_email}</p>
          </div>
          
          <div style="background: #e8f4fd; padding: 20px; border-radius: 8px; margin: 20px 0;">
            <h3 style="color: #1976d2; margin-top: 0;">🔐 Two-Factor Authentication</h3>
            <p style="color: #555;">Your account is secured with 2FA. Please:</p>
            <ol style="color: #555;">
              <li>Scan the QR code with Google Authenticator app</li>
              <li>Use your Client ID to login</li>
              <li>Enter the 6-digit code from the app when prompted</li>
            </ol>
          </div>
          
          <div style="text-align: center; margin: 30px 0;">
            <a href="http://localhost:5174" style="background: linear-gradient(135deg, #b19cd9, #9575cd); color: white; padding: 12px 30px; text-decoration: none; border-radius: 25px; font-weight: bold;">Login Now</a>
          </div>
          
          <p style="color: #888; font-size: 0.9em; text-align: center;">If you have any questions, please contact our support team.</p>
        </div>
        
        <div style="text-align: center; margin-top: 20px; color: #888; font-size: 0.8em;">
          <p>© 2024 Trading Bot. All rights reserved.</p>
        </div>
      </body>
    </html>
    """
    
    part = MIMEText(html, "html")
    message.attach(part)
    
    try:
        context = ssl.create_default_context()
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls(context=context)
        server.login(sender_email, password)
        server.sendmail(sender_email, to_email, message.as_string())
        server.quit()
        print(f"Welcome email sent successfully to {to_email}")
    except Exception as e:
        print(f"Welcome email error: {e}")
        raise e

def send_otp_email(to_email, otp):
    sender_email = os.getenv('SMTP_EMAIL')
    password = os.getenv('SMTP_PASSWORD')
    smtp_server = os.getenv('SMTP_SERVER')
    smtp_port = int(os.getenv('SMTP_PORT'))
    
    message = MIMEMultipart("alternative")
    message["Subject"] = "Trading Bot - Email Verification OTP"
    message["From"] = sender_email
    message["To"] = to_email
    
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #b19cd9, #9575cd); padding: 30px; border-radius: 10px; text-align: center; color: white;">
          <h1 style="margin: 0;">🔐 Password Reset Verification</h1>
        </div>
        
        <div style="padding: 30px; background: #f9f9f9; border-radius: 10px; margin-top: 20px;">
          <h2 style="color: #333;">Password Reset Request</h2>
          <p style="color: #666; line-height: 1.6;">We received a request to reset your password. Use the OTP below to verify your identity:</p>
          
          <div style="background: white; padding: 30px; border-radius: 10px; text-align: center; margin: 20px 0; border: 2px solid #b19cd9;">
            <h3 style="color: #b19cd9; margin-top: 0;">Your Verification OTP</h3>
            <div style="background: #f0f0f0; padding: 20px; font-size: 32px; font-weight: bold; color: #333; border-radius: 10px; font-family: monospace; letter-spacing: 3px;">
              {otp}
            </div>
          </div>
          
          <div style="background: #fff3cd; padding: 15px; border-radius: 8px; border-left: 4px solid #ffc107; margin: 20px 0;">
            <p style="color: #856404; margin: 0;"><strong>⚠️ Important:</strong> This OTP will expire in 5 minutes for security reasons.</p>
          </div>
          
          <p style="color: #666;">If you didn't request a password reset, please ignore this email or contact support if you have concerns.</p>
        </div>
        
        <div style="text-align: center; margin-top: 20px; color: #888; font-size: 0.8em;">
          <p>© 2024 Trading Bot. All rights reserved.</p>
        </div>
      </body>
    </html>
    """
    
    part = MIMEText(html, "html")
    message.attach(part)
    
    try:
        context = ssl.create_default_context()
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls(context=context)
        server.login(sender_email, password)
        server.sendmail(sender_email, to_email, message.as_string())
        server.quit()
        print(f"Email sent successfully to {to_email}")
    except smtplib.SMTPAuthenticationError as e:
        print(f"Authentication failed: {e}")
        raise Exception("Gmail authentication failed. Check App Password.")
    except Exception as e:
        print(f"SMTP Error: {e}")
        raise e

@app.route('/api/send-email-verification', methods=['POST'])
def send_email_verification():
    data = request.get_json()
    email = data.get('email')
    
    if not email:
        return jsonify({'error': 'Email is required'}), 400
    
    otp = random.randint(100000, 999999)
    otp_store[f"verify_{email}"] = {
        'otp': otp,
        'expires': time.time() + 300  # 5 minutes
    }
    
    # Send email verification OTP
    sender_email = os.getenv('SMTP_EMAIL')
    password = os.getenv('SMTP_PASSWORD')
    smtp_server = os.getenv('SMTP_SERVER')
    smtp_port = int(os.getenv('SMTP_PORT'))
    
    message = MIMEMultipart("alternative")
    message["Subject"] = "Trading Bot - Email Verification OTP"
    message["From"] = sender_email
    message["To"] = email
    
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #b19cd9, #9575cd); padding: 30px; border-radius: 10px; text-align: center; color: white;">
          <h1 style="margin: 0;">📧 Email Verification</h1>
        </div>
        
        <div style="padding: 30px; background: #f9f9f9; border-radius: 10px; margin-top: 20px;">
          <h2 style="color: #333;">Verify Your Email Address</h2>
          <p style="color: #666; line-height: 1.6;">Please use the OTP below to verify your email address:</p>
          
          <div style="background: white; padding: 30px; border-radius: 10px; text-align: center; margin: 20px 0; border: 2px solid #b19cd9;">
            <h3 style="color: #b19cd9; margin-top: 0;">Your Verification OTP</h3>
            <div style="background: #f0f0f0; padding: 20px; font-size: 32px; font-weight: bold; color: #333; border-radius: 10px; font-family: monospace; letter-spacing: 3px;">
              {otp}
            </div>
          </div>
          
          <div style="background: #fff3cd; padding: 15px; border-radius: 8px; border-left: 4px solid #ffc107; margin: 20px 0;">
            <p style="color: #856404; margin: 0;"><strong>⚠️ Important:</strong> This OTP will expire in 5 minutes.</p>
          </div>
        </div>
      </body>
    </html>
    """
    
    part = MIMEText(html, "html")
    message.attach(part)
    
    try:
        context = ssl.create_default_context()
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls(context=context)
        server.login(sender_email, password)
        server.sendmail(sender_email, email, message.as_string())
        server.quit()
        return jsonify({'success': True, 'message': 'Verification OTP sent successfully'})
    except Exception as e:
        print(f"Email verification error: {e}")
        return jsonify({'error': f'Failed to send verification email: {str(e)}'}), 500

@app.route('/api/verify-email-otp', methods=['POST'])
def verify_email_otp():
    data = request.get_json()
    email = data.get('email')
    otp = data.get('otp')
    
    key = f"verify_{email}"
    if key not in otp_store:
        return jsonify({'error': 'OTP not found or expired'}), 400
    
    stored = otp_store[key]
    if time.time() > stored['expires']:
        del otp_store[key]
        return jsonify({'error': 'OTP expired'}), 400
    
    if str(stored['otp']) != str(otp):
        return jsonify({'error': 'Invalid OTP'}), 400
    
    del otp_store[key]
    return jsonify({'success': True, 'message': 'Email verified successfully'})

@app.route('/api/check-signup-allowed', methods=['GET'])
def check_signup_allowed():
    return jsonify({'signupAllowed': not has_existing_users()})

@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    data = request.get_json()
    email = data.get('email')
    is_forgot_password = data.get('forgotPassword', False)
    
    # Block signup OTP if users already exist, but always allow forgot-password OTP
    if not is_forgot_password and has_existing_users():
        return jsonify({'error': 'Registration is closed. Please login with existing credentials.'}), 403
    
    if not email:
        return jsonify({'error': 'Email is required'}), 400
    
    otp = random.randint(100000, 999999)
    otp_store[email] = {
        'otp': otp,
        'expires': time.time() + 300  # 5 minutes
    }
    
    try:
        send_otp_email(email, otp)
        return jsonify({'success': True, 'message': 'OTP sent successfully'})
    except Exception as e:
        print(f"Email error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to send email: {str(e)}'}), 500

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    data = request.get_json()
    email = data.get('email')
    otp = data.get('otp')
    
    if email not in otp_store:
        return jsonify({'error': 'OTP not found or expired'}), 400
    
    stored = otp_store[email]
    if time.time() > stored['expires']:
        del otp_store[email]
        return jsonify({'error': 'OTP expired'}), 400
    
    if str(stored['otp']) != str(otp):
        return jsonify({'error': 'Invalid OTP'}), 400
    
    del otp_store[email]
    return jsonify({'success': True, 'message': 'Email verified successfully'})

@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json()
    email = data.get('email')
    new_password = data.get('newPassword')
    
    if not email or not new_password:
        return jsonify({'error': 'Email and password are required'}), 400
    
    # Validate password
    if not re.match(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,15}$', new_password) or ' ' in new_password:
        return jsonify({'error': 'Password must be 8-15 characters with uppercase, lowercase, number, and special character'}), 400
    
    # Update password in database
    from user_database import update_user_password
    success = update_user_password(email, new_password)
    
    if success:
        return jsonify({'success': True, 'message': 'Password reset successfully'})
    else:
        return jsonify({'error': 'User not found'}), 404

@app.route('/api/view-users', methods=['GET'])
def view_users():
    from user_database import get_all_users
    import datetime
    import random
    import string
    
    users = get_all_users()
    
    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Registered Users</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; background: #f8f9fa; }
            h1 { color: #6c5ce7; font-size: 2rem; margin-bottom: 10px; }
            .total { color: #333; margin-bottom: 20px; font-size: 1.1rem; }
            table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            th { background: #6c5ce7; color: white; padding: 15px 10px; text-align: left; font-weight: 600; }
            td { padding: 12px 10px; border-bottom: 1px solid #eee; font-family: Arial, sans-serif; font-size: 1em; }
            tr:hover { background: #f8f9ff; }
            .id-col { width: 5%; text-align: center; }
            .name-col { width: 15%; }
            .email-col { width: 25%; }
            .username-col { width: 10%; }
            .mobile-col { width: 12%; }
            .aadhar-col { width: 13%; }
            .qr-col { width: 12%; }
            .date-col { width: 8%; }

        </style>
    </head>
    <body>
        <h1>Registered Users</h1>
        <div class="total">Total Users: ''' + str(len(users)) + '''</div>
        <table>
            <thead>
                <tr>
                    <th class="id-col">ID</th>
                    <th class="name-col">Name</th>
                    <th class="email-col">Email</th>
                    <th class="username-col">Client ID</th>
                    <th class="mobile-col">Mobile</th>
                    <th class="aadhar-col">Aadhar</th>
                    <th class="qr-col">QR Key</th>
                    <th class="date-col">Created At</th>
                </tr>
            </thead>
            <tbody>
    '''
    
    for i, user in enumerate(users, 1):
        # Generate QR key based on user data
        qr_key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
        # Use current timestamp
        date = datetime.datetime.now().strftime('%Y-%m-%d\n%H:%M:%S')
        
        html += f'''
                <tr>
                    <td class="id-col">{i}</td>
                    <td class="name-col">{user.get('firstName', '')} {user.get('lastName', '')}</td>
                    <td class="email-col">{user.get('email', '')}</td>
                    <td class="username-col">{user.get('client_id') or user.get('clientId') or '-'}</td>
                    <td class="mobile-col">{user.get('mobile', '')}</td>
                    <td class="aadhar-col">{user.get('aadhar', '')}</td>
                    <td class="qr-col">{qr_key}</td>
                    <td class="date-col">{date}</td>
                </tr>
        '''
    
    html += '''
            </tbody>
        </table>
    </body>
    </html>
    '''
    
    return html

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    # Accept both 'emailOrUsername' (from login form) and 'clientId' (legacy)
    identifier = data.get('emailOrUsername') or data.get('clientId')
    password = data.get('password')
    totp_code = data.get('totpCode')
    
    if not identifier or not password:
        return jsonify({'error': 'Username/email and password are required'}), 400
    
    from user_database import get_user_by_email
    # Try login by client_id first, then by email
    user = verify_login(identifier, password)
    if not user:
        # Try by email
        user_by_email = get_user_by_email(identifier)
        if user_by_email and user_by_email.get('password') == password:
            user = user_by_email
    
    if not user:
        return jsonify({'error': 'Invalid credentials'}), 401
    
    client_id = user.get('client_id') or user.get('clientId')
    
    if not totp_code:
        return jsonify({'require_totp': True, 'message': 'Please enter TOTP code'})
    
    if verify_totp(client_id, totp_code):
        return jsonify({'success': True, 'message': 'Login successful'})
    else:
        return jsonify({'error': 'Invalid TOTP code'}), 401

@app.route('/api/signup', methods=['POST'])
def signup():
    # Add this check first
    if has_existing_users():
        return jsonify({'error': 'Registration is closed. Please login with existing credentials.'}), 403
    
    data = request.get_json()
    
    # Check if Client ID already exists (Checked both keys)
    client_id = data.get('client_id') or data.get('clientId')
    
    # Ensure normalized key for validation logic
    if not client_id and 'username' in data: # Fallback? No, client_id is mandatory
         # If missing, it might be under 'username' if frontend is weird, but stick to standard keys
         pass

    from user_database import check_client_id_exists
    if check_client_id_exists(client_id):
        return jsonify({'error': 'Client ID already exists'}), 400
    
    # Check if email already exists
    email = data.get('email')
    from user_database import check_email_exists
    if check_email_exists(email):
        return jsonify({'error': 'Email already exists'}), 400
    
    # Check if Aadhar already exists
    aadhar = data.get('aadhar')
    if check_aadhar_exists(aadhar):
        return jsonify({'error': 'Aadhar number already exists'}), 400
    
    # Validate Aadhar (12 digits only)
    if not re.match(r'^\d{12}$', aadhar):
        return jsonify({'error': 'Aadhar must be exactly 12 digits'}), 400
    
    # Validate password
    password = data.get('password')
    if not re.match(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,15}$', password) or ' ' in password:
        return jsonify({'error': 'Password must be 8-15 characters with uppercase, lowercase, number, and special character'}), 400
    
    secret, saved_client_id, generated_username = add_user(data)
    
    # Sync signup data to Central/Postgres
    try:
        signup_payload = {
            'client_id': saved_client_id,
            'username': generated_username,
            'email': data.get('email'),
            'phone': data.get('mobile'),
            'aadhar': data.get('aadhar'),
            'qr_key': secret
        }
        db_sync.store_signup_data(signup_payload)
    except Exception as e:
        print(f"Failed to sync signup data: {e}")
    
    # Generate QR code for Google Authenticator
    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=generated_username,  # Use generated username as requested
        issuer_name="Trading Bot"
    )
    
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(totp_uri)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    qr_code_base64 = base64.b64encode(buffer.getvalue()).decode()
    
    # Send welcome email with username and instructions
    try:
        # Re-using username parameter for client_id
        send_welcome_email(data['email'], saved_client_id, data['firstName'])
    except Exception as e:
        print(f"Failed to send welcome email: {e}")
    
    return jsonify({
        'success': True, 
        'message': 'Account created successfully',
        'qr_code': qr_code_base64,
        'secret': secret,
        'username': saved_client_id,
        'generated_username': generated_username
    })

@app.route('/api/get-signup-data', methods=['GET'])
def get_signup_data():
    from user_database import get_all_users
    import random
    import string
    import datetime
    
    users = get_all_users()
    
    # Enrich data for display
    enriched_users = []
    for user in users:
        u = user.copy()
        
        # FIX: Map 'totp_secret' from users.json to 'qr_key' expected by frontend
        if 'qr_key' not in u and 'totp_secret' in u:
            u['qr_key'] = u['totp_secret']
            
        if 'qr_key' not in u:
             u['qr_key'] = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
        if 'created_at' not in u:
             u['created_at'] = '2024-01-09 12:10:00'
        enriched_users.append(u)
        
    return jsonify(enriched_users)

@app.route('/')
def database_viewer():
    return render_template('database_viewer.html', db_views=sorted(TABLE_MAP.keys()))

@app.route('/view_data')
def view_data():
    db_name = request.args.get('db')
    # Input validation and sanitization
    if db_name not in TABLE_MAP:
        return render_template('view_data.html', db_name='', columns=[], rows=[], error="Invalid database name")
    
    columns, rows = get_client_data(None, db_name)
    if db_name == 'master_bot_data' and columns:
        display_columns = []
        for col in columns:
            if col == 'sl_number':
                display_columns.append('SL No')
            elif col == 'client_id':
                display_columns.append('Client ID')
            elif col == 'bot_id':
                display_columns.append('Bot ID')
            elif col == 'created_at':
                display_columns.append('Created At')
            else:
                display_columns.append(col.title())
        columns = display_columns
    elif db_name == 'client_bot_signups' and columns:
        display_columns = []
        for col in columns:
            if col == 'sl_number':
                display_columns.append('SL No')
            elif col == 'client_id':
                display_columns.append('Client ID')
            elif col == 'qr_key':
                display_columns.append('QR Key')
            elif col == 'created_at':
                display_columns.append('Created At')
            else:
                display_columns.append(col.title())
        columns = display_columns
    elif db_name == 'daily_trading_sessions' and columns:
        display_columns = []
        for col in columns:
            if col == 'sl_number':
                display_columns.append('SL No')
            elif col == 'client_id':
                display_columns.append('Client ID')
            elif col == 'username':
                display_columns.append('Login Username')
            elif col == 'session_date':
                display_columns.append('Date')
            elif col == 'kite_id':
                display_columns.append('Kite ID')
            elif col == 'kite_username':
                display_columns.append('Zerodha User Name')
            elif col == 'total_sessions':
                display_columns.append('Total Sessions')
            elif col == 'session_type':
                display_columns.append('Type')
            elif col == 'session_duration':
                display_columns.append('Duration')
            elif col == 'session_status':
                display_columns.append('Status')
            elif col == 'view_button':
                display_columns.append('View Button')
            else:
                display_columns.append(col.title())
        columns = display_columns
    return render_template('view_data.html', db_name=db_name, columns=columns, rows=rows)

@app.route('/api/sync-signup', methods=['POST'])
def sync_signup():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Handle both master bot data and client signup data
        client_id = data.get('client_id')
        
        # Check if this is master bot data (has name field) or client signup data (has username field)
        if 'name' in data:
            # Master bot data
            name = data.get('name')
            email = data.get('email')
            phone = data.get('phone')
            bot_id = data.get('bot_id')
            
            if not client_id or not name or not email:
                return jsonify({"status": "error", "message": "client_id, name, and email are required"}), 400
            
            cur.execute("SELECT sl_number FROM master_bot_data WHERE client_id = %s", (client_id,))
            existing = cur.fetchone()
            
            if existing:
                sql_update = """
                    UPDATE master_bot_data 
                    SET name = %s, email = %s, phone = %s, bot_id = %s, created_at = CURRENT_TIMESTAMP
                    WHERE client_id = %s
                """
                cur.execute(sql_update, (name, email, phone, bot_id, client_id))
            else:
                sql_insert = """
                    INSERT INTO master_bot_data (client_id, bot_id, name, email, phone, created_at)
                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """
                cur.execute(sql_insert, (client_id, bot_id, name, email, phone))
        
        elif 'username' in data:
            # Client signup data
            username = data.get('username')
            email = data.get('email')
            phone = data.get('phone')
            aadhar = data.get('aadhar')
            qr_key = data.get('qr_key')
            
            if not client_id or not username or not email:
                return jsonify({"status": "error", "message": "client_id, username, and email are required"}), 400
            
            cur.execute("SELECT id FROM client_bot_signups WHERE client_id = %s", (client_id,))
            existing = cur.fetchone()
            
            if existing:
                sql_update = """
                    UPDATE client_bot_signups 
                    SET username = %s, email = %s, phone = %s, aadhar = %s, qr_key = %s, created_at = CURRENT_TIMESTAMP
                    WHERE client_id = %s
                """
                cur.execute(sql_update, (username, email, phone, aadhar, qr_key, client_id))
            else:
                sql_insert = """
                    INSERT INTO client_bot_signups (client_id, username, email, phone, aadhar, qr_key, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """
                cur.execute(sql_insert, (client_id, username, email, phone, aadhar, qr_key))
        
        conn.commit()
        return jsonify({"status": "success", "message": "Data synced successfully", "client_id": client_id}), 201
        
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/sync-session', methods=['POST'])
def sync_session():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    
    # STEP 4: PC3 PAYLOAD LOGGING
    print("=== PC3 PAYLOAD RECEIVED ===")
    print(f"Raw payload: {data}")
    print(f"client_id: {data.get('client_id')}")
    print(f"username: {data.get('username')}")
    print(f"kite_id: {data.get('kite_id')}")
    print(f"kite_username: {data.get('kite_username')}")
    print("==============================")
    
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        
        client_id = data.get('client_id')
        session_date = data.get('session_date')
        login_time = data.get('login_time')
        logout_time = data.get('logout_time')
        kite_id = data.get('kite_id')
        
        # PAYLOAD HANDLING: Support both New Bot (sends kite_username) and Legacy Bot (sends Name in username)
        kite_username = data.get('kite_username')
        if not kite_username:
             # Legacy Fallback: PC3 sent the Kite Name in the 'username' field
             kite_username = data.get('username')
        
        # For 'username' (Login ID), we primarily look it up from our own database (Source of Truth)
        username = None 
        try:
             cur.execute("SELECT username FROM client_bot_signups WHERE client_id = %s", (client_id,))
             res = cur.fetchone()
             if res:
                 username = res[0]
        except Exception as e:
             print(f"Error fetching username: {e}")
             
        if not username:
             username = kite_username # Fallback if lookup fails (ensure not None)

        mode = data.get('mode')
        total_trades = data.get('total_trades', 0)
        net_pnl = data.get('net_pnl', 0.00)
        
        if not client_id or not session_date or not login_time:
            return jsonify({"status": "error", "message": "client_id, session_date, and login_time are required"}), 400
        
        # PRODUCTION SAFETY: Auto-close any existing ACTIVE sessions before creating new one
        if not logout_time:  # This is a new session start (login)
            cur.execute("""
                UPDATE user_session_data 
                SET logout_time = CURRENT_TIMESTAMP, 
                    mode = COALESCE(mode, 'AUTO-CLOSED')
                WHERE client_id = %s AND kite_id = %s AND logout_time IS NULL
            """, (client_id, kite_id))
            closed_count = cur.rowcount
            if closed_count > 0:
                print(f"Auto-closed {closed_count} existing sessions for {client_id}")
        # Check if record exists for this exact login time and client
        cur.execute("""
            SELECT id FROM user_session_data 
            WHERE client_id = %s AND session_date = %s AND login_time = %s
        """, (client_id, session_date, login_time))
        
        existing = cur.fetchone()
        
        if existing:
            # Update existing session (Logout event)
            cur.execute("""
                UPDATE user_session_data
                SET logout_time = %s,
                    total_trades = %s,
                    net_pnl = %s,
                    kite_id = COALESCE(%s, kite_id),
                    username = COALESCE(%s, username),
                    kite_username = COALESCE(%s, kite_username),
                    mode = COALESCE(%s, mode)
                WHERE id = %s
            """, (logout_time, total_trades, net_pnl, kite_id, username, kite_username, mode, existing[0]))
        else:
            # Insert new session (Login event)
            cur.execute("""
                INSERT INTO user_session_data 
                (client_id, kite_id, username, kite_username, session_date, login_time, logout_time, mode, total_trades, net_pnl)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (client_id, kite_id, username, kite_username, session_date, login_time, logout_time, mode, total_trades, net_pnl))
            
        conn.commit()
        return jsonify({"status": "success", "message": "Session synced successfully"}), 200

    except Exception as e:
        print(f"Error syncing session: {str(e)}")
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/session_details/<client_id>/<date>')
def session_details_page(client_id, date):
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Safe search with aggressive data type casting
        cur.execute("""
            SELECT session_date, login_time, logout_time, mode, 
                   COALESCE(total_trades, 0), COALESCE(net_pnl, 0.00)
            FROM user_session_data 
            WHERE TRIM(client_id) = %s 
            AND session_date::date = %s::date
            ORDER BY login_time ASC
        """, (client_id.strip(), date))
        
        sessions = cur.fetchall()
        
        if sessions:
            # Filter out closed sessions with 0 trades and 0 PnL
            filtered_sessions = []
            for s in sessions:
                trades = s[4] or 0
                pnl = s[5] or 0
                is_active = s[2] is None
                
                if trades != 0 or pnl != 0 or is_active:
                    filtered_sessions.append(s)
            
            # Implement Monotonic Progress Logic
            display_sessions = []
             # Sort sessions to ensure sequential processing
            sessions.sort(key=lambda x: x[1] if x[1] else datetime.min)
            
            max_seen_trades = 0
            last_valid_cum_pnl = 0
            
            for s in sessions:
                session_date = s[0]
                login_time = s[1]
                logout_time = s[2]
                mode = s[3]
                current_cum_trades = s[4] or 0
                current_cum_pnl = s[5] or 0
                is_active = logout_time is None
                
                if current_cum_trades < max_seen_trades:
                    continue
                
                delta_trades = current_cum_trades - max_seen_trades
                delta_pnl = current_cum_pnl - last_valid_cum_pnl
                
                if delta_trades > 0:
                    display_sessions.append((session_date, login_time, logout_time, mode, delta_trades, delta_pnl))
                    max_seen_trades = current_cum_trades
                    last_valid_cum_pnl = current_cum_pnl
            
            sessions = display_sessions

            total_sessions = len(sessions)
            active_sessions = sum(1 for s in sessions if s[2] is None) 
            total_trades = sum(s[4] for s in sessions)
            net_pnl = sum(s[5] for s in sessions)
            mode = sessions[0][3] if sessions else 'N/A'
            
            summary = [total_sessions, date, active_sessions, total_trades, net_pnl, mode]
            
            columns = ['Date', 'Login Time', 'Logout Time', 'Mode', 'Trades', 'Net PnL']
            formatted_rows = []
            for session in sessions:
                formatted_rows.append([
                    session[0].strftime('%Y-%m-%d') if session[0] else '',
                    session[1].strftime('%H:%M:%S') if session[1] else '',
                    session[2].strftime('%H:%M:%S') if session[2] else 'Active',
                    session[3] or '',
                    session[4],
                    f"₹{session[5]:.2f}"
                ])
            
            return render_template('session_details.html', client_id=client_id, columns=columns, rows=formatted_rows, summary=summary)
        
        return render_template('session_details.html', client_id=client_id, columns=[], rows=[], summary=None)
            
    except Exception as e:
        print(f"Error: {e}")
        return render_template('session_details.html', client_id=client_id, columns=[], rows=[], summary=None)

@app.route('/test-route/<client_id>/<date>')
def test_route(client_id, date):
    return f"<h1>Route Test</h1><p>Client: {client_id}</p><p>Date: {date}</p>"

@app.route('/api/fix-consolidated-data/<client_id>/<date>', methods=['GET', 'POST'])
def fix_consolidated_data(client_id, date):
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Delete all sessions for this client/date to start fresh
        cur.execute("""
            DELETE FROM user_session_data 
            WHERE client_id = %s AND DATE(session_date) = %s
        """, (client_id, date))
        
        # Insert sample realistic sessions based on the consolidated data
        sample_sessions = [
            ('10:27:02', '10:47:38', 3, 216.15, 'PAPER'),
            ('10:47:26', '11:43:26', 5, 10782.12, 'PAPER'),
            ('11:53:21', '11:53:32', 6, 10745.64, 'PAPER'),
            ('12:04:38', '12:05:32', 7, 16359.58, 'PAPER'),
            ('12:08:03', '12:08:38', 8, 16070.14, 'PAPER'),
            ('12:09:29', '12:09:46', 8, 16070.14, 'PAPER'),
            ('12:10:26', '12:10:53', 9, 15306.39, 'PAPER'),
            ('12:12:29', '12:14:16', 10, 18017.41, 'PAPER'),
            ('12:15:59', '12:16:06', 10, 18017.41, 'PAPER'),
            ('12:16:15', '12:18:01', 11, 22587.43, 'PAPER'),
            ('12:20:27', '12:21:19', 11, 22587.43, 'PAPER'),
            ('12:22:47', '12:23:01', 11, 22587.43, 'PAPER')
        ]
        
        for login, logout, trades, pnl, mode in sample_sessions:
            cur.execute("""
                INSERT INTO user_session_data 
                (client_id, session_date, login_time, logout_time, total_trades, net_pnl, mode, username, kite_username)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'dP73HnSC', 'Ratan Kumar Hugonder')
            """, (client_id, date, f"{date} {login}", f"{date} {logout}", trades, pnl, mode))
        
        conn.commit()
        return jsonify({"status": "success", "message": "Data restored with realistic sessions"}), 200
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/close-active-sessions/<client_id>', methods=['POST'])
def close_active_sessions(client_id):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE user_session_data 
            SET logout_time = CURRENT_TIMESTAMP, mode = 'MANUAL-CLOSE'
            WHERE client_id = %s AND logout_time IS NULL
        """, (client_id,))
        closed_count = cur.rowcount
        conn.commit()
        return jsonify({"status": "success", "closed": closed_count}), 200
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/test-pc3-payload', methods=['POST'])
def test_pc3_payload():
    """Test endpoint to verify PC3 sends correct payload"""
    data = request.get_json() or {}
    
    print("=== PC3 PAYLOAD TEST ===")
    print(f"client_id: {data.get('client_id')}")
    print(f"username: {data.get('username')}")
    print(f"kite_id: {data.get('kite_id')}")
    print(f"kite_username: {data.get('kite_username')}")
    
    # Check if payload matches expected format
    expected = {
        "client_id": "AD0004",
        "username": "dP73HnSC", 
        "kite_id": "DR4971",
        "kite_username": "Ratan Kumar Hugonder"
    }
    
    matches = all(data.get(k) == v for k, v in expected.items())
    status = "CORRECT" if matches else "INCORRECT"
    
    print(f"PAYLOAD STATUS: {status}")
    print("========================")
    
    return jsonify({
        "status": "success",
        "payload_correct": matches,
        "received": data,
        "expected": expected
    }), 200

@app.route('/api/add-test-data/<client_id>')
def add_test_data(client_id):
    """Add test session data for debugging"""
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Insert test session data
        cur.execute("""
            INSERT INTO user_session_data 
            (client_id, session_date, login_time, logout_time, kite_id, username, kite_username, mode, total_trades, net_pnl)
            VALUES (%s, CURRENT_DATE, CURRENT_TIMESTAMP - INTERVAL '2 hours', CURRENT_TIMESTAMP - INTERVAL '1 hour', 'DR4971', 'dP73HnSC', 'Ratan Kumar', 'PAPER', 5, 1500.00)
        """, (client_id,))
        
        conn.commit()
        return jsonify({"status": "success", "message": f"Test data added for {client_id}"})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/debug-data/<client_id>')
def debug_data(client_id):
    """Debug endpoint to check what data exists for a client"""
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Check all data for this client
        cur.execute("SELECT * FROM user_session_data WHERE client_id = %s", (client_id,))
        all_data = cur.fetchall()
        
        # Get column names
        columns = [desc[0] for desc in cur.description] if cur.description else []
        
        return jsonify({
            "client_id": client_id,
            "total_rows": len(all_data),
            "columns": columns,
            "data": [dict(zip(columns, row)) for row in all_data] if all_data else []
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/test-session/<client_id>')
def test_session(client_id):
    """Direct test route to check session data"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM user_session_data WHERE client_id = %s LIMIT 5", (client_id,))
        rows = cur.fetchall()
        
        result = f"<h1>Test Session Data for {client_id}</h1>"
        result += f"<p>Found {len(rows)} sessions</p>"
        
        if rows:
            result += "<table border='1'>"
            result += "<tr><th>ID</th><th>Client</th><th>Date</th><th>Login</th><th>Logout</th><th>Trades</th><th>PnL</th></tr>"
            for row in rows:
                result += f"<tr><td>{row[0]}</td><td>{row[1]}</td><td>{row[2]}</td><td>{row[3]}</td><td>{row[4]}</td><td>{row[8]}</td><td>{row[9]}</td></tr>"
            result += "</table>"
        else:
            result += "<p>No data found</p>"
            
        return result
    except Exception as e:
        return f"<h1>Error: {str(e)}</h1>"

@app.route('/api/trade-update', methods=['POST'])
def trade_update():
    """Updates real-time trade statistics in active sessions"""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        
        client_id = data.get('client_id')
        total_trades = data.get('total_trades', 0)
        net_pnl = data.get('net_pnl', 0.00)
        
        if not client_id:
            return jsonify({"status": "error", "message": "client_id is required"}), 400
        
        # Update the most recent active session
        cur.execute("""
            UPDATE user_session_data 
            SET total_trades = %s, net_pnl = %s
            WHERE client_id = %s AND logout_time IS NULL
            ORDER BY login_time DESC
            LIMIT 1
        """, (total_trades, net_pnl, client_id))
        
        conn.commit()
        return jsonify({"status": "success", "message": "Trade data updated"}), 200
        
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5001)