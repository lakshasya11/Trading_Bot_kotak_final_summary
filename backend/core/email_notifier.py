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
<body style="font-family: Arial, sans-serif; color: #333;">
    <h2 style="color: #28a745; border-bottom: 2px solid #28a745; padding-bottom: 8px;">Bot Login Notification</h2>
    <table style="border-collapse: collapse; width: 100%; max-width: 500px;">
        <tr>
            <td style="padding: 8px 12px; border: 1px solid #ddd; background-color: #f9f9f9; width: 40%;"><strong>Client ID:</strong></td>
            <td style="padding: 8px 12px; border: 1px solid #ddd;">{client_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px 12px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>UCC:</strong></td>
            <td style="padding: 8px 12px; border: 1px solid #ddd;">{kite_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px 12px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>User Name:</strong></td>
            <td style="padding: 8px 12px; border: 1px solid #ddd;">{name}</td>
        </tr>
        <tr>
            <td style="padding: 8px 12px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Mode:</strong></td>
            <td style="padding: 8px 12px; border: 1px solid #ddd;">
                <span style="background-color: {'#3b82f6' if mode == 'PAPER' else '#f59e0b'}; color: white; padding: 3px 10px; border-radius: 4px; font-size: 12px; font-weight: bold;">{mode}</span>
            </td>
        </tr>
        <tr>
            <td style="padding: 8px 12px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Login Time:</strong></td>
            <td style="padding: 8px 12px; border: 1px solid #ddd;">{login_time.strftime('%I:%M:%S %p')}</td>
        </tr>
        <tr>
            <td style="padding: 8px 12px; border: 1px solid #ddd; background-color: #f9f9f9;"><strong>Date:</strong></td>
            <td style="padding: 8px 12px; border: 1px solid #ddd;">{date}</td>
        </tr>
    </table>
    <p style="font-size: 11px; color: #999; margin-top: 20px;">This is an automated notification from your Kotak Breakout Trading Bot.</p>
</body>
</html>
"""
            EmailNotifier._send_email(recipient_email, subject, body)
            print(f"Login notification sent to {recipient_email}")
        except Exception as e:
            print(f"Failed to send login notification: {e}")
    
    @staticmethod
    def _build_trades_table(trades: list, net_pnl: float, pnl_color: str) -> str:
        """Build HTML trade history table"""
        if not trades:
            return "<p style='color:#666;'>No trades were executed in this session.</p>"
        rows_html = ""
        for i, t in enumerate(trades, 1):
            e_time = str(t.get('entry_time', ''))[11:19] or 'N/A'
            x_time = str(t.get('exit_time', t.get('timestamp', '')))[11:19] or 'N/A'
            t_pnl = t.get('net_pnl') or t.get('pnl') or 0
            t_color = "#28a745" if t_pnl >= 0 else "#dc3545"
            t_mode = t.get('trading_mode', 'N/A')
            if 'Paper' in str(t_mode): t_mode = 'PAPER'
            elif 'Live' in str(t_mode): t_mode = 'LIVE'
            bg = "#ffffff" if i % 2 == 0 else "#f9f9f9"
            rows_html += f"""
            <tr style="background-color:{bg};">
                <td style="padding:6px 8px;border:1px solid #ddd;text-align:center;">{i}</td>
                <td style="padding:6px 8px;border:1px solid #ddd;">{e_time}</td>
                <td style="padding:6px 8px;border:1px solid #ddd;">{x_time}</td>
                <td style="padding:6px 8px;border:1px solid #ddd;">{t.get('symbol','N/A')}</td>
                <td style="padding:6px 8px;border:1px solid #ddd;text-align:center;">{t.get('direction','N/A')}</td>
                <td style="padding:6px 8px;border:1px solid #ddd;text-align:center;">{t_mode}</td>
                <td style="padding:6px 8px;border:1px solid #ddd;text-align:right;">{t.get('entry_price',0):.2f}</td>
                <td style="padding:6px 8px;border:1px solid #ddd;text-align:right;">{t.get('exit_price',0):.2f}</td>
                <td style="padding:6px 8px;border:1px solid #ddd;text-align:right;">{t.get('quantity',0)}</td>
                <td style="padding:6px 8px;border:1px solid #ddd;text-align:right;color:{t_color};font-weight:bold;">&#8377;{t_pnl:.2f}</td>
                <td style="padding:6px 8px;border:1px solid #ddd;">{t.get('exit_reason','N/A')}</td>
            </tr>"""
        return f"""
    <h3 style="color:#555;margin-top:24px;">Trade History</h3>
    <table style="border-collapse:collapse;width:100%;font-size:12px;">
      <thead>
        <tr style="background-color:#2563eb;color:white;">
          <th style="padding:8px;border:1px solid #ddd;">#</th>
          <th style="padding:8px;border:1px solid #ddd;">Entry Time</th>
          <th style="padding:8px;border:1px solid #ddd;">Exit Time</th>
          <th style="padding:8px;border:1px solid #ddd;">Symbol</th>
          <th style="padding:8px;border:1px solid #ddd;">Type</th>
          <th style="padding:8px;border:1px solid #ddd;">Mode</th>
          <th style="padding:8px;border:1px solid #ddd;">Entry Price</th>
          <th style="padding:8px;border:1px solid #ddd;">Exit Price</th>
          <th style="padding:8px;border:1px solid #ddd;">Qty</th>
          <th style="padding:8px;border:1px solid #ddd;">PnL</th>
          <th style="padding:8px;border:1px solid #ddd;">Reason</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
      <tfoot>
        <tr style="background-color:#f2f2f2;font-weight:bold;">
          <td colspan="9" style="padding:8px;border:1px solid #ddd;text-align:right;">Total Net P&amp;L:</td>
          <td style="padding:8px;border:1px solid #ddd;text-align:right;color:{pnl_color};font-size:16px;">&#8377;{net_pnl:.2f}</td>
          <td style="border:1px solid #ddd;"></td>
        </tr>
      </tfoot>
    </table>"""

    @staticmethod
    def send_logout_notification(client_id: str, name: str, kite_id: str, mode: str,
                                 login_time: datetime, logout_time: datetime,
                                 total_trades: int, net_pnl: float, wins: int = 0, losses: int = 0, trades: list = None):
        """Send email notification with trade history when user stops the bot"""
        try:
            recipient_email = os.getenv('NOTIFICATION_EMAIL')
            if not recipient_email:
                print("NOTIFICATION_EMAIL not found in .env file")
                return

            pnl_color = "#28a745" if net_pnl >= 0 else "#dc3545"
            subject = f"Bot Stopped - {name} (Net P&L: Rs.{net_pnl:.2f})"
            trades_html = EmailNotifier._build_trades_table(trades or [], net_pnl, pnl_color)

            body = f"""
<html>
<body style="font-family:Arial,sans-serif;color:#333;">
  <div style="max-width:1100px;margin:0 auto;padding:20px;">
    <h2 style="color:#dc3545;border-bottom:2px solid #dc3545;padding-bottom:8px;">Bot Session Report</h2>
    <h3 style="color:#555;margin-bottom:10px;">Session Summary</h3>
    <table style="border-collapse:collapse;width:100%;max-width:500px;margin-bottom:20px;">
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;width:40%;"><strong>Client ID:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;">{client_id}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>UCC:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;">{kite_id}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>User Name:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;">{name}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Mode:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;"><span style="background-color:{'#3b82f6' if mode == 'PAPER' else '#f59e0b'};color:white;padding:3px 10px;border-radius:4px;font-size:12px;font-weight:bold;">{mode}</span></td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Login Time:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;">{login_time.strftime('%Y-%m-%d %I:%M:%S %p')}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Logout Time:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;">{logout_time.strftime('%Y-%m-%d %I:%M:%S %p')}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Total Trades:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;">{total_trades}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Wins:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;color:#28a745;font-weight:bold;">{wins}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Losses:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;color:#dc3545;font-weight:bold;">{losses}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Final Net P&amp;L:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;color:{pnl_color};font-weight:bold;font-size:20px;">&#8377;{net_pnl:.2f}</td></tr>
    </table>
    {trades_html}
    <p style="font-size:11px;color:#999;margin-top:20px;">This is an automated report from your Kotak Breakout Trading Bot.</p>
  </div>
</body>
</html>
"""
            EmailNotifier._send_email(recipient_email, subject, body)
            print(f"Logout notification sent to {recipient_email}")
        except Exception as e:
            print(f"Failed to send logout notification: {e}")
    
    @staticmethod
    def send_daily_summary(client_id: str, name: str, kite_id: str, mode: str,
                           total_trades: int, net_pnl: float, date: str,
                           wins: int = 0, losses: int = 0, trades: list = None):
        """Send daily summary email at 15:31 PM with full trade history"""
        try:
            recipient_email = os.getenv('NOTIFICATION_EMAIL')
            if not recipient_email:
                print("NOTIFICATION_EMAIL not found in .env file")
                return

            pnl_color = "#28a745" if net_pnl >= 0 else "#dc3545"
            subject = f"Daily Summary - {name} | {date} (Net P&L: Rs.{net_pnl:.2f})"
            trades_html = EmailNotifier._build_trades_table(trades or [], net_pnl, pnl_color)

            body = f"""
<html>
<body style="font-family:Arial,sans-serif;color:#333;">
  <div style="max-width:1100px;margin:0 auto;padding:20px;">
    <h2 style="color:#2563eb;border-bottom:2px solid #2563eb;padding-bottom:8px;">Daily Summary Report</h2>
    <table style="border-collapse:collapse;width:100%;max-width:500px;margin-bottom:20px;">
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;width:40%;"><strong>Client ID:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;">{client_id}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>UCC:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;">{kite_id}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>User Name:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;">{name}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Mode:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;"><span style="background-color:{'#3b82f6' if mode == 'PAPER' else '#f59e0b'};color:white;padding:3px 10px;border-radius:4px;font-size:12px;font-weight:bold;">{mode}</span></td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Date:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;">{date}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Total Trades:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;">{total_trades}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Wins:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;color:#28a745;font-weight:bold;">{wins}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Losses:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;color:#dc3545;font-weight:bold;">{losses}</td></tr>
        <tr><td style="padding:8px 12px;border:1px solid #ddd;background-color:#f9f9f9;"><strong>Final Net P&amp;L:</strong></td><td style="padding:8px 12px;border:1px solid #ddd;color:{pnl_color};font-weight:bold;font-size:20px;">&#8377;{net_pnl:.2f}</td></tr>
    </table>
    {trades_html}
    <p style="font-size:11px;color:#999;margin-top:20px;">This is an automated daily report from your Kotak Breakout Trading Bot.</p>
  </div>
</body>
</html>
"""
            EmailNotifier._send_email(recipient_email, subject, body)
            print(f"Daily summary email sent to {recipient_email}")
        except Exception as e:
            print(f"Failed to send daily summary: {e}")

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
