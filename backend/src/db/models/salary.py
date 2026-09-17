from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime


class SalarySlip(Document):
    """Monthly salary slip per employee."""
    attendant_id: PydanticObjectId      # ref → Attendant._id
    pump_id: PydanticObjectId           # ref → Pump._id (isolation)
    month: int                          # 1-12
    year: int
    basic_salary: float
    deductions: float = 0.0             # absent days, advances etc.
    bonuses: float = 0.0
    net_salary: float
    payment_status: str = "pending"     # pending | paid
    paid_on: Optional[datetime] = None
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "salary_slips"
        indexes = [
            [("attendant_id", 1), ("month", 1), ("year", 1)],
            [("pump_id", 1)],
        ]