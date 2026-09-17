from pydantic import BaseModel, EmailStr
from typing import List, Optional


# ── Token Schemas ──────────────────────────────────────────────────────────────

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """
    Decoded JWT payload.
    pump_owner: email set, attendant_id = None
    attendant:  email = None, attendant_id + pump_id set
    """
    email: Optional[str] = None
    attendant_id: Optional[str] = None
    pump_id: Optional[str] = None
    roles: List[str] = []
    scope: Optional[str] = None


# ── Pump Owner Auth (EXISTING — NO CHANGES) ───────────────────────────────────

class UserLogin(BaseModel):
    email: Optional[str] = None
    emailOrPhone: Optional[str] = None
    password: str
    rememberMe: Optional[bool] = False


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    phone: Optional[str] = None
    full_name: Optional[str] = None
    name: Optional[str] = None
    roles: Optional[List[str]] = None
    role: Optional[str] = None
    company_name: Optional[str] = None
    gstin: Optional[str] = None
    fleet_size: Optional[int] = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class SelectRoleRequest(BaseModel):
    role: str


class RefreshTokenRequest(BaseModel):
    refreshToken: str


class SendOTPRequest(BaseModel):
    identifier: str
    purpose: Optional[str] = "verification"


class VerifyOTPRequest(BaseModel):
    identifier: str
    code: str


# ── Google Auth (NEW) ─────────────────────────────────────────────────────────

class GoogleLoginRequest(BaseModel):
    id_token: str


class GoogleRegisterRequest(BaseModel):
    id_token: str
    phone: Optional[str] = None
    roles: List[str]
    company_name: Optional[str] = None
    gstin: Optional[str] = None
    fleet_size: Optional[int] = None


# ── Employee Auth (NEW) ────────────────────────────────────────────────────────

class EmployeeLogin(BaseModel):
    """Employee login — employee_id + password only."""
    employee_id: str
    password: str


class EmployeeChangePassword(BaseModel):
    current_password: str
    new_password: str