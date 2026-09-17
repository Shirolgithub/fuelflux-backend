from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime


class Transaction(Document):
    pump_id: PydanticObjectId           # ref to Pump._id
    attendant_id: Optional[PydanticObjectId] = None  # ref to Attendant._id
    nozzle_id: Optional[int] = None
    vehicle_plate: Optional[str] = None
    volume: float                       # Liters
    amount: float                       # Rupees
    event_type: str = "nozzle_sale"
    description: Optional[str] = None  # Expense-specific
    category: Optional[str] = None
    payment_mode: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "transactions"
        indexes = [
            [("pump_id", 1), ("timestamp", -1)],
        ]