"""
FILE: src/db/schemas/accounting.py
Updated for MongoDB migration — IDs are str (representing ObjectId).
"""

from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


# ─── Enums (mirror Beanie enums) ─────────────────────────────────────────

class AccountNatureEnum(str, Enum):
    income      = "income"
    expenditure = "expenditure"
    asset       = "asset"
    liability   = "liability"

class PartyCategoryEnum(str, Enum):
    customer = "customer"
    supplier = "supplier"
    other    = "other"

class RegistrationTypeEnum(str, Enum):
    registered   = "registered"
    unregistered = "unregistered"
    composition  = "composition"
    consumer     = "consumer"

class VoucherTypeEnum(str, Enum):
    payment  = "payment"
    receipt  = "receipt"
    contra   = "contra"
    journal  = "journal"


# ─── AccountGroup Schemas ─────────────────────────────────────────────────────

class AccountGroupCreate(BaseModel):
    pump_id:              str
    name:                 str
    category:             Optional[str] = None
    nature:               AccountNatureEnum
    affects_gross_profit: bool = False

class AccountGroupResponse(BaseModel):
    id:                   str
    pump_id:              str
    name:                 str
    category:             Optional[str] = None
    nature:               str
    affects_gross_profit: bool
    is_active:            bool
    created_at:           datetime

    class Config:
        from_attributes = True


# ─── Account Schemas ──────────────────────────────────────────────────────────

class BankDetails(BaseModel):
    bank_name:  Optional[str] = None
    account_no: Optional[str] = None
    ifsc:       Optional[str] = None
    branch:     Optional[str] = None

class AccountCreate(BaseModel):
    pump_id:         str
    group_id:        str
    party_id:        Optional[str] = None
    name:            str
    alias:           Optional[str] = None
    is_bank_account: bool = False
    bank_details:    Optional[BankDetails] = None
    tcs_apply:       bool = False

class AccountResponse(BaseModel):
    id:              str
    pump_id:         str
    group_id:        str
    party_id:        Optional[str] = None
    name:            str
    alias:           Optional[str] = None
    is_bank_account: bool
    bank_details:    Optional[Dict[str, Any]] = None
    tcs_apply:       bool
    current_balance: float
    is_active:       bool
    created_at:      datetime
    group_name:      Optional[str] = None   # joined field
    party_name:      Optional[str] = None   # joined field

    class Config:
        from_attributes = True


# ─── Party Schemas ────────────────────────────────────────────────────────────

class PartyCreate(BaseModel):
    pump_id:           str
    alias:             Optional[str] = None
    category:          PartyCategoryEnum = PartyCategoryEnum.other
    address:           Optional[str] = None
    legal_name:        Optional[str] = None
    registration_type: RegistrationTypeEnum = RegistrationTypeEnum.unregistered
    pan:               Optional[str] = None
    gst_number:        Optional[str] = None
    primary_contact:   Optional[str] = None

class PartyResponse(BaseModel):
    id:                str
    pump_id:           str
    alias:             Optional[str] = None
    category:          str
    address:           Optional[str] = None
    legal_name:        Optional[str] = None
    registration_type: str
    pan:               Optional[str] = None
    gst_number:        Optional[str] = None
    primary_contact:   Optional[str] = None
    linked_account_id: Optional[str] = None   # joined field
    is_active:         bool
    created_at:        datetime

    class Config:
        from_attributes = True


# ─── Voucher Schemas ──────────────────────────────────────────────────────────

class VoucherEntryCreate(BaseModel):
    account_id:    str
    debit_amount:  float = 0.0
    credit_amount: float = 0.0
    entry_order:   int   = 0

class VoucherEntryResponse(BaseModel):
    id:            str
    account_id:    str
    account_name:  Optional[str] = None   # joined
    debit_amount:  float
    credit_amount: float
    entry_order:   int

    class Config:
        from_attributes = True

class QuickVoucherCreate(BaseModel):
    """For Payment / Receipt / Contra - simple 2-account transfer"""
    pump_id:        str
    voucher_type:   VoucherTypeEnum
    from_account_id: str    # debit side
    to_account_id:   str    # credit side
    amount:         float
    narration:      Optional[str] = None
    voucher_date:   Optional[datetime] = None

class JournalVoucherCreate(BaseModel):
    """For Journal - multi-line entries, user supplies debit/credit manually"""
    pump_id:      str
    narration:    Optional[str] = None
    voucher_date: Optional[datetime] = None
    entries:      List[VoucherEntryCreate]   # Min 2; debits must == credits

class VoucherResponse(BaseModel):
    id:           str
    pump_id:      str
    voucher_type: str
    origin:       str
    narration:    Optional[str] = None
    voucher_date: datetime
    is_posted:    bool
    entries:      List[VoucherEntryResponse] = []
    created_at:   datetime

    class Config:
        from_attributes = True


# ─── Balance Sheet Schemas ────────────────────────────────────────────────────

class BalanceSheetAccount(BaseModel):
    account_id:      str
    account_name:    str
    alias:           Optional[str] = None
    current_balance: float

class BalanceSheetGroup(BaseModel):
    group_id:            str
    group_name:          str
    nature:              str
    affects_gross_profit: bool
    total:               float
    accounts:            List[BalanceSheetAccount]

class BalanceSheetResponse(BaseModel):
    pump_id:      str
    generated_at: datetime
    income:       List[BalanceSheetGroup]
    expenditure:  List[BalanceSheetGroup]
    assets:       List[BalanceSheetGroup]
    liabilities:  List[BalanceSheetGroup]
    gross_profit: float
    total_equity: float