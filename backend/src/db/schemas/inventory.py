"""
Pydantic schemas for inventory module.
Request/Response models for all 4 modules.

FIX: All id fields (pump_id, group_id, item_id) changed from `int` → `str`
     to match the Beanie/MongoDB router which uses PydanticObjectId (string-serialised).
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from src.db.models.inventory import (
    ItemClass, TaxType, RateTaxType, ValuationMethod, AdjustmentReason
)


# ─── ItemGroup Schemas ────────────────────────────────────────────────────────

class ItemGroupCreate(BaseModel):
    pump_id:                str                             # ← was int
    name:                   str
    item_class:             ItemClass           = ItemClass.goods
    category:               Optional[str]       = None
    description:            Optional[str]       = None
    hsn_code:               Optional[str]       = None

    tax_type:               TaxType             = TaxType.vat
    rate_tax_type:          RateTaxType         = RateTaxType.after_tax
    valuation_method:       ValuationMethod     = ValuationMethod.average_purchase_date

    vat_rate:               float               = 0.0
    surcharge_rate:         float               = 0.0
    cess_rate:              float               = 0.0
    additional_cess_rate:   float               = 0.0

    sales_account:          Optional[str]       = None
    purchase_account:       Optional[str]       = None
    vat_account:            Optional[str]       = None
    surcharge_account:      Optional[str]       = None
    cess_account:           Optional[str]       = None
    commission_account:     Optional[str]       = None
    license_fee_account:    Optional[str]       = None
    rebate_account:         Optional[str]       = None


class ItemGroupUpdate(BaseModel):
    name:                   Optional[str]       = None
    category:               Optional[str]       = None
    description:            Optional[str]       = None
    hsn_code:               Optional[str]       = None
    tax_type:               Optional[TaxType]   = None
    rate_tax_type:          Optional[RateTaxType] = None
    valuation_method:       Optional[ValuationMethod] = None
    vat_rate:               Optional[float]     = None
    surcharge_rate:         Optional[float]     = None
    cess_rate:              Optional[float]     = None
    additional_cess_rate:   Optional[float]     = None
    sales_account:          Optional[str]       = None
    purchase_account:       Optional[str]       = None
    vat_account:            Optional[str]       = None
    surcharge_account:      Optional[str]       = None
    cess_account:           Optional[str]       = None
    commission_account:     Optional[str]       = None
    license_fee_account:    Optional[str]       = None
    rebate_account:         Optional[str]       = None


class ItemGroupResponse(BaseModel):
    id:                     str                             # ← was int
    pump_id:                str                             # ← was int
    name:                   str
    item_class:             ItemClass
    category:               Optional[str]
    description:            Optional[str]
    hsn_code:               Optional[str]
    tax_type:               TaxType
    rate_tax_type:          RateTaxType
    valuation_method:       ValuationMethod
    vat_rate:               float
    surcharge_rate:         float
    cess_rate:              float
    additional_cess_rate:   float
    sales_account:          Optional[str]
    purchase_account:       Optional[str]
    vat_account:            Optional[str]
    surcharge_account:      Optional[str]
    cess_account:           Optional[str]
    commission_account:     Optional[str]
    license_fee_account:    Optional[str]
    rebate_account:         Optional[str]
    is_active:              bool
    created_at:             datetime
    updated_at:             datetime

    class Config:
        from_attributes = True


# ─── StockItem Schemas ────────────────────────────────────────────────────────

class StockItemCreate(BaseModel):
    pump_id:            str                                 # ← was int
    group_id:           Optional[str]       = None          # ← was Optional[int]
    name:               str
    item_code:          Optional[str]       = None
    hsn_code:           Optional[str]       = None
    category:           Optional[str]       = None
    unit:               str                 = "Liters"
    is_dispensed_item:  bool                = True
    package_details:    Optional[str]       = None
    selling_rate:       float               = 0.0

    # Optional item-level tax overrides
    override_tax:       bool                = False
    vat_rate:           Optional[float]     = None
    surcharge_rate:     Optional[float]     = None
    cess_rate:          Optional[float]     = None

    # Opening stock (optional — backend defaults to 0.0 if omitted)
    current_quantity:   float               = 0.0
    average_rate:       float               = 0.0


class StockItemUpdate(BaseModel):
    name:               Optional[str]       = None
    item_code:          Optional[str]       = None
    hsn_code:           Optional[str]       = None
    category:           Optional[str]       = None
    unit:               Optional[str]       = None
    is_dispensed_item:  Optional[bool]      = None
    package_details:    Optional[str]       = None
    group_id:           Optional[str]       = None          # ← was Optional[int]
    override_tax:       Optional[bool]      = None
    selling_rate:       Optional[float]     = None
    vat_rate:           Optional[float]     = None
    surcharge_rate:     Optional[float]     = None
    cess_rate:          Optional[float]     = None


class SetRateRequest(BaseModel):
    item_id:            str                                 # ← was int
    selling_rate:       float


class StockItemResponse(BaseModel):
    id:                 str                                 # ← was int
    pump_id:            str                                 # ← was int
    group_id:           Optional[str]                       # ← was Optional[int]
    name:               str
    item_code:          Optional[str]
    hsn_code:           Optional[str]
    category:           Optional[str]
    unit:               str
    is_dispensed_item:  bool
    package_details:    Optional[str]
    current_quantity:   float
    average_rate:       float
    current_valuation:  float
    selling_rate:       float
    override_tax:       bool
    vat_rate:           Optional[float]
    surcharge_rate:     Optional[float]
    cess_rate:          Optional[float]
    is_active:          bool
    created_at:         datetime
    updated_at:         datetime
    group_name:         Optional[str]       = None

    class Config:
        from_attributes = True


# ─── StockPurchase Schemas ────────────────────────────────────────────────────

class PurchaseLineCreate(BaseModel):
    item_id:            str                                 # ← was int
    quantity:           float               = Field(..., gt=0)
    basic_rate:         float               = Field(..., gt=0)
    rebate:             float               = 0.0
    vat_amount:         float               = 0.0
    surcharge_amount:   float               = 0.0
    cess_amount:        float               = 0.0
    license_fees:       float               = 0.0
    dealer_commission:  float               = 0.0

    @property
    def after_tax_amount(self) -> float:
        per_unit = (
            self.basic_rate
            - self.rebate
            + self.vat_amount
            + self.surcharge_amount
            + self.cess_amount
        )
        return round(per_unit * self.quantity, 2)


class StockPurchaseCreate(BaseModel):
    pump_id:                str                             # ← was int
    supplier_name:          str
    invoice_number:         Optional[str]   = None
    invoice_date:           Optional[datetime] = None
    purchase_timestamp:     Optional[datetime] = None

    sample_ref_number:      Optional[str]   = None
    sample_quantity:        Optional[float] = None
    challan_density_15c:    Optional[float] = None
    observed_density:       Optional[float] = None
    tanker_seal_numbers:    Optional[List[str]] = None

    vehicle_number:         Optional[str]   = None
    transporter_name:       Optional[str]   = None
    transporter_phone:      Optional[str]   = None
    driver_name:            Optional[str]   = None
    driver_license:         Optional[str]   = None
    delivery_comments:      Optional[str]   = None

    toll_amount:            float           = 0.0
    toll_receipt_url:       Optional[str]   = None
    extra_charges:          float           = 0.0
    extra_charges_note:     Optional[str]   = None

    line_items:             List[PurchaseLineCreate] = Field(..., min_length=1)


class PurchaseLineResponse(BaseModel):
    id:                 str                                 # ← was int
    item_id:            str                                 # ← was int
    item_name:          Optional[str]       = None
    quantity:           float
    basic_rate:         float
    rebate:             float
    vat_amount:         float
    surcharge_amount:   float
    cess_amount:        float
    license_fees:       float
    dealer_commission:  float
    after_tax_amount:   float

    class Config:
        from_attributes = True


class StockPurchaseResponse(BaseModel):
    id:                     str                             # ← was int
    pump_id:                str                             # ← was int
    supplier_name:          str
    invoice_number:         Optional[str]
    invoice_date:           Optional[datetime]
    purchase_timestamp:     datetime
    is_locked:              bool
    locked_at:              Optional[datetime]

    sample_ref_number:      Optional[str]
    sample_quantity:        Optional[float]
    challan_density_15c:    Optional[float]
    observed_density:       Optional[float]
    tanker_seal_numbers:    Optional[List[str]]

    vehicle_number:         Optional[str]
    transporter_name:       Optional[str]
    transporter_phone:      Optional[str]
    driver_name:            Optional[str]
    driver_license:         Optional[str]
    delivery_comments:      Optional[str]

    toll_amount:            float
    toll_receipt_url:       Optional[str]
    extra_charges:          float
    extra_charges_note:     Optional[str]

    line_items:             List[PurchaseLineResponse] = []
    created_at:             datetime
    updated_at:             datetime

    class Config:
        from_attributes = True


# ─── StockAdjustment Schemas ──────────────────────────────────────────────────

class StockAdjustmentCreate(BaseModel):
    pump_id:    str                                         # ← was int
    item_id:    str                                         # ← was int
    reason:     AdjustmentReason
    quantity:   float = Field(..., gt=0)
    notes:      Optional[str] = None


class StockAdjustmentResponse(BaseModel):
    id:         str                                         # ← was int
    pump_id:    str                                         # ← was int
    item_id:    str                                         # ← was int
    item_name:  Optional[str]   = None
    reason:     AdjustmentReason
    quantity:   float
    notes:      Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Stock Summary Schema ─────────────────────────────────────────────────────

class StockSummaryRow(BaseModel):
    item_id:                str                             # ← was int
    item_name:              str
    item_code:              Optional[str]
    unit:                   str
    group_name:             Optional[str]
    category:               Optional[str]

    opening_quantity:       float
    opening_value:          float

    purchase_quantity:      float
    sales_quantity:         float
    adjustment_quantity:    float

    closing_quantity:       float
    closing_value:          float
    average_rate:           float


class StockSummaryResponse(BaseModel):
    pump_id:        str                                     # ← was int
    date_from:      Optional[str]
    date_to:        Optional[str]
    group_id:       Optional[str]                           # ← was Optional[int]
    category:       Optional[str]
    items:          List[StockSummaryRow]
    total_closing_value: float