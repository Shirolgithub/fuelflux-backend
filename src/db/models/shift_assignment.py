from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime, date


class ShiftAssignment(Document):
    pump_id: PydanticObjectId
    attendant_id: PydanticObjectId
    date: date
    shift_type: str  # Morning | Evening | Night
    status: str = "scheduled"  # scheduled | completed | absent | covered
    covered_by: Optional[PydanticObjectId] = None  # if someone covered
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "shift_assignments"
        indexes = [
            [("pump_id", 1), ("date", 1)],
            [("attendant_id", 1), ("date", 1)],
        ]
