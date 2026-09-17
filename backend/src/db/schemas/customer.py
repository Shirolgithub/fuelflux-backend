from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class CustomerBase(BaseModel):
    name: str
    phone: Optional[str] = None
    vehicle_plate: str
    vehicle_type: Optional[str] = None
    credit_limit: float = 0.0
    is_fleet: bool = False

class CustomerCreate(CustomerBase):
    pump_id: str

class CustomerResponse(CustomerBase):
    id: str
    pump_id: str
    outstanding_amount: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True