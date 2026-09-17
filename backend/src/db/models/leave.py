from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime, date


class LeaveRequest(Document):
    """Employee leave application."""
    attendant_id: PydanticObjectId      # ref → Attendant._id
    pump_id: PydanticObjectId           # ref → Pump._id (isolation)
    leave_type: str = "Casual"          # Casual | Sick | Earned | Emergency
    from_date: date
    to_date: date
    reason: str
    status: str = "pending"             # pending | approved | rejected
    applied_on: datetime = Field(default_factory=datetime.utcnow)
    reviewed_by: Optional[str] = None   # pump owner email
    reviewed_on: Optional[datetime] = None
    reviewed_note: Optional[str] = None # reason for rejection etc.

    class Settings:
        name = "leave_requests"
        indexes = [
            [("attendant_id", 1)],
            [("pump_id", 1), ("status", 1)],
        ]