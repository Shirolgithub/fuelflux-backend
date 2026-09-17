from beanie import Document, PydanticObjectId, Indexed
from pydantic import Field
from typing import Optional, Annotated
from datetime import datetime


class Vehicle(Document):
    partner_id: PydanticObjectId        # ref to User._id (Logistic Partner)
    vehicle_plate: Indexed(str, unique=True)
    vehicle_type: str
    make_model: Optional[str] = None
    fuel_type: str = "Diesel"
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    credit_limit: float = 0.0
    outstanding_amount: float = 0.0
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "vehicles"
        indexes = [
            [("partner_id", 1)],
            [("vehicle_plate", 1)],
        ]