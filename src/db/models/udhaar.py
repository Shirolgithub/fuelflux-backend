"""
Udhaar (Credit) models — converted to Beanie Documents.
"""
from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional, List, Any, Dict
from datetime import datetime
import enum


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class CustomerType(str, enum.Enum):
    private = "private"
    commercial = "commercial"

class KYCStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"

class ContractStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    suspended = "suspended"
    amended = "amended"

class BillingFrequency(str, enum.Enum):
    one_time = "one_time"
    recurring = "recurring"

class BillingCycle(str, enum.Enum):
    weekly = "weekly"
    fortnightly = "fortnightly"
    monthly = "monthly"

class BillBy(str, enum.Enum):
    vehicle = "vehicle"
    customer = "customer"

class LimitType(str, enum.Enum):
    global_limit = "global"
    item_specific = "item_specific"
    custom_condition = "custom_condition"

class RegistrationType(str, enum.Enum):
    private = "private"
    commercial = "commercial"


# ─────────────────────────────────────────────
# UDHAAR (Simple/Legacy)
# ─────────────────────────────────────────────

class Udhaar(Document):
    customer_id: Optional[PydanticObjectId] = None   # ref to Customer._id
    vehicle_id: Optional[PydanticObjectId] = None    # ref to Vehicle._id
    pump_id: PydanticObjectId                         # ref to Pump._id
    attendant_id: Optional[PydanticObjectId] = None  # ref to Attendant._id
    amount: float
    volume: Optional[float] = None
    fuel_type: Optional[str] = None
    udhaar_type: str = "normal"                       # normal, credit_fleet, manual
    status: str = "pending"                           # pending, approved, paid, disputed
    remarks: Optional[str] = None
    used_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "udhaar"
        indexes = [
            [("pump_id", 1), ("status", 1)],
        ]


# ─────────────────────────────────────────────
# UDHAAR CUSTOMER
# ─────────────────────────────────────────────

class UdhaarCustomer(Document):
    """Credit customer entity. Can be private or commercial."""
    pump_id: PydanticObjectId                         # ref to Pump._id
    customer_type: CustomerType = CustomerType.commercial
    name: str
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    party_account_id: Optional[PydanticObjectId] = None
    kyc_status: KYCStatus = KYCStatus.pending
    is_active: bool = True
    deleted_at: Optional[datetime] = None             # soft delete
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    credit_limit:float = 0.0

    class Settings:
        name = "udhaar_customers"
        indexes = [
            [("pump_id", 1)],
            [("pump_id", 1), ("is_active", 1)],
        ]


# ─────────────────────────────────────────────
# KYC DOCUMENTS
# ─────────────────────────────────────────────

class UdhaarKYCDocument(Document):
    """KYC document per customer. Multiple documents allowed."""
    customer_id: PydanticObjectId                     # ref to UdhaarCustomer._id
    document_type: str                                # aadhaar, pan, gst
    image_url: Optional[str] = None
    status: KYCStatus = KYCStatus.pending
    rejection_reason: Optional[str] = None
    reviewed_by: Optional[PydanticObjectId] = None    # ref to User._id
    reviewed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "udhaar_kyc_documents"
        indexes = [
            [("customer_id", 1)],
        ]


# ─────────────────────────────────────────────
# UDHAAR VEHICLE
# ─────────────────────────────────────────────

class UdhaarVehicle(Document):
    """Vehicle linked to a credit customer."""
    customer_id: PydanticObjectId                     # ref to UdhaarCustomer._id
    pump_id: PydanticObjectId                         # ref to Pump._id
    number_plate: str
    registration_type: str = "commercial"             # private or commercial
    make: Optional[str] = None
    model: Optional[str] = None
    variant: Optional[str] = None
    fuel_types: List[str] = Field(default_factory=list)  # ["diesel", "petrol"]
    emission_standard: Optional[str] = None
    engine_number: Optional[str] = None
    chassis_number: Optional[str] = None
    registration_date: Optional[datetime] = None
    is_active: bool = True
    deleted_at: Optional[datetime] = None             # soft delete
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "udhaar_vehicles"
        indexes = [
            [("customer_id", 1)],
            [("pump_id", 1), ("number_plate", 1)],
        ]


# ─────────────────────────────────────────────
# CONTRACT
# ─────────────────────────────────────────────

class UdhaarContract(Document):
    """Contract issued to a credit customer. Versioned."""
    customer_id: PydanticObjectId                     # ref to UdhaarCustomer._id
    pump_id: PydanticObjectId                         # ref to Pump._id
    version: int = 1

    # Letterhead
    station_name: Optional[str] = None
    org_name: Optional[str] = None
    address: Optional[str] = None
    gst_number: Optional[str] = None

    # Validity
    valid_from: datetime = Field(default_factory=datetime.utcnow)
    valid_to: datetime

    # Financial safety
    security_deposit: float = 0.0
    total_credit_limit: float = 0.0
    max_spending_slips: Optional[int] = None

    # Money limits
    money_limit_per_fill: Optional[float] = None
    money_limit_per_day: Optional[float] = None
    money_limit_per_cycle: Optional[float] = None

    # Billing config
    billing_frequency: BillingFrequency = BillingFrequency.recurring
    bill_by: BillBy = BillBy.customer
    billing_cycle: Optional[BillingCycle] = None
    billing_start_date: Optional[datetime] = None
    round_off: bool = False

    # Operational SOPs
    require_meter_photo: bool = False
    require_vehicle_photo: bool = False
    require_fueling_video: bool = False
    require_driver_verification: bool = False
    sop_recipients: List[Dict[str, Any]] = Field(default_factory=list)

    # Terms & Conditions
    late_payment_interest: Optional[float] = None
    deposit_utilization_days: Optional[int] = None
    suspension_period_days: Optional[int] = None
    invoice_dispute_days: Optional[int] = None
    custom_terms: Optional[str] = None

    # Status & tracking
    status: ContractStatus = ContractStatus.active
    current_spend: float = 0.0
    current_slips_used: int = 0

    # Audit
    created_by: Optional[PydanticObjectId] = None    # ref to User._id
    amended_from: Optional[PydanticObjectId] = None  # previous version _id
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "udhaar_contracts"
        indexes = [
            [("customer_id", 1), ("version", -1)],
            [("pump_id", 1), ("status", 1)],
        ]


# ─────────────────────────────────────────────
# SLIP BOOKLETS
# ─────────────────────────────────────────────

class UdhaarSlipBooklet(Document):
    """Physical slip booklet assigned to a contract."""
    contract_id: PydanticObjectId       # ref to UdhaarContract._id
    booklet_number: str
    start_number: int
    end_number: int
    total_slips: int                    # auto = end - start + 1
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "udhaar_slip_booklets"
        indexes = [
            [("contract_id", 1)],
        ]


# ─────────────────────────────────────────────
# ITEM-SPECIFIC LIMITS
# ─────────────────────────────────────────────

class UdhaarItemLimit(Document):
    """Quantity limits for a specific fuel item per fill/day/cycle."""
    contract_id: PydanticObjectId       # ref to UdhaarContract._id
    item_name: str                      # petrol / diesel
    qty_per_fill: Optional[float] = None
    qty_per_day: Optional[float] = None
    qty_per_cycle: Optional[float] = None

    class Settings:
        name = "udhaar_item_limits"
        indexes = [
            [("contract_id", 1)],
        ]


# ─────────────────────────────────────────────
# CUSTOM CONDITION CARDS
# ─────────────────────────────────────────────

class UdhaarCustomCondition(Document):
    """Condition card: limits applied when vehicle_type+item+station ALL match."""
    contract_id: PydanticObjectId       # ref to UdhaarContract._id
    vehicle_type: Optional[str] = None
    item_name: Optional[str] = None
    station_id: Optional[PydanticObjectId] = None  # ref to Pump._id
    max_slips: Optional[int] = None
    money_per_fill: Optional[float] = None
    money_per_day: Optional[float] = None
    money_per_cycle: Optional[float] = None
    qty_per_fill: Optional[float] = None
    qty_per_day: Optional[float] = None
    qty_per_cycle: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "udhaar_custom_conditions"
        indexes = [
            [("contract_id", 1)],
        ]


# ─────────────────────────────────────────────
# INVOICES
# ─────────────────────────────────────────────

class UdhaarInvoice(Document):
    """Generated at end of billing cycle."""
    contract_id: PydanticObjectId       # ref to UdhaarContract._id
    customer_id: PydanticObjectId       # ref to UdhaarCustomer._id
    vehicle_id: Optional[PydanticObjectId] = None  # null if bill_by=customer
    pump_id: PydanticObjectId           # ref to Pump._id
    cycle_start: datetime
    cycle_end: datetime
    total_amount: float = 0.0
    rounded_amount: float = 0.0
    status: str = "unpaid"              # unpaid, paid, disputed, overdue
    late_interest_applied: float = 0.0
    deposit_utilized: float = 0.0
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    paid_at: Optional[datetime] = None
    disputed_at: Optional[datetime] = None
    dispute_reason: Optional[str] = None

    # Corporate billing fields (added for CorporateBillingConfig auto-generation)
    invoice_number: Optional[str] = None
    billing_period_label: Optional[str] = None
    invoice_pdf_url: Optional[str] = None

    # GST fields
    gstin_supplier: Optional[str] = None
    gstin_recipient: Optional[str] = None
    supply_type: Optional[str] = None   # intrastate / interstate
    cgst_rate: float = 0.0
    sgst_rate: float = 0.0
    cgst_amount: float = 0.0
    sgst_amount: float = 0.0
    igst_rate: float = 0.0
    igst_amount: float = 0.0
    taxable_amount: float = 0.0
    total_tax: float = 0.0

    class Settings:
        name = "udhaar_invoices"
        indexes = [
            [("contract_id", 1)],
            [("customer_id", 1), ("status", 1)],
            [("invoice_number", 1)],
        ]


# ─────────────────────────────────────────────
# CREDIT TRANSACTIONS
# ─────────────────────────────────────────────

class UdhaarTransaction(Document):
    """Every fuel sale linked to a credit customer/vehicle."""
    contract_id: PydanticObjectId       # ref to UdhaarContract._id
    customer_id: PydanticObjectId       # ref to UdhaarCustomer._id
    vehicle_id: Optional[PydanticObjectId] = None  # ref to UdhaarVehicle._id
    pump_id: PydanticObjectId           # ref to Pump._id
    item_name: Optional[str] = None     # petrol/diesel
    quantity: float = 0.0              # litres
    amount: float = 0.0
    slip_number: Optional[str] = None
    meter_photo_url: Optional[str] = None
    vehicle_photo_url: Optional[str] = None
    fueling_video_url: Optional[str] = None
    driver_verified: bool = False
    invoice_id: Optional[PydanticObjectId] = None  # ref to UdhaarInvoice._id
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "udhaar_transactions"
        indexes = [
            [("contract_id", 1)],
            [("customer_id", 1), ("created_at", -1)],
        ]


# ─────────────────────────────────────────────
# CORPORATE BILLING CONFIG
# ─────────────────────────────────────────────

class CorporateBillingConfig(Document):
    """
    Per-pump configuration for automated corporate/consolidated billing.
    Stores GST details, invoice numbering, and billing schedule.
    """
    pump_id: PydanticObjectId                     # ref to Pump._id
    gstin: Optional[str] = None                   # Supplier GSTIN
    billing_day: int = 1                          # Day of month to auto-generate invoices (1-28)
    invoice_prefix: str = "INV"                   # e.g. "INV" → INV-00001
    last_invoice_number: int = 0                  # Auto-incremented
    default_fuel_gst_rate: float = 0.0            # e.g. 0.18 for 18%
    default_supply_type: str = "intrastate"       # intrastate / interstate
    cc_email: Optional[str] = None                # CC email for invoice notifications
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "corporate_billing_configs"
        indexes = [
            [("pump_id", 1)],
            [("pump_id", 1), ("billing_day", 1)],
        ]