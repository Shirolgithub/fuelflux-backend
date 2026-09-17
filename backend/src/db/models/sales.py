"""
Sales models: Shift, ShiftPersonnel, ShiftPoint, SaleLog
"""
from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional, List
from datetime import datetime
import enum


class ShiftType(str, enum.Enum):
    morning = "morning"
    evening = "evening"
    night = "night"
    custom = "custom"


class ShiftStatus(str, enum.Enum):
    active = "active"
    closed = "closed"


class PaymentMode(str, enum.Enum):
    cash = "cash"
    credit = "credit"
    pos = "pos"
    upi = "upi"


class SaleType(str, enum.Enum):
    single = "single"
    batch = "batch"


# ─────────────────────────────────────────────
# Shift — one per station per session
# ─────────────────────────────────────────────
class Shift(Document):
    pump_id: PydanticObjectId           # ref to Pump._id
    shift_type: ShiftType
    status: ShiftStatus = ShiftStatus.active
    start_time: datetime
    end_time: Optional[datetime] = None
    created_by: PydanticObjectId        # ref to User._id
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "shifts"
        indexes = [
            [("pump_id", 1), ("status", 1)],
            [("pump_id", 1), ("start_time", -1)],
        ]


# ─────────────────────────────────────────────
# ShiftPersonnel — attendants active in shift
# ─────────────────────────────────────────────
class ShiftPersonnel(Document):
    shift_id: PydanticObjectId          # ref to Shift._id
    attendant_id: PydanticObjectId      # ref to Attendant._id

    class Settings:
        name = "shift_personnel"
        indexes = [
            [("shift_id", 1)],
        ]


# ─────────────────────────────────────────────
# ShiftPoint — totalizer readings per nozzle
# ─────────────────────────────────────────────
class ShiftPoint(Document):
    shift_id: PydanticObjectId          # ref to Shift._id
    nozzle_id: int                      # pump's nozzle number
    item_name: str                      # e.g. "Petrol", "Diesel"
    start_reading: float
    end_reading: Optional[float] = None
    testing_value: float = 0.0
    is_active: bool = True

    class Settings:
        name = "shift_points"
        indexes = [
            [("shift_id", 1)],
        ]


# ─────────────────────────────────────────────
# SaleLog — individual transaction in a shift
# ─────────────────────────────────────────────
class SaleLog(Document):
    shift_id: Optional[PydanticObjectId] = None  # null = standalone sale
    pump_id: PydanticObjectId           # ref to Pump._id
    sale_type: SaleType = SaleType.single
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Item / Nozzle
    nozzle_id: Optional[int] = None
    item_name: str                      # e.g. "Petrol"
    rate: float
    quantity: float
    amount: float                       # auto = quantity * rate

    # Payment
    payment_mode: PaymentMode
    pos_machine: Optional[str] = None
    billing_ref: Optional[str] = None

    # Credit sale details
    customer_name: Optional[str] = None
    customer_id: Optional[PydanticObjectId] = None  # ref to Customer._id
    credit_slip_ref: Optional[str] = None

    # Vehicle info
    vehicle_number: Optional[str] = None
    vehicle_type: Optional[str] = None

    # Attendant
    attendant_id: Optional[PydanticObjectId] = None  # ref to Attendant._id

    # Extras
    remarks: Optional[str] = None
    receipt_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: bool = False            # soft delete

    class Settings:
        name = "sale_logs"
        indexes = [
            [("pump_id", 1), ("timestamp", -1)],
            [("shift_id", 1)],
            [("attendant_id", 1)],
        ]