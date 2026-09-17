# src/db/models/credit_request.py

from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional, List
from datetime import datetime


class CreditRequest(Document):
    logistic_partner_id: PydanticObjectId  # ref to User._id
    vehicle_ids: List[PydanticObjectId] = Field(default_factory=list)  # ref to Vehicle._id list
    vehicle_plates: List[str] = Field(default_factory=list)
    pump_id: PydanticObjectId              # ref to Pump._id
    requested_limit: float
    approved_limit: Optional[float] = None

    # Status flow:
    # pending → approved → deposit_pending → deposit_confirmed
    # → contract_generated → logistic_signed → pump_signed → active
    # OR: rejected
    status: str = "pending"

    remarks: Optional[str] = None
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[PydanticObjectId] = None   # ref to User._id

    # Deposit
    deposit_amount: Optional[float] = None
    deposit_proof_url: Optional[str] = None
    deposit_confirmed: bool = False
    deposit_confirmed_at: Optional[datetime] = None
    deposit_confirmed_by: Optional[PydanticObjectId] = None  # ref to User._id

    # Contract
    contract_terms: Optional[str] = None             # JSON string
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    proof_doc_type: Optional[str] = None
    logistic_contract_data: Optional[str] = None

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

    # Activation
    activated_at: Optional[datetime] = None
    credit_limit: Optional[float] = None              # final active limit

    class Settings:
        name = "credit_requests"
        indexes = [
            [("pump_id", 1)],
            [("logistic_partner_id", 1)],
        ]