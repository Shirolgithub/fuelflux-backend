from beanie import Document, PydanticObjectId, Indexed
from pydantic import Field
from typing import Optional, Annotated
from datetime import datetime


class Pump(Document):
    name: str
    org_name: Optional[str] = None
    logo_url: Optional[str] = None
    address: str
    owner_id: PydanticObjectId          # ref to User._id
    contact_number: Optional[str] = None
    opening_time: Optional[str] = None
    closing_time: Optional[str] = None
    status: str = "pending"
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    gst: Optional[str] = None
    license: Optional[str] = None
    fuel_types: Optional[str] = None
    tanks_count: int = 0
    nozzles_count: int = 0
    daily_capacity: int = 0
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "pumps"
        indexes = [
            [("owner_id", 1)],
            [("status", 1)],
        ]

    class Config:
        populate_by_name = True