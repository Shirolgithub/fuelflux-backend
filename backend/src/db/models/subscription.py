# src/db/models/subscription.py
# Subscription System — Plans + Pump Subscriptions + Payments

from beanie import Document, PydanticObjectId
from pydantic import Field
from typing import Optional
from datetime import datetime


# ═══════════════════════════════════════════════════════════════
#  MODEL 1 — SubscriptionPlan
# ═══════════════════════════════════════════════════════════════
class SubscriptionPlan(Document):
    name: str                                         # Starter, Pro, Enterprise
    description: Optional[str] = None
    price_monthly: float = 0.0
    price_annual: Optional[float] = None
    is_active: bool = True
    is_default: bool = False

    # Feature flags
    feature_sales: bool = True
    feature_inventory: bool = False
    feature_crm: bool = False
    feature_udhaar: bool = False
    feature_analytics: bool = False
    feature_accounting: bool = False
    feature_logistic: bool = False
    feature_multi_pump: bool = False
    feature_api_access: bool = False
    feature_priority_support: bool = False

    # Usage limits (0 = unlimited)
    max_staff: int = 3
    max_pumps: int = 1
    max_tanks: int = 2
    report_history_days: int = 30
    default_trial_days: int = 0

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[PydanticObjectId] = None    # ref to User._id

    class Settings:
        name = "subscription_plans"


# ═══════════════════════════════════════════════════════════════
#  MODEL 2 — PumpSubscription
# ═══════════════════════════════════════════════════════════════
class PumpSubscription(Document):
    pump_id: PydanticObjectId           # ref to Pump._id
    plan_id: PydanticObjectId           # ref to SubscriptionPlan._id
    owner_id: PydanticObjectId          # ref to User._id
    billing_cycle: str = "monthly"      # monthly | annual
    status: str = "trial"               # trial → active → expired | cancelled
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    trial_end: Optional[datetime] = None
    trial_days_granted: int = 0
    admin_note: Optional[str] = None
    activated_by: Optional[PydanticObjectId] = None  # ref to User._id
    activated_at: Optional[datetime] = None
    # Razorpay
    razorpay_subscription_id: Optional[str] = None
    razorpay_plan_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "pump_subscriptions"
        indexes = [
            [("pump_id", 1)],
            [("owner_id", 1)],
        ]


# ═══════════════════════════════════════════════════════════════
#  MODEL 3 — SubscriptionPayment
# ═══════════════════════════════════════════════════════════════
class SubscriptionPayment(Document):
    pump_subscription_id: PydanticObjectId  # ref to PumpSubscription._id
    pump_id: PydanticObjectId           # ref to Pump._id
    owner_id: PydanticObjectId          # ref to User._id
    plan_id: PydanticObjectId           # ref to SubscriptionPlan._id
    amount: float
    currency: str = "INR"
    billing_cycle: str = "monthly"
    status: str = "pending"             # pending | paid | failed | refunded
    # Razorpay fields
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    razorpay_signature: Optional[str] = None
    # Manual payment
    is_manual: bool = False
    manual_note: Optional[str] = None
    verified_by: Optional[PydanticObjectId] = None   # ref to User._id
    verified_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "subscription_payments"
        indexes = [
            [("pump_id", 1)],
            [("owner_id", 1)],
        ]