from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime

class PaymentRequestCreate(BaseModel):
    pump_id: Optional[str] = None
    amount: float
    payment_type: str = "wallet_topup"
    transaction_reference: Optional[str] = None
    remarks: Optional[str] = None
    screenshot_url: Optional[str] = None

class PaymentRequestResponse(BaseModel):
    id: Optional[Any] = None
    logistic_partner_id: Optional[Any] = None
    pump_id: Optional[Any] = None          # Optional — wallet_topup has no pump
    amount: float
    payment_type: Optional[str] = None
    transaction_reference: Optional[str] = None
    status: str
    remarks: Optional[str] = None
    screenshot_url: Optional[str] = None
    requested_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None

    model_config = {"from_attributes": True, "arbitrary_types_allowed": True}