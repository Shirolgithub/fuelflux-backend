"""
Accounting models: AccountGroup → Account ← Party
                   Voucher → VoucherEntry (double-entry ledger)
"""
import enum
from datetime import datetime
from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional, Any, Dict


# ─── Enums ────────────────────────────────────────────────────────────────────

class AccountNature(str, enum.Enum):
    income = "income"
    expenditure = "expenditure"
    asset = "asset"
    liability = "liability"

class PartyCategory(str, enum.Enum):
    customer = "customer"
    supplier = "supplier"
    other = "other"

class RegistrationType(str, enum.Enum):
    registered = "registered"
    unregistered = "unregistered"
    composition = "composition"
    consumer = "consumer"

class VoucherType(str, enum.Enum):
    payment = "payment"
    receipt = "receipt"
    contra = "contra"
    journal = "journal"

class VoucherOrigin(str, enum.Enum):
    manual = "manual"
    sale = "sale"
    purchase = "purchase"
    adjustment = "adjustment"


# ─── AccountGroup ─────────────────────────────────────────────────────────────

class AccountGroup(Document):
    """Top-level financial category."""
    pump_id: PydanticObjectId           # ref to Pump._id
    name: str
    category: Optional[str] = None     # Expense, Income
    nature: AccountNature
    affects_gross_profit: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True

    class Settings:
        name = "account_groups"
        indexes = [
            [("pump_id", 1)],
        ]


# ─── Account ──────────────────────────────────────────────────────────────────

class Account(Document):
    """Money container. Must belong to an AccountGroup."""
    pump_id: PydanticObjectId           # ref to Pump._id
    group_id: PydanticObjectId          # ref to AccountGroup._id
    party_id: Optional[PydanticObjectId] = None  # ref to Party._id
    name: str
    alias: Optional[str] = None
    is_bank_account: bool = False
    bank_details: Optional[Dict[str, Any]] = None  # {bank_name, account_no, ifsc, branch}
    tcs_apply: bool = False
    current_balance: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True

    class Settings:
        name = "accounts"
        indexes = [
            [("pump_id", 1)],
            [("group_id", 1)],
        ]


# ─── Party ────────────────────────────────────────────────────────────────────

class Party(Document):
    """External business entity - supplier or credit customer."""
    pump_id: PydanticObjectId           # ref to Pump._id
    alias: Optional[str] = None
    category: PartyCategory = PartyCategory.other
    address: Optional[str] = None
    legal_name: Optional[str] = None
    registration_type: RegistrationType = RegistrationType.unregistered
    pan: Optional[str] = None
    gst_number: Optional[str] = None
    primary_contact: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True

    class Settings:
        name = "parties"
        indexes = [
            [("pump_id", 1)],
        ]


# ─── Voucher ──────────────────────────────────────────────────────────────────

class Voucher(Document):
    """Every money movement recorded as a Voucher."""
    pump_id: PydanticObjectId           # ref to Pump._id
    voucher_type: VoucherType
    origin: VoucherOrigin = VoucherOrigin.manual
    reference_id: Optional[PydanticObjectId] = None  # sale_id / purchase_id if auto
    narration: Optional[str] = None
    voucher_date: datetime = Field(default_factory=datetime.utcnow)
    is_posted: bool = True
    posted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "vouchers"
        indexes = [
            [("pump_id", 1), ("voucher_date", -1)],
            [("pump_id", 1), ("voucher_type", 1)],
        ]


# ─── VoucherEntry (Ledger Line) ───────────────────────────────────────────────

class VoucherEntry(Document):
    """Double-entry ledger line. Every voucher has >= 2 entries."""
    voucher_id: PydanticObjectId        # ref to Voucher._id
    account_id: PydanticObjectId        # ref to Account._id
    debit_amount: float = 0.0
    credit_amount: float = 0.0
    entry_order: int = 0

    class Settings:
        name = "voucher_entries"
        indexes = [
            [("voucher_id", 1)],
            [("account_id", 1)],
        ]