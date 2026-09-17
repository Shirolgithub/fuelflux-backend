from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class VehicleBase(BaseModel):
    vehicle_plate: str
    vehicle_type: str
    make_model: Optional[str] = None
    fuel_type: str = "Diesel"
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    credit_limit: float = 50000.0

class VehicleCreate(VehicleBase):
    pass

class VehicleResponse(VehicleBase):
    id: str
    partner_id: str
    outstanding_amount: float
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True