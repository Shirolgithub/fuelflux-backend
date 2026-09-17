from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class ReconciliationBase(BaseModel):
    pump_id: int
    shift_date: str
    expected_amount: float
    actual_amount: float
    expected_volume: float
    actual_volume: float
    mismatch_amount: float = 0.0
    mismatch_volume: float = 0.0
    status: str = "pending"   # pending, matched, mismatch, manual

class ReconciliationResponse(ReconciliationBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True