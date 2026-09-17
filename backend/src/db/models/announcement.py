from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime


class Announcement(Document):
    """Pump owner posts announcements for their employees."""
    pump_id: PydanticObjectId           # ref → Pump._id (isolation)
    title: str
    content: str
    announcement_type: str = "General"  # General | Urgent | Safety
    is_active: bool = True
    created_by: str                     # pump owner email
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None

    class Settings:
        name = "announcements"
        indexes = [
            [("pump_id", 1), ("is_active", 1)],
            [("pump_id", 1), ("created_at", -1)],
        ]