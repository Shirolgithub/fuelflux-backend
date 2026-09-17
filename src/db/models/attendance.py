from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime, date


class AttendanceRecord(Document):
    """
    One record per employee per day.
    pump_id stored for fast isolation queries.
    """
    attendant_id: PydanticObjectId      # ref → Attendant._id
    pump_id: PydanticObjectId           # ref → Pump._id (isolation)
    date: date                          # 2025-06-01
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None
    working_hours: Optional[float] = None
    status: str = "Present"             # Present | Late | Absent | Leave | Holiday
    note: Optional[str] = None          # pump owner add kar sakta hai
    is_manual: bool = False             # True = pump owner ne manually mark kiya
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "attendance_records"
        indexes = [
            [("attendant_id", 1), ("date", 1)],   # unique per employee per day
            [("pump_id", 1), ("date", 1)],         # pump owner view
        ]