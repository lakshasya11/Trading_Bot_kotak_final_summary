import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import os
from dotenv import load_dotenv

# Load from login/server/.env — try multiple path strategies
_base = os.path.dirname(os.path.abspath(__file__))
_env_paths = [
    os.path.join(_base, '..', '..', 'login', 'server', '.env'),   # relative to core/
    os.path.join(_base, '..', 'login', 'server', '.env'),          # relative to backend/
    os.path.join(os.getcwd(), 'login', 'server', '.env'),          # relative to cwd
    os.path.join(os.getcwd(), '..', 'login', 'server', '.env'),    # one level up from cwd
]
for _p in _env_paths:
    if os.path.exists(os.path.normpath(_p)):
        load_dotenv(os.path.normpath(_p))
        break

class EmailNotifier:
    @staticmethod
    def send_login_notification(client_id: str, name: str, kite_id: str, mode: str, login_time: datetime, date: str):
        """Send email notification when user starts the bot"""
        try:
            recipient_email = os.getenv('NOTIFICATION_EMAIL')
            if not recipient_email:
                print("NOTIFICATION_EMAIL not found in .env file")
                return
            
            subject = f"🟢 Bot Started - {name}"
            body = f"""
<html>
<body style="font-family: Arial, sans-serif;">
    <h2 style="color: #28a745;">Bot Login Notification</h2>
    <table style="border-collapse: collapse; width: 100%;">
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Client ID:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{client_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>UCC:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{kite_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>User Name:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{name}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Mode:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">
                <span style="background-color: {'#3b82f6' if mode == 'PAPER' else '#f59e0b'}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;">{mode}</span>
            </td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Login Time:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{login_time.strftime('%I:%M:%S %p')}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Date:</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{date}</td>
        </tr>
    </table>
</body>
</html>
"""
            EmailNotifier._send_email(recipient_email, subject, body)
            print(f"✅ Login notification sent to {recipient_email}")
        except Exception as e:
            print(f"❌ Failed to send login notification: {e}")
    
    @staticmethod
    def send_logout_notification(client_id: str, name: str, kite_id: str, mode: str, 
                                 login_time: datetime, logout_time: datetime, 
                                 total_trades: int, net_pnl: float, trades: list = None):
        """Send email notification with trade history when user stops the bot"""
        try:
            recipient_email = os.getenv('NOTIFICATION_EMAIL')
            if not recipient_email:
                print("NOTIFICATION_EMAIL not found in .env file")
                return
            
            pnl_color = "#28a745" if net_pnl >= 0 else "#dc3545"
            is_daily_summary = trades is not None and len(trades) > 0
            if is_daily_summary:
                subject = f"📊 Daily Trade Summary - {name} (Net P&L: ₹{net_pnl:.2f})"
            else:
                subject = f"🔴 Bot Stopped - {name} (Net P&L: ₹{net_pnl:.2f})"
            
            # Build trade history table if available
            trades_html = ""
            if trades and len(trades) > 0:
                trades_html = """
    <h3 style="margin-top: 30px; color: #333;">Daily Trade History</h3>
    <table style="border-collapse: collapse; width: 100%; font-size: 11px;">
        <thead>
            <tr style="background-color: #f2f2f2;">
                <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Entry Time</th>
                <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Exit Time</th>
                <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Symbol</th>
                <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Type</th>
                <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Mode</th>
                <th style="padding: 10px; border: 1px solid #ddd; text-align: right;">Entry Price</th>
                <th style="padding: 10px; border: 1px solid #ddd; text-align: right;">Exit Price</th>
                <th style="padding: 10px; border: 1px solid #ddd; text-align: right;">Qty</th>
                <th style="padding: 10px; border: 1px solid #ddd; text-align: right;">PnL</th>
                <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Reason</th>
            </tr>
        </thead>
        <tbody>
"""
                for t in trades:
                    t_pnl = t.get('net_pnl') or t.get('pnl') or 0
                    t_pnl_color = "#28a745" if t_pnl >= 0 else "#dc3545"
                    
                    e_time = t.get('entry_time', t.get('timestamp', ''))
                    x_time = t.get('exit_time', t.get('timestamp', ''))
                    
                    if isinstance(e_time, str) and len(e_time) > 11: e_time = e_time[11:19]
                    if isinstance(x_time, str) and len(x_time) > 11: x_time = x_time[11:19]
                    
                    t_mode = t.get('trading_mode', 'N/A')
                    if t_mode == 'Live Trading': t_mode = 'LIVE'
                    elif t_mode == 'Paper Trading': t_mode = 'PAPER'

                    trades_html += f"""
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;">{e_time}</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{x_time}</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{t.get('symbol', 'N/A')}</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{t.get('direction', 'N/A')}</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{t_mode}</td>
                <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{t.get('entry_price', 0):.2f}</td>
                <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{t.get('exit_price', 0):.2f}</td>
                <td style="padding: 8px; border: 1px solid #ddd; text-align: right;">{t.get('quantity', 0)}</td>
                <td style="padding: 8px; border: 1px solid #ddd; text-align: right; color: {t_pnl_color}; font-weight: bold;">₹{t_pnl:.2f}</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{t.get('exit_reason', 'N/A')}</td>
            </tr>
"""
                trades_html += "        </tbody>\n    </table>"
            else:
                trades_html = "<p style='color: #666;'>No trades were executed in this session.</p>"

            body = f"""
<html>
<body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
    <div style="max-width: 800px; margin: 0 auto; border: 1px solid #eee; padding: 20px; border-radius: 8px;">
        <h2 style="color: {'#28a745' if is_daily_summary else '#dc3545'}; border-bottom: 2px solid {'#28a745' if is_daily_summary else '#dc3545'}; padding-bottom: 10px;">{'Daily Performance Summary' if is_daily_summary else 'Bot Session Report'}</h2>
        
        <h3 style="color: #555;">Session Summary</h3>
        <table style="border-collapse: collapse; width: 100%; margin-bottom: 20px;">
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9; width: 35%;"><strong>Client ID:</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{client_id}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>UCC:</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{kite_id}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>User Name:</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{name}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Mode:</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">
                    <span style="background-color: {'#3b82f6' if mode == 'PAPER' else '#f59e0b'}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;">{mode}</span>
                </td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Login Time:</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{login_time.strftime('%Y-%m-%d %I:%M:%S %p')}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Logout Time:</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{logout_time.strftime('%Y-%m-%d %I:%M:%S %p')}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Total Trades:</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{total_trades}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Final Net P&L:</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd; color: {pnl_color}; font-weight: bold; font-size: 20px;">₹{net_pnl:.2f}</td>
            </tr>
        </table>

        {trades_html}
        
        <div style="margin-top: 30px; font-size: 11px; color: #999; text-align: center; border-top: 1px solid #eee; padding-top: 10px;">
            This is an automated report from your Kotak Breakout Trading Bot.
        </div>
    </div>
</body>
</html>
"""
            EmailNotifier._send_email(recipient_email, subject, body)
            print(f"✅ Logout notification with trade history sent to {recipient_email}")
        except Exception as e:
            print(f"❌ Failed to send logout notification: {e}")
    
    @staticmethod
    def _send_email(to_email: str, subject: str, html_body: str):
        """Internal method to send email using Gmail SMTP"""
        sender_email = os.getenv('SMTP_EMAIL')
        sender_password = os.getenv('SMTP_PASSWORD')
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        
        if not sender_email or not sender_password:
            print("SMTP_EMAIL or SMTP_PASSWORD not found in login/server/.env file")
            return
        
        msg = MIMEMultipart('alternative')
        msg['From'] = sender_email
        msg['To'] = to_email
        msg['Subject'] = subject
        
        msg.attach(MIMEText(html_body, 'html'))
        
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
