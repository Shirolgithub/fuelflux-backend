# # src/services/sms_service.py
# # Twilio SMS helper — used for Admin 2FA OTP delivery

# import os
# import structlog

# log = structlog.get_logger()


# def send_sms_otp(phone_number: str, otp_code: str) -> bool:
#     """
#     Sends OTP via Twilio SMS. Returns True if sent successfully,
#     False on failure (caller should fall back to console/dev mode).

#     phone_number must include country code, e.g. "+919876543210"
#     """
#     try:
#         from twilio.rest import Client

#         account_sid = os.getenv("TWILIO_ACCOUNT_SID")
#         auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
#         from_number = os.getenv("TWILIO_PHONE_NUMBER")

#         if not all([account_sid, auth_token, from_number]):
#             log.warning("Twilio credentials missing in .env — falling back to console OTP.")
#             return False

#         client = Client(account_sid, auth_token)

#         message = client.messages.create(
#             body=f"Your FuelFlux Admin verification code is: {otp_code}. Valid for 10 minutes. Do not share this code.",
#             from_=from_number,
#             to=phone_number,
#         )

#         log.info("Twilio SMS sent", sid=message.sid, to=phone_number)
#         return True

#     except ImportError:
#         log.error("Twilio package not installed. Run: pip install twilio")
#         return False
#     except Exception as e:
#         log.error("Twilio SMS failed", error=str(e), phone=phone_number)
#         return False


# src/services/sms_service.py
# Twilio WhatsApp helper — used for Admin 2FA OTP delivery
# (No DLT required — WhatsApp Sandbox works instantly for dev/testing)

import os
import structlog

log = structlog.get_logger()


def send_sms_otp(phone_number: str, otp_code: str) -> bool:
    """
    Sends OTP via Twilio WhatsApp. Returns True if sent successfully,
    False on failure — OTP is then printed to terminal for dev use.

    phone_number must include country code, e.g. "+919523698906"
    """
    try:
        from twilio.rest import Client

        account_sid   = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token    = os.getenv("TWILIO_AUTH_TOKEN")
        whatsapp_from = os.getenv("TWILIO_WHATSAPP_NUMBER", "+14155238886")  # Sandbox default

        if not all([account_sid, auth_token]):
            _print_otp_to_terminal(phone_number, otp_code, reason="Twilio credentials missing in .env")
            return False

        client = Client(account_sid, auth_token)

        message = client.messages.create(
            body=f"Your FuelFlux verification code is: *{otp_code}*. Valid for 10 minutes. Do not share this code.",
            from_=f"whatsapp:{whatsapp_from}",
            to=f"whatsapp:{phone_number}",
        )

        log.info("Twilio WhatsApp OTP sent", sid=message.sid, to=phone_number)
        return True

    except ImportError:
        _print_otp_to_terminal(phone_number, otp_code, reason="Twilio package not installed")
        return False
    except Exception as e:
        _print_otp_to_terminal(phone_number, otp_code, reason=str(e))
        return False


def _print_otp_to_terminal(phone_number: str, otp_code: str, reason: str = "") -> None:
    """Print OTP clearly to terminal when WhatsApp delivery fails (dev/debug mode)."""
    border = "=" * 50
    print(f"\n{border}")
    print(f"  🔐  OTP (WhatsApp failed — showing in terminal)")
    print(f"  📱  Phone  : {phone_number}")
    print(f"  🔑  OTP    : {otp_code}")
    print(f"  ⏱️  Expires: 10 minutes")
    if reason:
        print(f"  ⚠️  Reason : {reason}")
    print(f"{border}\n")
    log.warning("OTP printed to terminal (WhatsApp not delivered)", phone=phone_number, reason=reason)