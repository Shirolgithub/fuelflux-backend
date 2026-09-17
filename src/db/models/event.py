from beanie import Document, PydanticObjectId, Indexed
from pydantic import Field
from typing import Any, Dict, Annotated
from datetime import datetime


class Event(Document):
    station_id: PydanticObjectId        # ref to Pump._id
    event_type: str                     # attendance_active, nozzle_start, anpr_detected, tank_update etc.
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    payload: Dict[str, Any] = Field(default_factory=dict)  # All event data
    processed: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "events"
        indexes = [
            [("station_id", 1), ("timestamp", -1)],
            [("event_type", 1)],
        ]