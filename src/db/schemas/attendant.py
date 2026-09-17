from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class AttendantCreate(BaseModel):
    """
    Pump owner uses this to create an employee.
    employee_id: pump owner assigns manually (e.g. "FF-001")
    password: initial password — employee should change after first login
    """
    name: str
    phone: str
    email: Optional[str] = None
    employee_id: str                        # pump owner assigns this
    password: str                           # plaintext — hashed in route
    designation: str = "Attendant"          # Attendant | Cashier | Manager | Supervisor
    shift: Optional[str] = None             # Morning | Evening | Night
    address: Optional[str] = None
    date_of_joining: Optional[datetime] = None
    date_of_birth: Optional[datetime] = None
    emergency_contact: Optional[str] = None


class AttendantUpdate(BaseModel):
    """Pump owner can update these fields."""
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    designation: Optional[str] = None
    shift: Optional[str] = None
    address: Optional[str] = None
    emergency_contact: Optional[str] = None


class AttendantResetPassword(BaseModel):
    """Pump owner resets employee password."""
    new_password: str


class AttendantResponse(BaseModel):
    id: str
    pump_id: str
    name: str
    phone: str
    email: Optional[str] = None
    employee_id: str
    designation: str
    shift: Optional[str] = None
    role: str
    face_photo_url: Optional[str] = None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True