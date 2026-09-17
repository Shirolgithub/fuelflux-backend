from pydantic import BaseModel
from datetime import datetime
from typing import Any, Dict

class EventCreate(BaseModel):
    station_id: str
    event_type: str
    payload: Dict[str, Any]

class EventResponse(BaseModel):
    id: str
    station_id: str
    event_type: str
    timestamp: datetime
    processed: bool

    class Config:
        from_attributes = True