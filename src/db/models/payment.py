from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional, Dict, Any
from datetime import datetime


class PaymentRequest(Document):
    logistic_partner_id: PydanticObjectId  # ref to User._id
    pump_id: Optional[PydanticObjectId] = None  # ref to Pump._id (optional for vouchers)
    amount: float
    payment_type: str                      # wallet_topup, outstanding_settlement, advance_payment
    screenshot_url: Optional[str] = None
    vehicle_id: Optional[PydanticObjectId] = None  # ref to Vehicle._id
    status: str = "pending"               # pending, contract_generated, logistic_signed, pump_signed, approved, rejected
    transaction_reference: Optional[str] = None
    remarks: Optional[str] = None
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[PydanticObjectId] = None  # ref to User._id

    # Logistic Form Data (JSON)
    logistic_form_data: Optional[Dict[str, Any]] = None  # dict for easy access

    # Contract
    contract_terms: Optional[Dict[str, Any]] = None  # dict for easy access
    contract_generated_at: Optional[datetime] = None

    # Logistic Partner Sign
    logistic_signed: bool = False
    logistic_signed_at: Optional[datetime] = None
    logistic_otp_hash: Optional[str] = None
    logistic_sign_ip: Optional[str] = None

    # Pump Owner Sign
    pump_owner_signed: bool = False
    pump_owner_signed_at: Optional[datetime] = None
    pump_owner_otp_hash: Optional[str] = None
    pump_owner_sign_ip: Optional[str] = None

    class Settings:
        name = "payment_requests"
        indexes = [
            [("pump_id", 1)],
            [("logistic_partner_id", 1)],
        ]