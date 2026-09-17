from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime


class Tank(Document):
    pump_id: PydanticObjectId           # ref to Pump._id
    tank_number: str                    # Tank 1, Tank 2 etc.
    capacity_liters: float
    current_level_liters: float = 0.0
    fuel_type: str = "Diesel"           # Petrol / Diesel
    temperature: Optional[float] = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "tanks"
        indexes = [
            [("pump_id", 1)],
        ]