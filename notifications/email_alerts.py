"""
Email alert notifications for trade executions.
Uses Gmail SMTP with an App Password (not your regular Gmail password).
"""

import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import structlog

log = structlog.get_logger(__name__)

GMAIL_SENDER = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "dineshsaravanan1@gmail.com")


def send_trade_alert(
    symbol: str,
    side: str,
    qty: int,
    order_type: str,
    limit_price: float | None,
    status: str,
    order_id: str,
    rationale: str,
) -> None:
    """Send an email alert when a trade order is placed."""
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        log.warning("email_alert_skipped", reason="GMAIL_SENDER or GMAIL_APP_PASSWORD not set")
        return

    side_upper = side.upper().replace("ORDERSIDE.", "").replace("BUY", "BUY").replace("SELL", "SELL")
    emoji = "🟢" if "BUY" in side_upper else "🔴"
    price_str = f"${limit_price:.2f}" if limit_price else "Market Price"

    subject = f"{emoji} Wall Street Bro — {side_upper} {qty}x {symbol} @ {price_str}"

    body = f"""
<html>
<body style="font-family: Arial, sans-serif; background: #0f0f0f; color: #e0e0e0; padding: 20px;">
  <div style="max-width: 500px; margin: 0 auto; background: #1a1a1a; border-radius: 8px; padding: 24px; border: 1px solid #333;">
    <h2 style="color: {'#00e676' if 'BUY' in side_upper else '#ff1744'}; margin-top: 0;">
      {emoji} Trade Executed — {symbol}
    </h2>
    <table style="width: 100%; border-collapse: collapse;">
      <tr><td style="padding: 8px 0; color: #888;">Symbol</td><td style="padding: 8px 0; font-weight: bold;">{symbol}</td></tr>
      <tr><td style="padding: 8px 0; color: #888;">Side</td><td style="padding: 8px 0; font-weight: bold; color: {'#00e676' if 'BUY' in side_upper else '#ff1744'};">{side_upper}</td></tr>
      <tr><td style="padding: 8px 0; color: #888;">Quantity</td><td style="padding: 8px 0;">{qty} shares</td></tr>
      <tr><td style="padding: 8px 0; color: #888;">Order Type</td><td style="padding: 8px 0;">{order_type.upper()}</td></tr>
      <tr><td style="padding: 8px 0; color: #888;">Price</td><td style="padding: 8px 0;">{price_str}</td></tr>
      <tr><td style="padding: 8px 0; color: #888;">Status</td><td style="padding: 8px 0;">{status.upper()}</td></tr>
      <tr><td style="padding: 8px 0; color: #888;">Order ID</td><td style="padding: 8px 0; font-size: 12px; color: #666;">{order_id}</td></tr>
    </table>
    <hr style="border-color: #333; margin: 16px 0;">
    <p style="color: #888; font-size: 13px; margin: 0;"><strong style="color: #aaa;">Rationale:</strong><br>{rationale}</p>
    <hr style="border-color: #333; margin: 16px 0;">
    <p style="color: #555; font-size: 11px; margin: 0;">Wall Street Bro — Autonomous Trading System (Paper Account)</p>
  </div>
</body>
</html>
"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = GMAIL_SENDER
        msg["To"] = ALERT_EMAIL
        msg.attach(MIMEText(body, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, ALERT_EMAIL, msg.as_string())

        log.info("trade_alert_sent", symbol=symbol, side=side_upper, to=ALERT_EMAIL)
    except Exception as exc:
        log.error("trade_alert_failed", error=str(exc))
