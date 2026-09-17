from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class TransactionBase(BaseModel):
    pump_id: int
    attendant_id: Optional[int] = None
    nozzle_id: Optional[int] = None
    vehicle_plate: Optional[str] = None
    volume: float
    amount: float
    event_type: str = "nozzle_sale"

class TransactionCreate(TransactionBase):
    pass

class TransactionResponse(TransactionBase):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True