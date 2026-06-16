#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   ALERTS SYSTEM v2 — Email & SMS Notifications                 ║
║   (Integrated with options_manager.py)                           ║
║                                                                  ║
║   Real-time breach alerts, profit targets, stop losses          ║
║   Email via SMTP, SMS via Twilio                                ║
║   Understands strategy state, position context, Greeks          ║
╚══════════════════════════════════════════════════════════════════╝
"""

import smtplib
import time
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    from twilio.rest import Client
    HAS_TWILIO = True
except ImportError:
    HAS_TWILIO = False


# ═══════════════════════════════════════════════════════════════════
#  EMAIL ALERT HANDLER — options_manager.py aware
# ═══════════════════════════════════════════════════════════════════

class EmailAlertHandler:
    """Sends alert emails via SMTP. Context-aware for options trading."""
    
    def __init__(self, smtp_config: Dict):
        """
        Initialize email handler.
        
        Args:
            smtp_config: {
                'sender_email': 'your_email@gmail.com',
                'sender_password': 'app_password',  # Use app password for Gmail
                'smtp_server': 'smtp.gmail.com',
                'smtp_port': 587,
                'recipient_emails': ['alert@example.com'],
            }
        """
        self.config = smtp_config
        self.sender_email = smtp_config.get('sender_email')
        self.sender_password = smtp_config.get('sender_password')
        self.smtp_server = smtp_config.get('smtp_server', 'smtp.gmail.com')
        self.smtp_port = smtp_config.get('smtp_port', 587)
        self.recipients = smtp_config.get('recipient_emails', [])
        self.rate_limit = {}  # Track alert frequency per alert type
        
    def send_alert(self, alert_type: str, subject: str, message: str,
                   severity: str = 'INFO', context: Optional[Dict] = None) -> bool:
        """
        Send alert email.
        
        Args:
            alert_type: 'BREACH', 'PROFIT_TARGET', 'STOP_LOSS', 'ADJUSTMENT'
            subject: Email subject
            message: Alert message body
            severity: 'INFO', 'WARNING', 'CRITICAL'
            context: Dict with strategy/position details (from options_manager state)
        """
        # Rate limiting: Don't spam same alert type
        if self._is_rate_limited(alert_type):
            return False
        
        try:
            # Create HTML email
            html_body = self._format_html_email(
                alert_type, message, severity, context
            )
            
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"[{severity}] {subject}"
            msg['From'] = self.sender_email
            msg['To'] = ', '.join(self.recipients)
            
            msg.attach(MIMEText(html_body, 'html'))
            
            # Send via SMTP
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                server.sendmail(
                    self.sender_email,
                    self.recipients,
                    msg.as_string()
                )
            
            self._record_alert(alert_type)
            print(f"📧 Alert email sent: {subject}")
            return True
            
        except Exception as e:
            print(f"❌ Email send failed: {e}")
            return False
    
    def _is_rate_limited(self, alert_type: str, min_interval_sec: int = 300) -> bool:
        """Check if alert type should be rate limited (5 min cooldown)."""
        now = time.time()
        last_alert = self.rate_limit.get(alert_type, 0)
        return (now - last_alert) < min_interval_sec
    
    def _record_alert(self, alert_type: str) -> None:
        """Record alert timestamp for rate limiting."""
        self.rate_limit[alert_type] = time.time()
    
    def _format_html_email(self, alert_type: str, message: str,
                          severity: str, context: Optional[Dict] = None) -> str:
        """Format alert as HTML email with context."""
        
        color_map = {
            'INFO': '#0066CC',
            'WARNING': '#FF8800',
            'CRITICAL': '#CC0000',
        }
        
        color = color_map.get(severity, '#0066CC')
        
        # Build context section if available
        context_html = ""
        if context:
            context_html = "<div class='context'>"
            if context.get('strategy'):
                context_html += f"<p><strong>Strategy:</strong> {context.get('strategy')}</p>"
            if context.get('current_spot'):
                context_html += f"<p><strong>Current Spot:</strong> {context.get('current_spot'):.2f}</p>"
            if context.get('entry_spot'):
                context_html += f"<p><strong>Entry Spot:</strong> {context.get('entry_spot'):.2f}</p>"
            if context.get('upper_be') or context.get('lower_be'):
                context_html += f"<p><strong>Breakevens:</strong> {context.get('lower_be', 'N/A')} — {context.get('upper_be', 'N/A')}</p>"
            if context.get('net_credit'):
                context_html += f"<p><strong>Net Credit:</strong> Rs {context.get('net_credit'):.0f}</p>"
            context_html += "</div>"
        
        html = f"""
        <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; }}
                    .container {{ max-width: 600px; margin: auto; }}
                    .header {{
                        background-color: {color};
                        color: white;
                        padding: 20px;
                        border-radius: 5px 5px 0 0;
                    }}
                    .content {{
                        background-color: #f9f9f9;
                        padding: 20px;
                        border: 1px solid #ddd;
                    }}
                    .context {{
                        background-color: #fff;
                        border: 1px solid #eee;
                        border-left: 4px solid {color};
                        padding: 15px;
                        margin: 15px 0;
                        border-radius: 3px;
                    }}
                    .context p {{
                        margin: 8px 0;
                        font-size: 14px;
                    }}
                    .alert-type {{
                        font-weight: bold;
                        font-size: 18px;
                    }}
                    .message {{
                        font-size: 16px;
                        line-height: 1.6;
                        margin: 15px 0;
                    }}
                    .timestamp {{
                        font-size: 12px;
                        color: #666;
                        margin-top: 20px;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <div class="alert-type">[{severity}] {alert_type}</div>
                    </div>
                    <div class="content">
                        <div class="message">{message}</div>
                        {context_html}
                        <p style="font-style: italic; color: #666;">
                            ⚡ Options Manager Alert
                        </p>
                        <div class="timestamp">
                            {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}
                        </div>
                    </div>
                </div>
            </body>
        </html>
        """
        
        return html


# ═══════════════════════════════════════════════════════════════════
#  SMS ALERT HANDLER (via Twilio)
# ═══════════════════════════════════════════════════════════════════

class SMSAlertHandler:
    """Sends alert SMS via Twilio. Critical/warning only."""
    
    def __init__(self, twilio_config: Dict):
        """
        Initialize SMS handler.
        
        Args:
            twilio_config: {
                'account_sid': 'your_sid',
                'auth_token': 'your_token',
                'from_number': '+1234567890',  # Twilio number
                'to_numbers': ['+9191XXXXXXXX'],  # Recipient(s)
            }
        """
        if not HAS_TWILIO:
            print("⚠️  Twilio not installed. Install with: pip install twilio")
            self.client = None
            return
        
        self.config = twilio_config
        self.from_number = twilio_config.get('from_number')
        self.to_numbers = twilio_config.get('to_numbers', [])
        
        try:
            self.client = Client(
                twilio_config.get('account_sid'),
                twilio_config.get('auth_token')
            )
        except Exception as e:
            print(f"❌ Twilio init failed: {e}")
            self.client = None
        
        self.rate_limit = {}
    
    def send_alert(self, alert_type: str, message: str,
                   severity: str = 'INFO', context: Optional[Dict] = None) -> bool:
        """
        Send alert SMS.
        
        Args:
            alert_type: 'BREACH', 'PROFIT_TARGET', 'STOP_LOSS', etc.
            message: Alert message (keep short for SMS)
            severity: 'INFO', 'WARNING', 'CRITICAL'
            context: Optional position context
        """
        if not self.client:
            return False
        
        # Rate limiting: Don't spam (15 min cooldown)
        if self._is_rate_limited(alert_type, min_interval_sec=900):
            return False
        
        try:
            # Format SMS (160 char limit) with context
            strategy_tag = ""
            if context and context.get('strategy'):
                strategy_tag = f" [{context['strategy'][:10]}]"
            
            sms_body = f"[{severity}] {alert_type}{strategy_tag}: {message[:80]}"
            
            for to_number in self.to_numbers:
                message_obj = self.client.messages.create(
                    body=sms_body,
                    from_=self.from_number,
                    to=to_number
                )
                
                print(f"💬 SMS sent to {to_number}: {sms_body[:50]}...")
            
            self._record_alert(alert_type)
            return True
            
        except Exception as e:
            print(f"❌ SMS send failed: {e}")
            return False
    
    def _is_rate_limited(self, alert_type: str,
                        min_interval_sec: int = 900) -> bool:
        """Check if alert should be rate limited."""
        now = time.time()
        last_alert = self.rate_limit.get(alert_type, 0)
        return (now - last_alert) < min_interval_sec
    
    def _record_alert(self, alert_type: str) -> None:
        """Record alert timestamp for rate limiting."""
        self.rate_limit[alert_type] = time.time()


# ═══════════════════════════════════════════════════════════════════
#  ALERT MANAGER — Unified Alert Dispatcher (v2)
# ═══════════════════════════════════════════════════════════════════

class AlertManager:
    """
    Manages all alert channels (email, SMS).
    Context-aware for options_manager.py position state.
    """
    
    def __init__(self, email_config: Optional[Dict] = None,
                 sms_config: Optional[Dict] = None):
        """
        Initialize alert manager.
        
        Args:
            email_config: Email configuration dict
            sms_config: SMS (Twilio) configuration dict
        """
        self.email_handler = None
        self.sms_handler = None
        
        if email_config:
            try:
                self.email_handler = EmailAlertHandler(email_config)
                print("✅ Email alerts enabled")
            except Exception as e:
                print(f"⚠️  Email alerts disabled: {e}")
        
        if sms_config:
            try:
                self.sms_handler = SMSAlertHandler(sms_config)
                print("✅ SMS alerts enabled")
            except Exception as e:
                print(f"⚠️  SMS alerts disabled: {e}")
        
        self.alert_history = []
    
    def send_breach_alert(self, side: str, distance: float, strike: float,
                         severity: str = 'WARNING',
                         context: Optional[Dict] = None) -> bool:
        """
        Send breach alert with position context.
        
        Args:
            side: 'upper' or 'lower'
            distance: Points away from breakeven
            strike: Strike price
            severity: Alert severity
            context: From options_manager state
        """
        subject = f"🚨 BREACH ALERT — {side.upper()}"
        message = f"Price approaching {side} breakeven. Strike {strike}, {distance:.0f}pts away."
        
        return self.send_alert('BREACH', subject, message, severity, context)
    
    def send_profit_target_alert(self, strategy: str, profit_pct: float,
                                context: Optional[Dict] = None) -> bool:
        """Send profit target reached alert."""
        subject = f"💰 Profit Target — {strategy}"
        message = f"Position at {profit_pct:.1f}% profit. Consider taking profits."
        
        if not context:
            context = {'strategy': strategy}
        
        return self.send_alert('PROFIT_TARGET', subject, message, 'INFO', context)
    
    def send_stop_loss_alert(self, strategy: str, loss_amount: float,
                            context: Optional[Dict] = None) -> bool:
        """Send stop loss hit alert."""
        subject = f"🛑 STOP LOSS — {strategy}"
        message = f"Hard stop loss triggered. Loss: Rs {loss_amount:.0f}. CLOSE POSITION IMMEDIATELY."
        
        if not context:
            context = {'strategy': strategy}
        
        return self.send_alert('STOP_LOSS', subject, message, 'CRITICAL', context)
    
    def send_adjustment_alert(self, adjustment_type: str, details: str,
                             context: Optional[Dict] = None) -> bool:
        """Send adjustment action alert."""
        subject = f"⚙️  Adjustment Alert — {adjustment_type}"
        message = f"Action: {details}"
        
        return self.send_alert('ADJUSTMENT', subject, message, 'INFO', context)
    
    def send_position_opened_alert(self, strategy: str, 
                                  context: Optional[Dict] = None) -> bool:
        """Send alert when position is opened."""
        subject = f"✅ Position Opened — {strategy}"
        message = f"New {strategy} position opened. Monitor for breakevens and adjustments."
        
        if not context:
            context = {'strategy': strategy}
        
        return self.send_alert('POSITION_OPENED', subject, message, 'INFO', context)
    
    def send_position_closed_alert(self, strategy: str, pnl: float,
                                  context: Optional[Dict] = None) -> bool:
        """Send alert when position is closed."""
        status = "✅ PROFIT" if pnl > 0 else "❌ LOSS"
        subject = f"{status} — {strategy}"
        message = f"Position closed. P&L: Rs {pnl:,.0f}"
        
        if not context:
            context = {'strategy': strategy}
        
        severity = 'INFO' if pnl > 0 else 'WARNING'
        return self.send_alert('POSITION_CLOSED', subject, message, severity, context)
    
    def send_alert(self, alert_type: str, subject: str, message: str,
                   severity: str = 'INFO',
                   context: Optional[Dict] = None) -> bool:
        """
        Send alert via all configured channels.
        
        Args:
            alert_type: Alert category
            subject: Alert subject/title
            message: Alert message body
            severity: 'INFO', 'WARNING', 'CRITICAL'
            context: Position/strategy context from options_manager
        """
        success = False
        
        # Send via email
        if self.email_handler:
            success |= self.email_handler.send_alert(
                alert_type, subject, message, severity, context
            )
        
        # Send via SMS (only critical/warning)
        if self.sms_handler and severity in ['WARNING', 'CRITICAL']:
            success |= self.sms_handler.send_alert(
                alert_type, message, severity, context
            )
        
        # Log alert
        self.alert_history.append({
            'type': alert_type,
            'subject': subject,
            'severity': severity,
            'timestamp': datetime.now().isoformat(),
            'context': context,
        })
        
        return success
    
    def get_alert_summary(self) -> Dict:
        """Get summary of recent alerts."""
        return {
            'total_alerts': len(self.alert_history),
            'recent_alerts': self.alert_history[-10:],  # Last 10
            'alert_types': list(set(a['type'] for a in self.alert_history)),
        }


# ═══════════════════════════════════════════════════════════════════
#  USAGE EXAMPLE
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # Example configuration
    email_config = {
        'sender_email': 'your_email@gmail.com',
        'sender_password': 'your_app_password',  # Gmail app password
        'smtp_server': 'smtp.gmail.com',
        'smtp_port': 587,
        'recipient_emails': ['alert@example.com', 'another@example.com'],
    }
    
    sms_config = {
        'account_sid': 'your_twilio_sid',
        'auth_token': 'your_twilio_token',
        'from_number': '+1234567890',
        'to_numbers': ['+919191XXXXXX'],
    }
    
    # Initialize alert manager
    alerts = AlertManager(email_config=email_config, sms_config=sms_config)
    
    # Example context from options_manager state
    sample_context = {
        'strategy': 'Iron Fly',
        'current_spot': 24500,
        'entry_spot': 24450,
        'upper_be': 24600,
        'lower_be': 24350,
        'net_credit': 250,
    }
    
    # Send different alerts
    alerts.send_breach_alert('upper', 50, 24500, severity='WARNING', context=sample_context)
    
    alerts.send_profit_target_alert('Iron Fly', 50.0, context=sample_context)
    
    alerts.send_stop_loss_alert('Iron Fly', 5000, context=sample_context)
    
    alerts.send_position_opened_alert('Iron Fly', context=sample_context)
    
    # Get alert summary
    print(f"\n{alerts.get_alert_summary()}")
