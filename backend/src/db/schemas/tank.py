from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class TankBase(BaseModel):
    tank_number: str
    capacity_liters: float
    fuel_type: str = "Diesel"

class TankCreate(TankBase):
    pump_id: str

class TankResponse(TankBase):
    id: str
    pump_id: str
    current_level_liters: float
    temperature: Optional[float] = None
    last_updated: datetime

    class Config:
        from_attributes = True