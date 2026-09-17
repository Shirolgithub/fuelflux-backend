"""
FILE: src/db/schemas/sales_schemas.py
Updated for MongoDB — IDs are str (ObjectId)
"""

from pydantic import BaseModel, Field, model_validator
from typing import Optional, List
from datetime import datetime
from enum import Enum


class ShiftType(str, Enum):
    morning = "morning"
    evening = "evening"
    night = "night"
    custom = "custom"


class PaymentMode(str, Enum):
    cash = "cash"
    credit = "credit"
    pos = "pos"
    upi = "upi"


class SaleType(str, Enum):
    single = "single"
    batch = "batch"


# ── Shift Point Reading ───────────────────────────────────────────
class ShiftPointIn(BaseModel):
    nozzle_id: int
    item_name: str
    start_reading: float
    testing_value: float = 0.0
    is_active: bool = True


class ShiftPointOut(BaseModel):
    id: str
    nozzle_id: int
    item_name: str
    start_reading: float
    end_reading: Optional[float] = None
    testing_value: float
    is_active: bool

    class Config:
        from_attributes = True


# ── Start Shift ───────────────────────────────────────────────────
class StartShiftRequest(BaseModel):
    pump_id: str
    shift_type: ShiftType
    start_time: datetime
    point_readings: List[ShiftPointIn]
    personnel_ids: List[str] = []   # attendant IDs active this shift


class ShiftResponse(BaseModel):
    id: str
    pump_id: str
    shift_type: ShiftType
    status: str
    start_time: datetime
    end_time: Optional[datetime] = None
    point_readings: List[ShiftPointOut] = []

    class Config:
        from_attributes = True


# ── End Shift ─────────────────────────────────────────────────────
class EndPointReading(BaseModel):
    nozzle_id: int
    end_reading: float
    testing_value: Optional[float] = None   # can update testing value at close too


class EndShiftRequest(BaseModel):
    shift_id: str
    end_readings: List[EndPointReading]


# ── Sale Log (Single or Batch) ────────────────────────────────────
class SaleLogCreate(BaseModel):
    pump_id: str
    shift_id: Optional[str] = None          # null = standalone sale
    sale_type: SaleType = SaleType.single
    timestamp: Optional[datetime] = None    # past timestamp allowed

    nozzle_id: Optional[int] = None
    item_name: str
    rate: float
    quantity: float
    # amount auto-calculated backend side

    payment_mode: PaymentMode
    pos_machine: Optional[str] = None
    billing_ref: Optional[str] = None

    customer_name: Optional[str] = None
    customer_id: Optional[str] = None
    credit_slip_ref: Optional[str] = None

    vehicle_number: Optional[str] = None
    vehicle_type: Optional[str] = None

    attendant_id: Optional[str] = None
    remarks: Optional[str] = None
    receipt_url: Optional[str] = None

    @model_validator(mode="after")
    def validate_credit(self):
        if self.payment_mode == PaymentMode.credit:
            if not self.customer_name and not self.customer_id:
                raise ValueError("Credit sales require customer_name or customer_id")
        if self.payment_mode == PaymentMode.pos:
            if not self.billing_ref:
                raise ValueError("POS sales require billing_ref")
        return self


class SaleLogResponse(BaseModel):
    id: str
    pump_id: str
    shift_id: Optional[str]
    sale_type: str
    timestamp: datetime
    nozzle_id: Optional[int]
    item_name: str
    rate: float
    quantity: float
    amount: float
    payment_mode: str
    pos_machine: Optional[str]
    billing_ref: Optional[str]
    customer_name: Optional[str]
    credit_slip_ref: Optional[str]
    vehicle_number: Optional[str]
    vehicle_type: Optional[str]
    attendant_id: Optional[str]
    remarks: Optional[str]
    receipt_url: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ── Item Rate (mid-shift update) ──────────────────────────────────
class ItemRateUpdate(BaseModel):
    pump_id: str
    item_name: str
    new_rate: float


# ── Shift Summary (See Summary button) ───────────────────────────
class PointSummary(BaseModel):
    nozzle_id: int
    item_name: str
    start_reading: float
    end_reading: Optional[float]
    testing_value: float
    sold_quantity: float            # (end - start) - testing
    total_amount: float


class PaymentSummary(BaseModel):
    payment_mode: str
    total_amount: float
    transaction_count: int


class ItemSummary(BaseModel):
    item_name: str
    total_quantity: float
    total_amount: float


class ShiftSummaryResponse(BaseModel):
    shift_id: str
    pump_id: str
    shift_type: str
    status: str
    start_time: datetime
    end_time: Optional[datetime]
    net_amount: float
    total_quantity: float
    credit_ratio: float             # credit_amount / net_amount * 100
    point_summaries: List[PointSummary]
    payment_summaries: List[PaymentSummary]
    item_summaries: List[ItemSummary]
    total_sales_count: int


# ── Overview Dashboard ────────────────────────────────────────────
class OverviewResponse(BaseModel):
    pump_id: str
    net_amount: float
    credit_ratio: float
    cash_amount: float
    credit_amount: float
    pos_amount: float
    monthly_item_sales: dict       # {item_name: {month: amount}}
    weekly_item_sales: dict
    monthly_payment_breakdown: dict
    recent_shifts: List[dict]


# ── Filters for Entries tab ───────────────────────────────────────
class SaleFilter(BaseModel):
    pump_id: str
    customer_name: Optional[str] = None
    vehicle_type: Optional[str] = None
    vehicle_number: Optional[str] = None
    item_name: Optional[str] = None
    payment_mode: Optional[PaymentMode] = None
    shift_id: Optional[str] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    page: int = 1
    page_size: int = 50