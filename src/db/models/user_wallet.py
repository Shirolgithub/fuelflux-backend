"""
User Wallet and Ledger Transaction Models
For production-grade double-entry ledger wallet with Razorpay integration.
"""
from beanie import Document, PydanticObjectId, Indexed
from pydantic import Field, BaseModel, BeforeValidator
from typing import Optional, Dict, Annotated, Any
from datetime import datetime, date
from decimal import Decimal
from bson.decimal128 import Decimal128


def validate_decimal(v: Any) -> Decimal:
    """
    Custom validator to convert BSON Decimal128 to Python Decimal cleanly during validation.
    """
    if isinstance(v, Decimal128):
        return v.to_decimal()
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal("0.0")
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0.0")


DecimalField = Annotated[Decimal, BeforeValidator(validate_decimal)]


class UserWallet(Document):
    user_id: Indexed(PydanticObjectId, unique=True)
    balance: DecimalField = Field(default=Decimal("0.0"))                  # cached balance
    currency: str = "INR"
    status: str = "active"                                           # active | frozen
    daily_withdrawal_used: DecimalField = Field(default=Decimal("0.0"))    # tracks withdrawal limit
    daily_withdrawal_date: Optional[date] = None                     # date of limit tracking
    fund_account_id: Optional[str] = None                            # RazorpayX fund account ID
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "user_wallets"
        indexes = [
            [("user_id", 1)]
        ]


class WalletTransaction(Document):
    wallet_id: Indexed(PydanticObjectId)
    type: str                                                        # "credit" | "debit"
    amount: DecimalField                                             # positive decimal
    balance_after: DecimalField                                      # balance snapshot
    category: str                                                    # add_money | spend | withdrawal | p2p_sent | p2p_received | refund
    reference_id: Optional[Indexed(str)] = None                      # razorpay payment_id / payout_id / internal txn ID
    status: str = "pending"                                          # pending | success | failed | reversed
    idempotency_key: Indexed(str, unique=True)                       # unique constraint to prevent duplicate processing
    metadata: Optional[Dict] = None                                  # custom extra metadata JSON
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "wallet_transactions"
        indexes = [
            [("wallet_id", 1)],
            [("reference_id", 1)],
            [("idempotency_key", 1)]
        ]


class PaymentOrder(Document):
    wallet_id: PydanticObjectId
    razorpay_order_id: Indexed(str, unique=True)
    amount: int                                                      # amount in paise (e.g. 50000 for Rs.500)
    amount_inr: DecimalField                                         # amount in INR for display
    status: str = "created"                                          # created | paid | failed
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "payment_orders"
        indexes = [
            [("razorpay_order_id", 1)],
            [("wallet_id", 1)]
        ]


class PayoutRequest(Document):
    wallet_id: PydanticObjectId
    amount: DecimalField
    bank_account_holder: str
    bank_account_number: str                                         # masked in responses
    bank_ifsc: str
    fund_account_id: Optional[str] = None                            # RazorpayX fund account ID
    razorpay_payout_id: Optional[Indexed(str)] = None
    status: str = "queued"                                           # queued | processing | processed | reversed | failed
    idempotency_key: Indexed(str, unique=True)
    failure_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None

    class Settings:
        name = "payout_requests"
        indexes = [
            [("wallet_id", 1)],
            [("razorpay_payout_id", 1)],
            [("idempotency_key", 1)]
        ]


class P2PTransfer(Document):
    from_wallet_id: PydanticObjectId
    to_wallet_id: PydanticObjectId
    amount: DecimalField
    sender_txn_id: PydanticObjectId                                  # ref -> WalletTransaction
    receiver_txn_id: PydanticObjectId                                # ref -> WalletTransaction
    note: Optional[str] = None
    status: str = "completed"                                        # completed | failed
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "p2p_transfers"
        indexes = [
            [("from_wallet_id", 1)],
            [("to_wallet_id", 1)]
        ]


class WalletAuditLog(Document):
    wallet_id: PydanticObjectId
    user_id: PydanticObjectId
    action: str                                                      # e.g. "ADD_MONEY", "WITHDRAWAL", "P2P_SENT", "P2P_RECEIVED"
    amount: DecimalField
    balance_before: DecimalField
    balance_after: DecimalField
    performed_by: str                                                # user email / "system" / "webhook"
    ip_address: Optional[str] = None
    metadata_json: Optional[str] = None                              # JSON string of extra context
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "wallet_audit_logs"
        indexes = [
            [("wallet_id", 1)],
            [("timestamp", -1)]
        ]
