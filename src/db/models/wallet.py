"""
Wallet-related DB models for the station owner console.
Tracks merchant virtual wallet, POS device registry, and payout settlements.
"""
from beanie import Document, PydanticObjectId
from pydantic import Field, BaseModel
from typing import Optional, List
from datetime import datetime


class BankAccount(BaseModel):
    """Embedded: a linked bank account for payouts."""
    account_holder: str
    account_number: str          # stored as plain string (mask on frontend)
    ifsc: str
    bank_name: str
    account_type: str = "current"   # current | savings
    is_primary: bool = False
    added_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class UpiId(BaseModel):
    """Embedded: a linked UPI VPA for merchant payments."""
    upi_vpa: str            # e.g. petrolstation@okaxis
    label: str              # e.g. "GPay", "PhonePe"
    is_primary: bool = False
    added_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class PumpWallet(Document):
    pump_id: PydanticObjectId
    balance: float = 0.0              # virtual topup balance
    cashless_mtd: float = 0.0         # cached MTD cashless (updated from SaleLog)
    gateway_settled: float = 0.0      # settled by payment gateway, pending payout
    bank_accounts: List[BankAccount] = []
    upi_ids: List[UpiId] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "pump_wallets"
        indexes = [
            [("pump_id", 1)]
        ]


class POSDevice(Document):
    pump_id: PydanticObjectId
    terminal_id: str                   # POS-101
    model: str                         # Pax A920, PineLabs Pl-40
    serial_number: str
    status: str = "online"             # online | offline
    battery_level: int = 100
    assigned_attendant_name: str = "Unassigned"
    mtd_volume_inr: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "pos_devices"
        indexes = [
            [("pump_id", 1)]
        ]


class MerchantPayout(Document):
    pump_id: PydanticObjectId
    payout_id: str                     # PAY-260720-ABC12
    amount: float
    recipient_bank: str
    status: str = "processing"         # processing | settled | failed
    payout_date: datetime = Field(default_factory=datetime.utcnow)
    reconciled: bool = True
    bank_statement_amount: Optional[float] = None
    discrepancy_reason: Optional[str] = None

    class Settings:
        name = "merchant_payouts"
        indexes = [
            [("pump_id", 1)]
        ]
