from beanie import Document, PydanticObjectId, Indexed
from pydantic import Field
from typing import Optional
from datetime import datetime


class Attendant(Document):
    """
    Pump employee/attendant.
    - Belongs to exactly ONE pump (pump_id)
    - Login: employee_id + password (pump owner sets initial password)
    - Data isolation: pump_id ensures employee sees only their pump's data
    """
    pump_id: PydanticObjectId                   # ref → Pump._id
    name: str
    phone: Indexed(str, unique=True)
    email: Optional[str] = None
    employee_id: Indexed(str, unique=True)       # e.g. "FF-001" — pump owner assigns
    designation: str = "Attendant"               # Attendant | Cashier | Manager | Supervisor
    shift: Optional[str] = None                  # Morning | Evening | Night
    role: str = "attendant"

    # ── Auth ──────────────────────────────────────────────────────────────────
    hashed_password: str                         # bcrypt — pump owner sets initial pw

    # ── Profile ───────────────────────────────────────────────────────────────
    face_photo_url: Optional[str] = None
    address: Optional[str] = None
    date_of_joining: Optional[datetime] = None
    date_of_birth: Optional[datetime] = None
    emergency_contact: Optional[str] = None

    # ── Status ────────────────────────────────────────────────────────────────
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "attendants"
        indexes = [
            [("pump_id", 1)],
            [("phone", 1)],
        ]