from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime


class Customer(Document):
    name: str
    phone: Optional[str] = None
    vehicle_plate: str
    vehicle_type: Optional[str] = None
    credit_limit: float = 0.0
    outstanding_amount: float = 0.0
    is_fleet: bool = False
    pump_id: PydanticObjectId           # ref to Pump._id
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "customers"
        indexes = [
            [("pump_id", 1)],
            [("vehicle_plate", 1)],
        ]