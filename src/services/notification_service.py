"""
src/services/notification_service.py
Centralized notification service for email and WhatsApp notifications.
Used by invoice_scheduler.py for compliance reminders and billing alerts.
"""

import os
import smtplib
import structlog
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

log = structlog.get_logger()


def send_email_notification(
    to_email: str,
    subject: str,
    body: str,
    cc_email: Optional[str] = None,
) -> bool:
    """
    Send an email notification via SMTP.
    Falls back to console logging if SMTP credentials are missing.

    Required .env variables:
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_EMAIL
    """
    try:
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER")
        smtp_password = os.getenv("SMTP_PASSWORD")
        from_email = os.getenv("SMTP_FROM_EMAIL", smtp_user)

        if not all([smtp_host, smtp_user, smtp_password]):
            # Dev mode: just log it
            log.warning(
                "SMTP credentials missing — email not sent (dev fallback)",
                to=to_email,
                subject=subject,
            )
            log.info("[DEV EMAIL]", to=to_email, subject=subject, body=body[:200])
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email
        if cc_email:
            msg["Cc"] = cc_email

        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            recipients = [to_email]
            if cc_email:
                recipients.append(cc_email)
            server.sendmail(from_email, recipients, msg.as_string())

        log.info("Email sent successfully", to=to_email, subject=subject)
        return True

    except Exception as e:
        log.error("Email notification failed", error=str(e), to=to_email)
        return False


def send_whatsapp_notification(phone: str, message: str) -> bool:
    """
    Send a WhatsApp notification via Twilio.
    Falls back to console logging if Twilio credentials are missing.

    phone must include country code, e.g. "+919876543210"

    Required .env variables:
        TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER
    """
    try:
        from twilio.rest import Client

        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        whatsapp_from = os.getenv("TWILIO_WHATSAPP_NUMBER", "+14155238886")  # Twilio Sandbox default

        if not all([account_sid, auth_token]):
            log.warning(
                "Twilio credentials missing — WhatsApp not sent (dev fallback)",
                phone=phone,
            )
            log.info("[DEV WHATSAPP]", to=phone, message=message[:200])
            return False

        client = Client(account_sid, auth_token)
        client.messages.create(
            body=message,
            from_=f"whatsapp:{whatsapp_from}",
            to=f"whatsapp:{phone}",
        )

        log.info("WhatsApp notification sent", to=phone)
        return True

    except ImportError:
        log.warning("Twilio not installed — WhatsApp notification skipped", phone=phone)
        return False
    except Exception as e:
        log.error("WhatsApp notification failed", error=str(e), phone=phone)
        return False
