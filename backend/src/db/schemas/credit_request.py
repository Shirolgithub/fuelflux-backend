from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class CreditRequestBase(BaseModel):
    vehicle_ids: List[str]
    requested_limit: float
    remarks: Optional[str] = None

class CreditRequestCreate(CreditRequestBase):
    pump_id: str

class CreditRequestResponse(CreditRequestBase):
    id: str
    logistic_partner_id: str
    pump_id: str
    status: str
    requested_at: datetime
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    vehicle_plates: Optional[List[str]] = None

    class Config:
        from_attributes = True