"""
Inventory models: ItemGroup → StockItem → StockPurchase/StockAdjustment
"""
import enum
from datetime import datetime
from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional, List, Any


# ─── Enums ────────────────────────────────────────────────────────────────────

class ItemClass(str, enum.Enum):
    goods = "goods"
    services = "services"

class TaxType(str, enum.Enum):
    vat = "vat"
    gst = "gst"

class RateTaxType(str, enum.Enum):
    after_tax = "after_tax"
    before_tax = "before_tax"

class ValuationMethod(str, enum.Enum):
    average_purchase_date = "average_purchase_date"
    fifo = "fifo"

class AdjustmentReason(str, enum.Enum):
    damage = "damage"
    loss = "loss"
    theft = "theft"
    expired = "expired"
    other = "other"


# ─── ItemGroup ────────────────────────────────────────────────────────────────

class ItemGroup(Document):
    """Parent group - defines tax settings inherited by all child StockItems."""
    pump_id: PydanticObjectId           # ref to Pump._id
    name: str
    item_class: ItemClass = ItemClass.goods
    category: Optional[str] = None     # Fuel, Lubricants
    description: Optional[str] = None
    hsn_code: Optional[str] = None
    tax_type: TaxType = TaxType.vat
    rate_tax_type: RateTaxType = RateTaxType.after_tax
    valuation_method: ValuationMethod = ValuationMethod.average_purchase_date
    vat_rate: float = 0.0
    surcharge_rate: float = 0.0
    cess_rate: float = 0.0
    additional_cess_rate: float = 0.0
    # Account mappings
    sales_account: Optional[str] = None
    purchase_account: Optional[str] = None
    vat_account: Optional[str] = None
    surcharge_account: Optional[str] = None
    cess_account: Optional[str] = None
    commission_account: Optional[str] = None
    license_fee_account: Optional[str] = None
    rebate_account: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True

    class Settings:
        name = "item_groups"
        indexes = [
            [("pump_id", 1)],
        ]


# ─── StockItem ────────────────────────────────────────────────────────────────

class StockItem(Document):
    """Individual product - inherits tax from ItemGroup but can override."""
    pump_id: PydanticObjectId           # ref to Pump._id
    group_id: Optional[PydanticObjectId] = None  # ref to ItemGroup._id
    name: str
    item_code: Optional[str] = None     # e.g. "MS" for Petrol
    hsn_code: Optional[str] = None
    category: Optional[str] = None
    unit: str = "Liters"
    is_dispensed_item: bool = True
    package_details: Optional[str] = None
    current_quantity: float = 0.0
    average_rate: float = 0.0
    current_valuation: float = 0.0
    selling_rate: float = 0.0
    # Item-level tax overrides
    override_tax: bool = False
    vat_rate: Optional[float] = None
    surcharge_rate: Optional[float] = None
    cess_rate: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True

    class Settings:
        name = "stock_items"
        indexes = [
            [("pump_id", 1)],
            [("pump_id", 1), ("item_code", 1)],
        ]


# ─── StockPurchaseLine (embedded / separate) ─────────────────────────────────

class StockPurchaseLine(Document):
    """One row per item in a purchase invoice."""
    purchase_id: PydanticObjectId       # ref to StockPurchase._id
    item_id: PydanticObjectId           # ref to StockItem._id
    quantity: float
    basic_rate: float
    rebate: float = 0.0
    vat_amount: float = 0.0
    surcharge_amount: float = 0.0
    cess_amount: float = 0.0
    license_fees: float = 0.0
    dealer_commission: float = 0.0
    after_tax_amount: float

    class Settings:
        name = "stock_purchase_lines"
        indexes = [
            [("purchase_id", 1)],
        ]


# ─── StockPurchase (Header) ───────────────────────────────────────────────────

class StockPurchase(Document):
    """One invoice = one StockPurchase header."""
    pump_id: PydanticObjectId           # ref to Pump._id
    supplier_name: str
    invoice_number: Optional[str] = None
    invoice_date: Optional[datetime] = None
    purchase_timestamp: datetime = Field(default_factory=datetime.utcnow)
    is_locked: bool = False
    locked_at: Optional[datetime] = None
    # Fuel Sample / Quality Compliance
    sample_ref_number: Optional[str] = None
    sample_quantity: Optional[float] = None
    challan_density_15c: Optional[float] = None
    observed_density: Optional[float] = None
    tanker_seal_numbers: Optional[List[str]] = None
    # Logistics
    vehicle_number: Optional[str] = None
    transporter_name: Optional[str] = None
    transporter_phone: Optional[str] = None
    driver_name: Optional[str] = None
    driver_license: Optional[str] = None
    delivery_comments: Optional[str] = None
    # Ancillary Costs
    toll_amount: float = 0.0
    toll_receipt_url: Optional[str] = None
    extra_charges: float = 0.0
    extra_charges_note: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "stock_purchases"
        indexes = [
            [("pump_id", 1), ("purchase_timestamp", -1)],
        ]


# ─── StockAdjustment ─────────────────────────────────────────────────────────

class StockAdjustment(Document):
    """Manual inventory correction."""
    pump_id: PydanticObjectId           # ref to Pump._id
    item_id: PydanticObjectId           # ref to StockItem._id
    reason: AdjustmentReason
    quantity: float                     # Negative for damage/loss/theft/expired
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[PydanticObjectId] = None   # ref to User._id

    class Settings:
        name = "stock_adjustments"
        indexes = [
            [("pump_id", 1)],
            [("item_id", 1)],
        ]