from beanie import Document, Indexed
from pydantic import Field, EmailStr
from typing import List, Optional, Annotated
from datetime import datetime
import enum


class User(Document):
    email: Indexed(str, unique=True)
    phone: Optional[Indexed(str, unique=True)] = None
    full_name: Optional[str] = None
    hashed_password: str
    roles: List[str] = Field(default_factory=lambda: ["pump_owner"])
    is_active: bool = True
    # Logistic partner profile fields
    company_name: Optional[str] = None
    gstin: Optional[str] = None
    billing_address: Optional[str] = None
    fleet_size: Optional[int] = None          # how many vehicles in fleet
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Verification (for logistic partners) ─────────────────────────────────
    # "pending" → registered, waiting admin approval
    # "verified" → admin approved, full portal access
    # "rejected" → admin rejected, cannot use portal
    # None / missing → pump_owner / admin users (no KYC required)
    verification_status: Optional[str] = None   # "pending" | "verified" | "rejected"
    verification_notes: Optional[str] = None    # reason shown to user on rejection
    verified_at: Optional[datetime] = None
    verified_by: Optional[str] = None           # admin email who approved/rejected

    # ── KYC Documents (for logistic partners) ─────────────────────────────────
    # Each entry: {"doc_type": str, "file_url": str, "original_name": str, "uploaded_at": str}
    # doc_type values: "gstin_certificate", "pan_card", "company_registration", "transport_license"
    kyc_documents: Optional[List[dict]] = None

    # ── Bank Account (for wallet / payment receipts) ──────────────────────────
    bank_account_holder: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc_code: Optional[str] = None
    bank_name: Optional[str] = None
    bank_upi_id: Optional[str] = None
    bank_verified: bool = False

    class Settings:
        name = "users"
        indexes = [
            [("email", 1)],
            [("phone", 1)],
        ]

    class Config:
        populate_by_name = True


class BlacklistedToken(Document):
    token: Indexed(str, unique=True)
    blacklisted_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime

    class Settings:
        name = "blacklisted_tokens"


class OTPCode(Document):
    identifier: Indexed(str)   # email or phone
    code: str                               # 6-digit OTP
    otp_type: str = "email"                 # "email" or "sms"
    purpose: str = "verification"           # "verification" or "reset"
    is_used: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime

    class Settings:
        name = "otp_codes"