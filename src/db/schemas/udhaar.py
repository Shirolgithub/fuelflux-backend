from pydantic import BaseModel , field_validator ,model_validator
from typing import Optional ,List ,Any
from enum import Enum
from datetime import datetime

class UdhaarBase(BaseModel):
    amount: float
    volume: Optional[float] = None
    fuel_type: Optional[str] = None
    remarks: Optional[str] = None
    udhaar_type: str = "normal"   # normal, credit_fleet, manual

class UdhaarCreate(UdhaarBase):
    vehicle_id: Optional[str] = None
    customer_id: Optional[str] = None
    pump_id: str

class UdhaarResponse(UdhaarBase):
    id: str
    vehicle_id: Optional[str] = None
    customer_id: Optional[str] = None
    pump_id: str
    attendant_id: Optional[str] = None
    status: str
    used_at: datetime

    class Config:
        from_attributes = True

# ─────────────────────────────────────────────
# ENUMS (mirror models)
# ─────────────────────────────────────────────
 
class CustomerType(str, Enum):
    private = "private"
    commercial = "commercial"
 
class KYCStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
 
class ContractStatus(str, Enum):
    active = "active"
    expired = "expired"
    suspended = "suspended"
    amended = "amended"
 
class BillingFrequency(str, Enum):
    one_time = "one_time"
    recurring = "recurring"
 
class BillingCycle(str, Enum):
    weekly = "weekly"
    fortnightly = "fortnightly"
    monthly = "monthly"
 
class BillBy(str, Enum):
    vehicle = "vehicle"
    customer = "customer"
 
class RegistrationType(str, Enum):
    private = "private"
    commercial = "commercial"
 
 
# ─────────────────────────────────────────────
# KYC DOCUMENT
# ─────────────────────────────────────────────
 
class KYCDocumentCreate(BaseModel):
    document_type: str
    image_url: Optional[str] = None
 
class KYCVerifyRequest(BaseModel):
    status: KYCStatus   # accepted or rejected
    rejection_reason: Optional[str] = None
 
class KYCDocumentResponse(BaseModel):
    id: str
    document_type: str
    image_url: Optional[str]
    status: KYCStatus
    rejection_reason: Optional[str]
    reviewed_at: Optional[datetime]
    created_at: datetime
 
    class Config:
        from_attributes = True
 
 
# ─────────────────────────────────────────────
# CUSTOMER
# ─────────────────────────────────────────────
 
class CustomerCreate(BaseModel):
    customer_type: CustomerType = CustomerType.commercial
    name: str
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    party_account_id: Optional[str] = None
 
class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    customer_type: Optional[CustomerType] = None
 
class CustomerResponse(BaseModel):
    id: str
    pump_id: str
    customer_type: CustomerType
    name: str
    contact_name: Optional[str]
    contact_phone: Optional[str]
    contact_email: Optional[str]
    party_account_id: Optional[str]
    kyc_status: KYCStatus
    is_active: bool
    created_at: datetime
 
    class Config:
        from_attributes = True
 
class CustomerListItem(BaseModel):
    """Lightweight response for directory listing"""
    id: str
    name: str
    customer_type: CustomerType
    contact_phone: Optional[str]
    kyc_status: KYCStatus
    vehicle_count: int = 0
    has_active_contract: bool = False
    created_at: datetime
 
    class Config:
        from_attributes = True
 
 
# ─────────────────────────────────────────────
# VEHICLE
# ─────────────────────────────────────────────
 
class VehicleCreate(BaseModel):
    customer_id: str
    number_plate: str
    registration_type: RegistrationType = RegistrationType.commercial
    make: Optional[str] = None
    model: Optional[str] = None
    variant: Optional[str] = None
    fuel_types: Optional[List[str]] = []
    emission_standard: Optional[str] = None
    engine_number: Optional[str] = None
    chassis_number: Optional[str] = None
    registration_date: Optional[datetime] = None
 
class VehicleUpdate(BaseModel):
    make: Optional[str] = None
    model: Optional[str] = None
    variant: Optional[str] = None
    fuel_types: Optional[List[str]] = None
    emission_standard: Optional[str] = None
    engine_number: Optional[str] = None
    chassis_number: Optional[str] = None
    registration_date: Optional[datetime] = None
 
class VehicleResponse(BaseModel):
    id: str
    customer_id: str
    number_plate: str
    registration_type: RegistrationType
    make: Optional[str]
    model: Optional[str]
    variant: Optional[str]
    fuel_types: Optional[List[str]]
    emission_standard: Optional[str]
    engine_number: Optional[str]
    chassis_number: Optional[str]
    registration_date: Optional[datetime]
    is_active: bool
    created_at: datetime
 
    class Config:
        from_attributes = True
 
 
# ─────────────────────────────────────────────
# SLIP BOOKLET
# ─────────────────────────────────────────────
 
class SlipBookletCreate(BaseModel):
    booklet_number: str
    start_number: int
    end_number: int
 
    @model_validator(mode="after")
    def validate_range(self):
        if self.end_number < self.start_number:
            raise ValueError("end_number must be >= start_number")
        return self
 
class SlipBookletResponse(BaseModel):
    id: str
    booklet_number: str
    start_number: int
    end_number: int
    total_slips: int
 
    class Config:
        from_attributes = True
 
 
# ─────────────────────────────────────────────
# ITEM LIMIT
# ─────────────────────────────────────────────
 
class ItemLimitCreate(BaseModel):
    item_name: str  # petrol / diesel
    qty_per_fill: Optional[float] = None
    qty_per_day: Optional[float] = None
    qty_per_cycle: Optional[float] = None
 
class ItemLimitResponse(BaseModel):
    id: str
    item_name: str
    qty_per_fill: Optional[float]
    qty_per_day: Optional[float]
    qty_per_cycle: Optional[float]
 
    class Config:
        from_attributes = True
 
 
# ─────────────────────────────────────────────
# CUSTOM CONDITION CARD
# ─────────────────────────────────────────────
 
class CustomConditionCreate(BaseModel):
    vehicle_type: Optional[str] = None   # HMV, LMV, etc.
    item_name: Optional[str] = None      # diesel, petrol
    station_id: Optional[str] = None
 
    max_slips: Optional[int] = None
    money_per_fill: Optional[float] = None
    money_per_day: Optional[float] = None
    money_per_cycle: Optional[float] = None
    qty_per_fill: Optional[float] = None
    qty_per_day: Optional[float] = None
    qty_per_cycle: Optional[float] = None
 
class CustomConditionResponse(BaseModel):
    id: str
    vehicle_type: Optional[str]
    item_name: Optional[str]
    station_id: Optional[str]
    max_slips: Optional[int]
    money_per_fill: Optional[float]
    money_per_day: Optional[float]
    money_per_cycle: Optional[float]
    qty_per_fill: Optional[float]
    qty_per_day: Optional[float]
    qty_per_cycle: Optional[float]
 
    class Config:
        from_attributes = True
 
 
# ─────────────────────────────────────────────
# SOP RECIPIENT
# ─────────────────────────────────────────────
 
class SOPRecipient(BaseModel):
    type: str    # "email" or "sms"
    value: str   # email address or phone number
 
 
# ─────────────────────────────────────────────
# CONTRACT
# ─────────────────────────────────────────────
 
class ContractCreate(BaseModel):
    customer_id: str
 
    # Letterhead
    station_name: Optional[str] = None
    org_name: Optional[str] = None
    address: Optional[str] = None
    gst_number: Optional[str] = None
 
    # Validity
    valid_from: Optional[datetime] = None   # defaults to now if not sent
    valid_to: datetime
    security_deposit: float = 0.0
 
    # Slips
    slip_booklets: Optional[List[SlipBookletCreate]] = []
 
    # Global limits
    total_credit_limit: float = 0.0
    max_spending_slips: Optional[int] = None
    money_limit_per_fill: Optional[float] = None
    money_limit_per_day: Optional[float] = None
    money_limit_per_cycle: Optional[float] = None
 
    # Item-specific limits
    item_limits: Optional[List[ItemLimitCreate]] = []
 
    # Custom conditions
    custom_conditions: Optional[List[CustomConditionCreate]] = []
 
    # Billing
    billing_frequency: BillingFrequency = BillingFrequency.recurring
    bill_by: BillBy = BillBy.customer
    billing_cycle: Optional[BillingCycle] = None
    billing_start_date: Optional[datetime] = None
    round_off: bool = False
 
    # SOPs
    require_meter_photo: bool = False
    require_vehicle_photo: bool = False
    require_fueling_video: bool = False
    require_driver_verification: bool = False
    sop_recipients: Optional[List[SOPRecipient]] = []
 
    # T&C
    late_payment_interest: Optional[float] = None
    deposit_utilization_days: Optional[int] = None
    suspension_period_days: Optional[int] = None
    invoice_dispute_days: Optional[int] = None
    custom_terms: Optional[str] = None
 
    @field_validator("valid_to")
    @classmethod
    def valid_to_must_be_future(cls, v):
        v_naive = v.replace(tzinfo=None) if v.tzinfo else v
        if v_naive <= datetime.utcnow():
            raise ValueError("valid_to must be a future date")
        return v_naive
 
class ContractResponse(BaseModel):
    id: str
    customer_id: str
    pump_id: str
    version: int
    station_name: Optional[str]
    org_name: Optional[str]
    address: Optional[str]
    gst_number: Optional[str]
    valid_from: datetime
    valid_to: datetime
    security_deposit: float
    total_credit_limit: float
    max_spending_slips: Optional[int]
    money_limit_per_fill: Optional[float]
    money_limit_per_day: Optional[float]
    money_limit_per_cycle: Optional[float]
    billing_frequency: BillingFrequency
    bill_by: BillBy
    billing_cycle: Optional[BillingCycle]
    billing_start_date: Optional[datetime]
    round_off: bool
    require_meter_photo: bool
    require_vehicle_photo: bool
    require_fueling_video: bool
    require_driver_verification: bool
    sop_recipients: Optional[List[Any]]
    late_payment_interest: Optional[float]
    deposit_utilization_days: Optional[int]
    suspension_period_days: Optional[int]
    invoice_dispute_days: Optional[int]
    custom_terms: Optional[str]
    status: ContractStatus
    current_spend: float
    current_slips_used: int
    amended_from: Optional[str]
    created_at: datetime
 
    # Nested
    slip_booklets: List[SlipBookletResponse] = []
    item_limits: List[ItemLimitResponse] = []
    custom_conditions: List[CustomConditionResponse] = []
 
    # Computed
    credit_usage_percent: Optional[float] = None
 
    class Config:
        from_attributes = True
 
 
# ─────────────────────────────────────────────
# CONTRACT USAGE (for progress bar)
# ─────────────────────────────────────────────
 
class ContractUsageResponse(BaseModel):
    contract_id: str
    total_credit_limit: float
    current_spend: float
    remaining_credit: float
    usage_percent: float
    max_spending_slips: Optional[int]
    current_slips_used: int
    status: ContractStatus
    alert: bool   # True if usage >= 90%
 
 
# ─────────────────────────────────────────────
# TRANSACTION
# ─────────────────────────────────────────────
 
class UdhaarTransactionCreate(BaseModel):
    contract_id: str
    vehicle_id: Optional[str] = None
    item_name: Optional[str] = None
    quantity: float = 0.0
    amount: float = 0.0
    slip_number: Optional[str] = None
    meter_photo_url: Optional[str] = None
    vehicle_photo_url: Optional[str] = None
    fueling_video_url: Optional[str] = None
    driver_verified: bool = False
 
class UdhaarTransactionResponse(BaseModel):
    id: str
    contract_id: str
    customer_id: str
    vehicle_id: Optional[str]
    item_name: Optional[str]
    quantity: float
    amount: float
    slip_number: Optional[str]
    meter_photo_url: Optional[str]
    vehicle_photo_url: Optional[str]
    fueling_video_url: Optional[str]
    driver_verified: bool
    created_at: datetime
 
    class Config:
        from_attributes = True
 
 
# ─────────────────────────────────────────────
# INVOICE
# ─────────────────────────────────────────────
 
class InvoiceResponse(BaseModel):
    id: str
    contract_id: str
    customer_id: str
    vehicle_id: Optional[str]
    cycle_start: datetime
    cycle_end: datetime
    total_amount: float
    rounded_amount: float
    status: str
    late_interest_applied: float
    deposit_utilized: float
    generated_at: datetime
    paid_at: Optional[datetime]
 
    class Config:
        from_attributes = True
 
 
# ─────────────────────────────────────────────
# SINGLE CUSTOMER VIEW (full dashboard)
# ─────────────────────────────────────────────
 
class SingleCustomerView(BaseModel):
    customer: CustomerResponse
    kyc_documents: List[KYCDocumentResponse] = []
    vehicles: List[VehicleResponse] = []
    active_contract: Optional[ContractResponse] = None
    contract_usage: Optional[ContractUsageResponse] = None
    recent_transactions: List[UdhaarTransactionResponse] = []
    recent_invoices: List[InvoiceResponse] = []
 