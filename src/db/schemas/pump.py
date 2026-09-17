from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class PumpBase(BaseModel):
    name: str
    address: str
    contact_number: Optional[str] = None
    opening_time: Optional[str] = None
    closing_time: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    gst: Optional[str] = None
    license: Optional[str] = None
    fuel_types: Optional[str] = None
    tanks_count: Optional[int] = 0
    nozzles_count: Optional[int] = 0
    daily_capacity: Optional[int] = 0
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class PumpCreate(PumpBase):
    pass

class PumpResponse(PumpBase):
    id: str
    owner_id: str
    status: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True