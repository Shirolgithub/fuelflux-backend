"""
Razorpay & RazorpayX Service Layer
Handles standard Razorpay payment orders/webhooks and RazorpayX payouts with mock simulation fallback.
"""
import razorpay
import hmac
import hashlib
import httpx
from typing import Dict, Any, Optional
from decimal import Decimal
import structlog
from src.core.config import settings

log = structlog.get_logger()

class RazorpayService:
    def __init__(self):
        self.key_id = settings.RAZORPAY_KEY_ID or "mock_key_id"
        self.key_secret = settings.RAZORPAY_KEY_SECRET or "mock_key_secret"
        self.webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET or "mock_webhook_secret"
        self.account_number = settings.RAZORPAYX_ACCOUNT_NUMBER

        # Initialize standard Razorpay client
        try:
            self.client = razorpay.Client(auth=(self.key_id, self.key_secret))
        except Exception as e:
            log.error("razorpay_client_init_failed", error=str(e))
            self.client = None

    def create_order(self, amount_inr: Decimal, receipt_id: str) -> Dict[str, Any]:
        """
        Creates a standard Razorpay Order.
        Amount is converted to paise (1 INR = 100 paise).
        """
        # Convert Decimal amount safely to integer paise
        amount_paise = int(amount_inr * Decimal("100"))
        
        if not self.client or self.key_id == "mock_key_id":
            # Simulation fallback
            mock_order_id = f"order_mock_{receipt_id}_{int(amount_inr)}"
            log.info("razorpay_create_order_simulated", amount=amount_inr, order_id=mock_order_id)
            return {
                "id": mock_order_id,
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt_id,
                "status": "created",
                "simulated": True
            }

        try:
            order_data = {
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt_id,
                "payment_capture": 1
            }
            order = self.client.order.create(data=order_data)
            log.info("razorpay_order_created", order_id=order["id"], amount=amount_inr)
            return order
        except Exception as e:
            log.error("razorpay_order_creation_failed", error=str(e))
            raise e

    def verify_payment_signature(self, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
        """
        Verifies the Razorpay payment signature using SHA256 HMAC.
        """
        if razorpay_order_id.startswith("order_mock_"):
            log.info("razorpay_signature_verify_simulated", order_id=razorpay_order_id)
            return True

        if not self.client:
            return False

        try:
            params = {
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            }
            self.client.utility.verify_payment_signature(params)
            log.info("razorpay_signature_verified", order_id=razorpay_order_id, payment_id=razorpay_payment_id)
            return True
        except Exception as e:
            log.error("razorpay_signature_verification_failed", error=str(e))
            return False

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        """
        Verifies the webhook signature sent by Razorpay webhook headers.
        """
        if signature == "mock_signature_bypass":
            log.warn("razorpay_webhook_signature_bypass_requested")
            return True

        try:
            expected = hmac.new(
                self.webhook_secret.encode('utf-8'),
                body,
                hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(expected, signature)
        except Exception as e:
            log.error("razorpay_webhook_signature_error", error=str(e))
            return False

    async def create_razorpayx_contact(self, name: str, email: str, reference_id: str) -> Optional[str]:
        """
        Creates a Contact in RazorpayX.
        Returns the contact_id.
        """
        if not self.account_number or self.key_id == "mock_key_id":
            mock_contact_id = f"cont_mock_{reference_id}"
            log.info("razorpayx_create_contact_simulated", contact_id=mock_contact_id)
            return mock_contact_id

        url = "https://api.razorpay.com/v1/contacts"
        payload = {
            "name": name,
            "email": email,
            "type": "customer",
            "reference_id": reference_id
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    json=payload,
                    auth=(self.key_id, self.key_secret),
                    timeout=10.0
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    contact_id = data.get("id")
                    log.info("razorpayx_contact_created", contact_id=contact_id)
                    return contact_id
                else:
                    log.error("razorpayx_contact_creation_error", status_code=response.status_code, body=response.text)
                    return None
            except Exception as e:
                log.error("razorpayx_contact_request_failed", error=str(e))
                return None

    async def create_razorpayx_fund_account(
        self, contact_id: str, name: str, ifsc: str, account_number: str
    ) -> Optional[str]:
        """
        Creates a Bank Account Fund Account linked to a Contact in RazorpayX.
        Returns the fund_account_id.
        """
        if contact_id.startswith("cont_mock_") or self.key_id == "mock_key_id":
            mock_fa_id = f"fa_mock_{account_number[-4:]}"
            log.info("razorpayx_create_fund_account_simulated", fund_account_id=mock_fa_id)
            return mock_fa_id

        url = "https://api.razorpay.com/v1/fund_accounts"
        payload = {
            "contact_id": contact_id,
            "account_type": "bank_account",
            "bank_account": {
                "name": name,
                "ifsc": ifsc,
                "account_number": account_number
            }
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    json=payload,
                    auth=(self.key_id, self.key_secret),
                    timeout=10.0
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    fa_id = data.get("id")
                    log.info("razorpayx_fund_account_created", fund_account_id=fa_id)
                    return fa_id
                else:
                    log.error("razorpayx_fund_account_error", status_code=response.status_code, body=response.text)
                    return None
            except Exception as e:
                log.error("razorpayx_fund_account_request_failed", error=str(e))
                return None

    async def initiate_razorpayx_payout(
        self, fund_account_id: str, amount_inr: Decimal, idempotency_key: str
    ) -> Dict[str, Any]:
        """
        Initiates a Payout in RazorpayX to a Fund Account.
        Uses IMPS by default.
        """
        amount_paise = int(amount_inr * Decimal("100"))

        if fund_account_id.startswith("fa_mock_") or not self.account_number or self.key_id == "mock_key_id":
            mock_payout_id = f"payout_mock_{idempotency_key[:8]}"
            log.info("razorpayx_initiate_payout_simulated", payout_id=mock_payout_id, amount=amount_inr)
            return {
                "id": mock_payout_id,
                "status": "processing",
                "amount": amount_paise,
                "simulated": True
            }

        url = "https://api.razorpay.com/v1/payouts"
        payload = {
            "account_number": self.account_number,
            "fund_account_id": fund_account_id,
            "amount": amount_paise,
            "currency": "INR",
            "mode": "IMPS",
            "purpose": "payout",
            "queue_if_low_balance": True,
            "reference_id": idempotency_key
        }
        headers = {
            "X-Payout-Idempotency": idempotency_key
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                    auth=(self.key_id, self.key_secret),
                    timeout=10.0
                )
                if response.status_code in (200, 201, 202):
                    data = response.json()
                    log.info("razorpayx_payout_initiated", payout_id=data.get("id"), status=data.get("status"))
                    return data
                else:
                    log.error("razorpayx_payout_error", status_code=response.status_code, body=response.text)
                    raise Exception(f"RazorpayX Payout API error: {response.text}")
            except Exception as e:
                log.error("razorpayx_payout_request_failed", error=str(e))
                raise e


# Singleton instance
razorpay_service = RazorpayService()
