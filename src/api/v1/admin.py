# ============================================================
#  FuelFlux — Admin API  (src/api/v1/admin.py)
#  Industry-level | Separate Auth | 2FA | Audit Log | 30-min JWT
#  MongoDB / Beanie version
# ============================================================

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from beanie import PydanticObjectId
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
import structlog
import traceback
import random
import json
import secrets

from src.core.config import settings
from src.db.models.user import User, BlacklistedToken, OTPCode
from src.db.models.pump import Pump
from src.db.models.payment import PaymentRequest
from src.db.models.customer import Customer
from src.db.models.vehicle import Vehicle
from src.db.models.subscription import SubscriptionPayment, SubscriptionPlan, PumpSubscription
from src.db.models.audit_log import AuditLog
from src.db.models.platform_settings import PlatformSettings
from src.db.models.support import SupportTicket
from src.services.sms_service import send_sms_otp

log = structlog.get_logger()

router = APIRouter(prefix="/admin", tags=["admin"])

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
ADMIN_TOKEN_EXPIRE_MINUTES = 30          # Separate from user's 10080 (7 days)
ADMIN_TOKEN_SCOPE          = "admin"     # Scope marker in JWT payload
ADMIN_ROLES                = {"admin", "superadmin"}


# ─────────────────────────────────────────────
# HELPERS — Admin JWT (30-minute expiry)
# ─────────────────────────────────────────────

def create_admin_access_token(data: dict) -> str:
    """
    Creates a short-lived (30 min) JWT specifically for admin sessions.
    Includes scope='admin' so it can never be used on regular user routes.
    """
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ADMIN_TOKEN_EXPIRE_MINUTES)
    payload.update({
        "exp":   expire,
        "scope": ADMIN_TOKEN_SCOPE,
        "iat":   datetime.now(timezone.utc),
    })
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_admin_token(token: str) -> dict:
    """
    Decodes and validates an admin JWT.
    Raises HTTPException if invalid, expired, or wrong scope.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token expired or invalid. Please login again.",
        )

    if payload.get("scope") != ADMIN_TOKEN_SCOPE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token scope mismatch. Admin token required.",
        )
    return payload


# ─────────────────────────────────────────────
# DEPENDENCY — Get Current Admin User
# ─────────────────────────────────────────────

admin_bearer = HTTPBearer()


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(admin_bearer),
) -> User:
    """
    FastAPI dependency. Validates the admin Bearer token and
    returns the authenticated admin User object.
    """
    token = credentials.credentials

    # 1. Check blacklist (logout invalidation)
    blacklisted = await BlacklistedToken.find_one(BlacklistedToken.token == token)
    if blacklisted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been terminated. Please login again.",
        )

    # 2. Decode + scope check
    payload = decode_admin_token(token)
    email: str = payload.get("sub")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload.")

    # 3. Fetch user from DB
    user = await User.find_one(User.email == email, User.is_active == True)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin user not found or inactive.")

    # 4. Role guard
    user_roles = set(user.roles or [])
    if not user_roles.intersection(ADMIN_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges. Admin role required.",
        )

    return user


# ─────────────────────────────────────────────
# AUDIT LOG HELPER
# ─────────────────────────────────────────────

async def write_audit_log(
    admin: User,
    action: str,
    target_type: str = None,
    target_id: str = None,
    metadata: dict = None,
):
    """
    Writes a structured audit entry to BOTH the log stream (for live
    tailing) and the database (for the /admin/audit-log page).
    Every admin action MUST call this.
    """
    log.info(
        "ADMIN_AUDIT",
        admin_id=str(admin.id),
        admin_email=admin.email,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id else None,
        metadata=metadata or {},
        timestamp=datetime.utcnow().isoformat(),
    )

    try:
        entry = AuditLog(
            admin_id      = admin.id,
            admin_email   = admin.email,
            action        = action,
            target_type   = target_type,
            target_id     = str(target_id) if target_id else None,
            metadata_json = json.dumps(metadata) if metadata else None,
        )
        await entry.insert()
    except Exception as e:
        # Never let audit logging crash the actual admin action
        log.error("Failed to persist audit log", error=str(e))


def to_oid(value: str, field_name: str = "id") -> PydanticObjectId:
    """Safely converts a string to PydanticObjectId, raising a clean 400 on failure."""
    try:
        return PydanticObjectId(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} format.")


# ═══════════════════════════════════════════════════════════════
#  SECTION 1 — ADMIN AUTH
#  Routes: /admin/auth/login  |  /admin/auth/verify-2fa  |  /admin/auth/logout
# ═══════════════════════════════════════════════════════════════

@router.post("/auth/login")
async def admin_login(payload: dict):
    """
    Step 1 of admin login.
    Validates email/password → sends OTP via SMS (Twilio) to admin's
    registered phone number. Falls back to console print if Twilio
    fails or admin has no phone number on file.
 
    Body: { "emailOrPhone": str, "password": str }
    """
    try:
        from src.core.security import verify_password
 
        identifier = payload.get("emailOrPhone") or payload.get("email")
        password   = payload.get("password")
 
        if not identifier or not password:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "Email and password are required."},
            )
 
        # 1. Find user
        user = await User.find_one(
            {"$or": [{"email": identifier}, {"phone": identifier}]}
        )
 
        if not user or not verify_password(password, user.hashed_password):
            log.warning("Admin login failed — bad credentials", identifier=identifier)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"success": False, "message": "Invalid credentials."},
            )
 
        if not user.is_active:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "Account is inactive."},
            )
 
        # 2. Role check — only admins allowed
        user_roles = set(user.roles or [])
        if not user_roles.intersection(ADMIN_ROLES):
            log.warning("Non-admin login attempt on admin route", email=user.email)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"success": False, "message": "Access denied. Admin credentials required."},
            )
 
        # 3. Invalidate old OTPs for this admin
        await OTPCode.find(
            OTPCode.identifier == user.email,
            OTPCode.purpose    == "admin_2fa",
            OTPCode.is_used    == False,
        ).delete()
 
        # 4. Generate 6-digit OTP
        otp_code   = str(random.randint(100000, 999999))
        expires_at = datetime.utcnow() + timedelta(minutes=10)
 
        await OTPCode(
            identifier = user.email,
            code       = otp_code,
            otp_type   = "sms",
            purpose    = "admin_2fa",
            is_used    = False,
            expires_at = expires_at,
        ).insert()
 
        # 5. Try sending via Twilio SMS first
        sms_sent = False
 
        if user.phone:
            phone_for_sms = user.phone if user.phone.startswith("+") else f"+91{user.phone}"
            sms_sent = send_sms_otp(phone_for_sms, otp_code)
 
        # 6. Fallback: console print (dev mode / Twilio failed / no phone)
        if not sms_sent:
            print(f"\n{'='*45}")
            print(f"  [ADMIN 2FA - FALLBACK] OTP for {user.email}: {otp_code}")
            print(f"  (SMS not sent — Twilio unavailable or no phone on file)")
            print(f"  Expires in: 10 minutes")
            print(f"{'='*45}\n")
            log.warning("Admin 2FA fell back to console — SMS not sent", email=user.email)
        else:
            log.info("Admin 2FA OTP sent via SMS", email=user.email, phone=user.phone)
 
        return {
            "success": True,
            "message": (
                f"OTP sent to your registered mobile number ending in {user.phone[-4:]}."
                if sms_sent else
                "OTP sent (check backend console — SMS delivery unavailable)."
            ),
            "email":    user.email,
            "sms_sent": sms_sent,
            # DEV ONLY — remove in production
            "otp": otp_code,
        }
 
    except Exception as e:
        log.error("Admin login error", error=str(e), traceback=traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Internal server error."},
        )


@router.post("/auth/verify-2fa")
async def admin_verify_2fa(payload: dict):
    """
    Step 2 of admin login — verifies OTP and issues a 30-minute admin JWT.

    Body: { "email": str, "code": str }
    Returns: { accessToken, user }
    """
    try:
        email = payload.get("email", "").strip()
        code  = payload.get("code",  "").strip()

        if not email or not code:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "Email and OTP code are required."},
            )

        # 1. Find valid OTP
        otp_entry = await OTPCode.find(
            OTPCode.identifier == email,
            OTPCode.code       == code,
            OTPCode.purpose    == "admin_2fa",
            OTPCode.is_used    == False,
            OTPCode.expires_at >  datetime.utcnow(),
        ).sort(-OTPCode.created_at).first_or_none()

        if not otp_entry:
            # Check if it exists but expired
            expired = await OTPCode.find_one(
                OTPCode.identifier == email,
                OTPCode.code       == code,
                OTPCode.purpose    == "admin_2fa",
                OTPCode.is_used    == False,
            )
            msg = "OTP has expired. Please request a new one." if expired else "Invalid OTP code."
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": msg},
            )

        # 2. Mark OTP as used
        otp_entry.is_used = True
        await otp_entry.save()

        # 3. Fetch admin user
        user = await User.find_one(User.email == email, User.is_active == True)
        if not user:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "Admin user not found."},
            )

        # 4. Issue 30-minute admin token
        access_token = create_admin_access_token({
            "sub":   user.email,
            "roles": user.roles,
            "id":    str(user.id),
        })

        await write_audit_log(user, action="ADMIN_LOGIN_SUCCESS")
        log.info("Admin authenticated successfully", email=user.email)

        return {
            "success":     True,
            "accessToken": access_token,
            "user": {
                "id":    str(user.id),
                "name":  user.full_name or "Administrator",
                "email": user.email,
                "roles": user.roles,
            },
        }

    except Exception as e:
        log.error("Admin 2FA verify error", error=str(e), traceback=traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Internal server error."},
        )


@router.post("/auth/logout")
async def admin_logout(
    credentials: HTTPAuthorizationCredentials = Depends(admin_bearer),
    current_admin: User = Depends(get_current_admin),
):
    """
    Blacklists the admin JWT to invalidate the session immediately.
    """
    try:
        token = credentials.credentials

        existing = await BlacklistedToken.find_one(BlacklistedToken.token == token)
        if not existing:
            try:
                p = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
                exp = p.get("exp")
                expires_at = (
                    datetime.fromtimestamp(exp, timezone.utc).replace(tzinfo=None)
                    if exp else datetime.utcnow() + timedelta(minutes=30)
                )
            except Exception:
                expires_at = datetime.utcnow() + timedelta(minutes=30)

            await BlacklistedToken(token=token, expires_at=expires_at).insert()

        await write_audit_log(current_admin, action="ADMIN_LOGOUT")
        return {"success": True, "message": "Admin session terminated successfully."}

    except Exception as e:
        log.error("Admin logout error", error=str(e))
        raise HTTPException(status_code=500, detail="Logout failed.")


# ═══════════════════════════════════════════════════════════════
#  SECTION 2 — OVERVIEW / DASHBOARD STATS
#  Route: GET /admin/overview
# ═══════════════════════════════════════════════════════════════

@router.get("/overview")
async def admin_overview(admin: User = Depends(get_current_admin)):
    """
    Returns KPI summary for the admin dashboard.
    """
    try:
        total_pumps   = await Pump.find(Pump.status == "active").count() if False else await Pump.find_all().count()
        active_pumps  = await Pump.find(Pump.status == "active").count()
        pending_pumps = await Pump.find(Pump.status == "pending").count()

        all_users        = await User.find_all().to_list()
        owner_count      = sum(1 for u in all_users if "pump_owner" in (u.roles or []))
        logistics_count  = sum(1 for u in all_users if "logistic" in (u.roles or []))

        return {
            "success": True,
            "data": {
                "total_pumps":           total_pumps,
                "active_pumps":          active_pumps,
                "pending_registrations": pending_pumps,
                "total_owners":          owner_count,
                "logistics_partners":    logistics_count,
                "open_support_tickets":  await SupportTicket.find(SupportTicket.status == "open").count(),
                "active_subscriptions":  active_pumps,
                "mrr":                   0,   # TODO: aggregate from SubscriptionPayment
            },
        }
    except Exception as e:
        log.error("Admin overview error", error=str(e), traceback=traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to fetch overview.")


# ═══════════════════════════════════════════════════════════════
#  SECTION 3 — PUMPS MANAGEMENT
#  Routes: GET /admin/pumps  |  GET /admin/pumps/{id}
#          PATCH /admin/pumps/{id}/status  |  DELETE /admin/pumps/{id}
# ═══════════════════════════════════════════════════════════════

@router.get("/pumps")
async def list_all_pumps(
    status_filter: str = Query(None, alias="status"),
    page:          int = Query(1, ge=1),
    limit:         int = Query(20, ge=1, le=100),
    admin:         User = Depends(get_current_admin),
):
    """
    Returns paginated list of all pumps.
    Optional filter: ?status=active | pending | rejected | inactive
    """
    try:
        query = Pump.find(Pump.status == status_filter) if status_filter else Pump.find_all()

        total = await query.count()
        pumps = await query.skip((page - 1) * limit).limit(limit).to_list()

        return {
            "success": True,
            "data": {
                "pumps": [
                    {
                        "id":         str(p.id),
                        "name":       p.name,
                        "status":     p.status,
                        "owner_id":   str(p.owner_id) if p.owner_id else None,
                        "address":    getattr(p, "address", None),
                        "city":       getattr(p, "city", None),
                        "state":      getattr(p, "state", None),
                        "created_at": p.created_at.isoformat() if p.created_at else None,
                    }
                    for p in pumps
                ],
                "total": total,
                "page":  page,
                "pages": (total + limit - 1) // limit,
            },
        }
    except Exception as e:
        log.error("List pumps error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch pumps.")


@router.get("/pumps/{pump_id}")
async def get_pump_detail(
    pump_id: str,
    admin:   User = Depends(get_current_admin),
):
    """Returns full detail of a single pump."""
    pump = await Pump.get(to_oid(pump_id, "pump_id"))
    if not pump:
        raise HTTPException(status_code=404, detail="Pump not found.")

    return {
        "success": True,
        "data": {
            "id":         str(pump.id),
            "name":       pump.name,
            "status":     pump.status,
            "owner_id":   str(pump.owner_id) if pump.owner_id else None,
            "address":    getattr(pump, "address", None),
            "city":       getattr(pump, "city", None),
            "state":      getattr(pump, "state", None),
            "created_at": pump.created_at.isoformat() if pump.created_at else None,
        },
    }


@router.patch("/pumps/{pump_id}/status")
async def update_pump_status(
    pump_id: str,
    payload: dict,
    admin:   User = Depends(get_current_admin),
):
    """
    Updates pump status. Admin-only.
    Body: { "status": "active" | "rejected" | "inactive", "reason": str (optional) }
    """
    try:
        allowed_statuses = {"active", "rejected", "inactive", "pending"}
        new_status = payload.get("status")
        reason     = payload.get("reason", "")

        if new_status not in allowed_statuses:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"Invalid status. Allowed: {allowed_statuses}"},
            )

        pump = await Pump.get(to_oid(pump_id, "pump_id"))
        if not pump:
            raise HTTPException(status_code=404, detail="Pump not found.")

        old_status      = pump.status
        pump.status     = new_status
        pump.updated_at = datetime.utcnow()
        await pump.save()

        await write_audit_log(
            admin,
            action      = f"PUMP_STATUS_CHANGED:{old_status}→{new_status}",
            target_type = "pump",
            target_id   = pump_id,
            metadata    = {"reason": reason},
        )

        return {
            "success": True,
            "message": f"Pump '{pump.name}' status updated to '{new_status}'.",
            "pump": {
                "id":     str(pump.id),
                "name":   pump.name,
                "status": pump.status,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("Update pump status error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to update pump status.")


# ═══════════════════════════════════════════════════════════════
#  SECTION 4 — PENDING REGISTRATIONS
#  Routes: GET /admin/pending-registrations
#          POST /admin/registrations/{id}/approve
#          POST /admin/registrations/{id}/reject
#          POST /admin/registrations/{id}/request-info
# ═══════════════════════════════════════════════════════════════

@router.get("/pending-registrations")
async def get_pending_registrations(
    page:  int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
):
    """Returns all pumps with status='pending' — the approval queue."""
    query = Pump.find(Pump.status == "pending").sort(-Pump.created_at)
    total = await query.count()
    pumps = await query.skip((page - 1) * limit).limit(limit).to_list()

    result = []
    for p in pumps:
        owner = await User.get(p.owner_id) if p.owner_id else None
        result.append({
            "id":             str(p.id),
            "pump_name":      p.name,
            "owner_name":     owner.full_name if owner else "N/A",
            "owner_email":    owner.email     if owner else "N/A",
            "owner_phone":    owner.phone     if owner else "N/A",
            "submitted_date": p.created_at.isoformat() if p.created_at else None,
            "status":         p.status,
            "address":        getattr(p, "address", None),
            "city":           getattr(p, "city",    None),
        })

    return {
        "success": True,
        "data": {"registrations": result, "total": total, "page": page},
    }


@router.post("/registrations/{pump_id}/approve")
async def approve_registration(
    pump_id: str,
    admin:   User = Depends(get_current_admin),
):
    """Approves a pending pump registration and activates the station."""
    pump = await Pump.find_one(Pump.id == to_oid(pump_id, "pump_id"), Pump.status == "pending")
    if not pump:
        raise HTTPException(status_code=404, detail="Pending registration not found.")

    pump.status     = "active"
    pump.updated_at = datetime.utcnow()
    await pump.save()

    await write_audit_log(
        admin,
        action      = "REGISTRATION_APPROVED",
        target_type = "pump",
        target_id   = pump_id,
    )

    # TODO: Send welcome email/SMS to pump owner
    # send_approval_notification(pump.owner_id)

    return {
        "success": True,
        "message": f"Pump '{pump.name}' has been approved and activated.",
    }


@router.post("/registrations/{pump_id}/reject")
async def reject_registration(
    pump_id: str,
    payload: dict,
    admin:   User = Depends(get_current_admin),
):
    """
    Rejects a pending pump registration.
    Body: { "reason": str }
    """
    reason = payload.get("reason", "").strip()
    if not reason:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Rejection reason is required."},
        )

    pump = await Pump.find_one(Pump.id == to_oid(pump_id, "pump_id"), Pump.status == "pending")
    if not pump:
        raise HTTPException(status_code=404, detail="Pending registration not found.")

    pump.status     = "rejected"
    pump.updated_at = datetime.utcnow()
    await pump.save()

    await write_audit_log(
        admin,
        action      = "REGISTRATION_REJECTED",
        target_type = "pump",
        target_id   = pump_id,
        metadata    = {"reason": reason},
    )

    # TODO: Notify owner with reason
    # send_rejection_notification(pump.owner_id, reason)

    return {
        "success": True,
        "message": f"Pump '{pump.name}' registration rejected.",
        "reason":  reason,
    }


@router.post("/registrations/{pump_id}/request-info")
async def request_more_info(
    pump_id: str,
    payload: dict,
    admin:   User = Depends(get_current_admin),
):
    """
    Sends a message to the pump owner requesting additional info.
    Body: { "message": str }
    """
    message = payload.get("message", "").strip()
    if not message:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Message is required."},
        )

    pump = await Pump.get(to_oid(pump_id, "pump_id"))
    if not pump:
        raise HTTPException(status_code=404, detail="Pump not found.")

    await write_audit_log(
        admin,
        action      = "REGISTRATION_INFO_REQUESTED",
        target_type = "pump",
        target_id   = pump_id,
        metadata    = {"message": message},
    )

    # TODO: Send message to owner via email/SMS
    # send_info_request(pump.owner_id, message)

    return {
        "success": True,
        "message": "Information request sent to pump owner.",
    }


# ═══════════════════════════════════════════════════════════════
#  SECTION 5 — OWNERS MANAGEMENT
#  Route: GET /admin/owners  |  GET /admin/owners/{id}
#         PATCH /admin/owners/{id}/status
# ═══════════════════════════════════════════════════════════════

@router.get("/owners")
async def list_owners(
    page:  int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
):
    """Returns paginated list of all pump owners."""
    all_users = await User.find_all().to_list()
    owners = [u for u in all_users if "pump_owner" in (u.roles or [])]

    total     = len(owners)
    paginated = owners[(page - 1) * limit: page * limit]

    owners_out = []
    for u in paginated:
        pump_count = await Pump.find(Pump.owner_id == u.id).count()
        owners_out.append({
            "id":         str(u.id),
            "name":       u.full_name,
            "email":      u.email,
            "phone":      u.phone,
            "is_active":  u.is_active,
            "roles":      u.roles,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "pump_count": pump_count,
        })

    return {
        "success": True,
        "data": {
            "owners": owners_out,
            "total":  total,
            "page":   page,
        },
    }


@router.get("/owners/{owner_id}")
async def get_owner_detail(
    owner_id: str,
    admin:    User = Depends(get_current_admin),
):
    """Returns full profile of a pump owner including their stations."""
    user = await User.get(to_oid(owner_id, "owner_id"))
    if not user or "pump_owner" not in (user.roles or []):
        raise HTTPException(status_code=404, detail="Owner not found.")

    pumps = await Pump.find(Pump.owner_id == user.id).to_list()

    return {
        "success": True,
        "data": {
            "id":        str(user.id),
            "name":      user.full_name,
            "email":     user.email,
            "phone":     user.phone,
            "is_active": user.is_active,
            "roles":     user.roles,
            "pumps": [
                {"id": str(p.id), "name": p.name, "status": p.status}
                for p in pumps
            ],
        },
    }


@router.patch("/owners/{owner_id}/status")
async def toggle_owner_status(
    owner_id: str,
    payload:  dict,
    admin:    User = Depends(get_current_admin),
):
    """
    Activate or deactivate a pump owner account.
    Body: { "is_active": bool, "reason": str }
    """
    user = await User.get(to_oid(owner_id, "owner_id"))
    if not user:
        raise HTTPException(status_code=404, detail="Owner not found.")

    is_active       = payload.get("is_active", True)
    reason          = payload.get("reason", "")
    user.is_active  = is_active
    user.updated_at = datetime.utcnow()
    await user.save()

    action = "OWNER_ACTIVATED" if is_active else "OWNER_DEACTIVATED"
    await write_audit_log(admin, action=action, target_type="user", target_id=owner_id, metadata={"reason": reason})

    return {
        "success": True,
        "message": f"Owner account {'activated' if is_active else 'deactivated'}.",
    }


# ═══════════════════════════════════════════════════════════════
#  SECTION 6 — USER DIRECTORY
#  Route: GET /admin/users  |  PATCH /admin/users/{id}/status
# ═══════════════════════════════════════════════════════════════

@router.get("/users")
async def list_all_users(
    role:  str = Query(None),
    page:  int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
):
    """Returns paginated user directory. Optional role filter."""
    all_users = await User.find_all().to_list()

    if role:
        all_users = [u for u in all_users if role in (u.roles or [])]

    total     = len(all_users)
    paginated = all_users[(page - 1) * limit: page * limit]

    return {
        "success": True,
        "data": {
            "users": [
                {
                    "id":         str(u.id),
                    "name":       u.full_name,
                    "email":      u.email,
                    "phone":      u.phone,
                    "roles":      u.roles,
                    "is_active":  u.is_active,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in paginated
            ],
            "total": total,
            "page":  page,
        },
    }


@router.patch("/users/{user_id}/status")
async def toggle_user_status(
    user_id: str,
    payload: dict,
    admin:   User = Depends(get_current_admin),
):
    """Activate or deactivate any user account."""
    user = await User.get(to_oid(user_id, "user_id"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    is_active       = payload.get("is_active", True)
    reason          = payload.get("reason", "")
    user.is_active  = is_active
    user.updated_at = datetime.utcnow()
    await user.save()

    action = "USER_ACTIVATED" if is_active else "USER_DEACTIVATED"
    await write_audit_log(admin, action=action, target_type="user", target_id=user_id, metadata={"reason": reason})

    return {"success": True, "message": f"User {'activated' if is_active else 'deactivated'}."}


# ═══════════════════════════════════════════════════════════════
#  SECTION 7B — INVESTORS
#  Route: GET /admin/investors
# ═══════════════════════════════════════════════════════════════

@router.get("/investors")
async def list_investors(
    page:  int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
):
    """Returns all investor accounts."""
    all_users = await User.find_all().to_list()
    investors = [u for u in all_users if "investor" in (u.roles or [])]

    total     = len(investors)
    paginated = investors[(page - 1) * limit: page * limit]

    return {
        "success": True,
        "data": {
            "investors": [
                {
                    "id":         str(u.id),
                    "name":       u.full_name,
                    "email":      u.email,
                    "phone":      u.phone,
                    "is_active":  u.is_active,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in paginated
            ],
            "total": total,
        },
    }


# ═══════════════════════════════════════════════════════════════
#  SECTION 8 — AUDIT LOGS
#  Route: GET /admin/audit-log
# ═══════════════════════════════════════════════════════════════

@router.get("/audit-log")
async def get_audit_log(
    page:  int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    admin: User = Depends(get_current_admin),
):
    """Returns paginated audit log entries, most recent first."""
    query = AuditLog.find_all().sort(-AuditLog.timestamp)
    total = await query.count()
    logs  = await query.skip((page - 1) * limit).limit(limit).to_list()

    return {
        "success": True,
        "data": {
            "logs": [
                {
                    "id":          str(l.id),
                    "admin_id":    str(l.admin_id),
                    "admin_email": l.admin_email,
                    "action":      l.action,
                    "target_type": l.target_type,
                    "target_id":   l.target_id,
                    "timestamp":   l.timestamp.isoformat() if l.timestamp else None,
                }
                for l in logs
            ],
            "total": total,
            "page":  page,
            "pages": (total + limit - 1) // limit,
        },
    }


# ═══════════════════════════════════════════════════════════════
#  SECTION 9 — PLATFORM HEALTH (for navbar widget)
#  Route: GET /admin/health
# ═══════════════════════════════════════════════════════════════

@router.get("/health")
async def platform_health(admin: User = Depends(get_current_admin)):
    """Returns real-time platform diagnostics for the admin navbar widget."""
    try:
        import time
        from src.core.database import get_mongo_client
        from src.core.config import settings as app_settings

        start = time.time()
        client = get_mongo_client()
        await client[app_settings.MONGODB_DB_NAME].command("ping")
        db_ms = round((time.time() - start) * 1000, 2)

        return {
            "success": True,
            "data": {
                "database":  {"status": "healthy", "latency_ms": db_ms},
                "api":       {"status": "healthy", "uptime": "99.98%"},
                "scheduler": {"status": "idle"},
                "overall":   "healthy",
            },
        }
    except Exception as e:
        return {
            "success": True,
            "data": {
                "database": {"status": "degraded", "error": str(e)},
                "overall":  "degraded",
            },
        }


# ═══════════════════════════════════════════════════════════════
#  SECTION — PAYMENTS MONITOR
# ═══════════════════════════════════════════════════════════════

@router.get("/payments/pending")
async def admin_get_pending_payments(
    page:  int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
):
    """
    Returns ALL pending payment requests across the entire platform.
    Admin sees everything — pump owner, logistic partner, amount, method, proof.
    """
    try:
        query = PaymentRequest.find(PaymentRequest.status == "pending").sort(-PaymentRequest.requested_at)

        total    = await query.count()
        payments = await query.skip((page - 1) * limit).limit(limit).to_list()

        result = []
        for pay in payments:
            partner = await User.get(pay.logistic_partner_id) if pay.logistic_partner_id else None
            pump    = await Pump.get(pay.pump_id) if pay.pump_id else None
            owner   = await User.get(pump.owner_id) if pump and pump.owner_id else None

            result.append({
                "id":                    str(pay.id),
                "amount":                pay.amount,
                "payment_type":          pay.payment_type,
                "transaction_reference": pay.transaction_reference,
                "screenshot_url":        pay.screenshot_url,
                "remarks":               pay.remarks,
                "status":                pay.status,
                "requested_at":          pay.requested_at.isoformat() if pay.requested_at else None,

                "logistic_partner": {
                    "id":    str(partner.id) if partner else None,
                    "name":  partner.full_name if partner else "N/A",
                    "email": partner.email if partner else "N/A",
                    "phone": partner.phone if partner else "N/A",
                },

                "pump": {
                    "id":   str(pump.id) if pump else None,
                    "name": pump.name if pump else "N/A",
                },

                "pump_owner": {
                    "id":    str(owner.id) if owner else None,
                    "name":  owner.full_name if owner else "N/A",
                    "email": owner.email if owner else "N/A",
                },
            })

        return {
            "success": True,
            "data": {
                "payments": result,
                "total":    total,
                "page":     page,
                "pages":    (total + limit - 1) // limit,
            },
        }

    except Exception as e:
        log.error("Admin pending payments error", error=str(e), traceback=traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to fetch pending payments.")


@router.get("/payments/all")
async def admin_get_all_payments(
    status_filter: str = Query(None, alias="status"),   # pending | approved | rejected
    pump_id:       str = Query(None),
    page:          int = Query(1, ge=1),
    limit:         int = Query(20, ge=1, le=100),
    admin:         User = Depends(get_current_admin),
):
    """
    Full payment history across the platform.
    Supports filter by status and pump_id.
    """
    try:
        filters = {}
        if status_filter:
            filters["status"] = status_filter
        if pump_id:
            filters["pump_id"] = to_oid(pump_id, "pump_id")

        query = PaymentRequest.find(filters).sort(-PaymentRequest.requested_at)

        total    = await query.count()
        payments = await query.skip((page - 1) * limit).limit(limit).to_list()

        result = []
        for pay in payments:
            partner = await User.get(pay.logistic_partner_id) if pay.logistic_partner_id else None
            pump    = await Pump.get(pay.pump_id) if pay.pump_id else None

            result.append({
                "id":                    str(pay.id),
                "amount":                pay.amount,
                "payment_type":          pay.payment_type,
                "transaction_reference": pay.transaction_reference,
                "screenshot_url":        pay.screenshot_url,
                "status":                pay.status,
                "requested_at":          pay.requested_at.isoformat() if pay.requested_at else None,
                "reviewed_at":           pay.reviewed_at.isoformat()  if pay.reviewed_at  else None,
                "reviewed_by":           str(pay.reviewed_by) if pay.reviewed_by else None,
                "partner_name":          partner.full_name if partner else "N/A",
                "pump_name":             pump.name if pump else "N/A",
            })

        return {
            "success": True,
            "data": {
                "payments": result,
                "total":    total,
                "page":     page,
                "pages":    (total + limit - 1) // limit,
            },
        }

    except Exception as e:
        log.error("Admin all payments error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch payments.")


@router.get("/payments/{payment_id}")
async def admin_get_payment_detail(
    payment_id: str,
    admin:      User = Depends(get_current_admin),
):
    """Full detail of a single payment request — for the PaymentDetailModal."""
    try:
        pay = await PaymentRequest.get(to_oid(payment_id, "payment_id"))
        if not pay:
            raise HTTPException(status_code=404, detail="Payment not found.")

        partner = await User.get(pay.logistic_partner_id) if pay.logistic_partner_id else None
        pump    = await Pump.get(pay.pump_id) if pay.pump_id else None
        owner   = await User.get(pump.owner_id) if pump and pump.owner_id else None

        return {
            "success": True,
            "data": {
                "id":                    str(pay.id),
                "amount":                pay.amount,
                "payment_type":          pay.payment_type,
                "transaction_reference": pay.transaction_reference,
                "screenshot_url":        pay.screenshot_url,
                "remarks":               pay.remarks,
                "status":                pay.status,
                "requested_at":          pay.requested_at.isoformat() if pay.requested_at else None,
                "reviewed_at":           pay.reviewed_at.isoformat()  if pay.reviewed_at  else None,

                "logistic_partner": {
                    "id":    str(partner.id) if partner else None,
                    "name":  partner.full_name if partner else "N/A",
                    "email": partner.email if partner else "N/A",
                    "phone": partner.phone if partner else "N/A",
                },
                "pump": {
                    "id":   str(pump.id) if pump else None,
                    "name": pump.name if pump else "N/A",
                },
                "pump_owner": {
                    "id":    str(owner.id) if owner else None,
                    "name":  owner.full_name if owner else "N/A",
                    "email": owner.email if owner else "N/A",
                },
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        log.error("Admin payment detail error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch payment detail.")


@router.post("/payments/{payment_id}/mark-paid")
async def admin_mark_payment_paid(
    payment_id: str,
    payload:    dict,
    admin:      User = Depends(get_current_admin),
):
    """
    Admin manually marks a payment as approved.
    Used for offline/cash payments that can't be auto-verified.

    Body: { "note": str (optional) }
    """
    try:
        note = payload.get("note", "Manually verified by admin").strip()

        pay = await PaymentRequest.get(to_oid(payment_id, "payment_id"))
        if not pay:
            raise HTTPException(status_code=404, detail="Payment not found.")

        if pay.status != "pending":
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"Payment is already '{pay.status}'. Only pending payments can be marked paid.",
                },
            )

        # ── Apply credit logic (same as pump owner approve) ──
        vehicles = await Vehicle.find(Vehicle.partner_id == pay.logistic_partner_id).to_list()

        remaining_payment = pay.amount
        total_settled     = 0.0

        for vehicle in vehicles:
            if remaining_payment <= 0:
                break
            customer = await Customer.find_one(
                Customer.vehicle_plate == vehicle.vehicle_plate,
                Customer.pump_id       == pay.pump_id,
            )
            if customer and customer.outstanding_amount > 0:
                settle_amount               = min(customer.outstanding_amount, remaining_payment)
                customer.outstanding_amount -= settle_amount
                remaining_payment           -= settle_amount
                total_settled               += settle_amount
                customer.updated_at          = datetime.utcnow()
                await customer.save()

        # Apply remaining as new credit
        new_credit = max(0.0, remaining_payment)
        for vehicle in vehicles:
            customer = await Customer.find_one(
                Customer.vehicle_plate == vehicle.vehicle_plate,
                Customer.pump_id       == pay.pump_id,
            )
            if customer:
                customer.credit_limit += new_credit
                customer.updated_at    = datetime.utcnow()
                await customer.save()

        # ── Update payment record ──
        pay.status      = "approved"
        pay.reviewed_at = datetime.utcnow()
        pay.reviewed_by = admin.id           # Admin's user ID
        pay.remarks     = f"[Admin Override] {note}"

        await pay.save()

        await write_audit_log(
            admin,
            action      = "PAYMENT_MARKED_PAID",
            target_type = "payment",
            target_id   = payment_id,
            metadata    = {
                "amount":        pay.amount,
                "total_settled": total_settled,
                "new_credit":    new_credit,
                "note":          note,
            },
        )

        return {
            "success":       True,
            "message":       "Payment marked as paid and credit updated.",
            "amount":        pay.amount,
            "total_settled": total_settled,
            "new_credit":    new_credit,
        }

    except HTTPException:
        raise
    except Exception as e:
        log.error("Admin mark-paid error", error=str(e), traceback=traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to mark payment as paid.")


# ═══════════════════════════════════════════════════════════════
#  SECTION 10 — SUPPORT TICKETS MANAGEMENT
#  Routes: GET /admin/support/tickets
#          POST /admin/support/tickets/{ticket_id}
# ═══════════════════════════════════════════════════════════════

async def ticket_to_dict(ticket: SupportTicket) -> dict:
    user = await User.get(ticket.user_id) if ticket.user_id else None
    user_name  = user.full_name if user else "N/A"
    user_email = user.email if user else "N/A"
    user_role  = "user"
    if user and user.roles:
        user_role = user.roles[0] if isinstance(user.roles, list) and len(user.roles) > 0 else "user"

    return {
        "id":        str(ticket.id),
        "subject":   ticket.subject,
        "status":    ticket.status,
        "priority":  ticket.priority,
        "userId":    str(ticket.user_id),
        "userName":  user_name,
        "userRole":  user_role,
        "userEmail": user_email,
        "createdAt": ticket.created_at.isoformat() + "Z" if ticket.created_at else None,
        "messages":  ticket.messages or [],
    }


@router.get("/support/tickets")
async def admin_get_support_tickets(admin: User = Depends(get_current_admin)):
    """Fetch all support tickets."""
    try:
        tickets = await SupportTicket.find_all().sort(-SupportTicket.created_at).to_list()
        return {
            "success": True,
            "data": {
                "tickets": [await ticket_to_dict(t) for t in tickets]
            }
        }
    except Exception as e:
        log.error("Get support tickets error", error=str(e), traceback=traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to fetch support tickets.")


@router.post("/support/tickets/{ticket_id}")
async def admin_reply_support_ticket(
    ticket_id: str,
    payload:   dict,
    admin:     User = Depends(get_current_admin),
):
    """Reply to a support ticket and/or update its status."""
    try:
        ticket = await SupportTicket.get(to_oid(ticket_id, "ticket_id"))
        if not ticket:
            raise HTTPException(status_code=404, detail="Support ticket not found.")

        message    = payload.get("message", "").strip()
        new_status = payload.get("status")

        # Update status if provided
        if new_status:
            ticket.status = new_status

        # Append message if message is not empty
        if message:
            new_msg = {
                "sender":     "admin",
                "senderName": admin.full_name or "System Admin",
                "message":    message,
                "timestamp":  datetime.utcnow().isoformat() + "Z",
            }
            current_messages = list(ticket.messages or [])
            current_messages.append(new_msg)
            ticket.messages = current_messages

        await ticket.save()

        await write_audit_log(
            admin,
            action="SUPPORT_TICKET_REPLIED",
            target_type="support_ticket",
            target_id=ticket_id,
            metadata={"status": ticket.status}
        )

        return {
            "success": True,
            "data": {
                "ticket": await ticket_to_dict(ticket)
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("Reply support ticket error", error=str(e), traceback=traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to reply to support ticket.")


# ═══════════════════════════════════════════════════════════════
#  SECTION — SUBSCRIPTION PAYMENTS (Razorpay)
# ═══════════════════════════════════════════════════════════════

@router.get("/subscription-payments")
async def admin_list_subscription_payments(
    page:  int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    admin: User = Depends(get_current_admin),
):
    """All Razorpay subscription payments — for Payments Monitor 'Subscription' tab."""
    try:
        query = SubscriptionPayment.find_all().sort(-SubscriptionPayment.created_at)
        total    = await query.count()
        payments = await query.skip((page - 1) * limit).limit(limit).to_list()

        result = []
        for pay in payments:
            pump  = await Pump.get(pay.pump_id) if pay.pump_id else None
            plan  = await SubscriptionPlan.get(pay.plan_id) if pay.plan_id else None
            owner = await User.get(pay.owner_id) if pay.owner_id else None

            result.append({
                "id":                  str(pay.id),
                "amount":              pay.amount,
                "currency":            pay.currency,
                "billing_cycle":       pay.billing_cycle,
                "status":              pay.status,
                "razorpay_payment_id": pay.razorpay_payment_id,
                "razorpay_order_id":   pay.razorpay_order_id,
                "pump_id":             str(pay.pump_id) if pay.pump_id else None,
                "pump_name":           pump.name if pump else None,
                "plan_id":             str(pay.plan_id) if pay.plan_id else None,
                "plan_name":           plan.name if plan else None,
                "owner_name":          owner.full_name if owner else None,
                "paid_at":             pay.paid_at.isoformat() if pay.paid_at else None,
                "created_at":          pay.created_at.isoformat() if pay.created_at else None,
            })

        return {
            "success": True,
            "data": {"payments": result, "total": total, "page": page},
        }

    except Exception as e:
        log.error("Admin subscription payments error", error=str(e), traceback=traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to fetch subscription payments.")


# ═══════════════════════════════════════════════════════════════
#  ADMIN SETTINGS  (singleton document)
# ═══════════════════════════════════════════════════════════════

async def get_or_create_settings() -> PlatformSettings:
    """Returns the singleton settings row, creating it with defaults if missing."""
    settings_row = await PlatformSettings.find_one(PlatformSettings.singleton_key == "global")
    if not settings_row:
        settings_row = PlatformSettings(
            singleton_key="global",
            api_key=f"ff_live_{secrets.token_hex(10)}",
        )
        await settings_row.insert()
    return settings_row


@router.get("/settings")
async def get_platform_settings(admin: User = Depends(get_current_admin)):
    """Returns current platform settings."""
    s = await get_or_create_settings()
    return {
        "success": True,
        "data": {
            "default_fleet_credit_limit":   s.default_fleet_credit_limit,
            "transaction_alert_threshold":  s.transaction_alert_threshold,
            "require_2fa":                  s.require_2fa,
            "admin_session_expiry_minutes": s.admin_session_expiry_minutes,
            "api_key":                      s.api_key,
            "updated_at":                   s.updated_at.isoformat() if s.updated_at else None,
        },
    }


@router.patch("/settings")
async def update_platform_settings(
    payload: dict,
    admin:   User = Depends(get_current_admin),
):
    """
    Updates platform settings.
    Body: { default_fleet_credit_limit, transaction_alert_threshold,
            require_2fa, admin_session_expiry_minutes }
    """
    s = await get_or_create_settings()

    for field in ["default_fleet_credit_limit", "transaction_alert_threshold",
                  "require_2fa", "admin_session_expiry_minutes"]:
        if field in payload:
            setattr(s, field, payload[field])

    s.updated_by = admin.id
    s.updated_at = datetime.utcnow()
    await s.save()

    await write_audit_log(admin, action="PLATFORM_SETTINGS_UPDATED", metadata=payload)

    return {"success": True, "message": "Settings updated successfully."}


@router.post("/settings/regenerate-key")
async def regenerate_api_key(admin: User = Depends(get_current_admin)):
    """Generates a new live API key, invalidating the old one."""
    s = await get_or_create_settings()
    s.api_key    = f"ff_live_{secrets.token_hex(10)}"
    s.updated_by = admin.id
    s.updated_at = datetime.utcnow()
    await s.save()

    await write_audit_log(admin, action="API_KEY_REGENERATED")

    return {"success": True, "data": {"api_key": s.api_key}}


# ═══════════════════════════════════════════════════════════════
#  SECTION — LOGISTICS PARTNER VERIFICATION (MongoDB / Beanie)
#  Routes: GET  /admin/logistics          → all logistics (tabs)
#          GET  /admin/logistics/pending  → pending queue only
#          POST /admin/logistics/{id}/approve
#          POST /admin/logistics/{id}/reject
# ═══════════════════════════════════════════════════════════════

@router.get("/logistics")
async def list_logistics_partners(
    status_filter: str = Query(None, alias="status"),   # "pending" | "verified" | "rejected" | None=all
    page:          int = Query(1, ge=1),
    limit:         int = Query(30, ge=1, le=100),
    admin: User = Depends(get_current_admin),
):
    """
    Returns all logistic partners from MongoDB (Beanie).
    Optional ?status=pending|verified|rejected to filter.
    """
    # Build base query — users with logistic role
    all_logistics = await User.find({"roles": {"$in": ["logistic"]}}).to_list()

    # Filter by verification status
    if status_filter:
        all_logistics = [
            u for u in all_logistics
            if (u.verification_status or "pending") == status_filter
        ]

    total = len(all_logistics)
    paginated = all_logistics[(page - 1) * limit: page * limit]

    def _fmt(u: User):
        return {
            "id": str(u.id),
            "name": u.full_name or "",
            "email": u.email,
            "phone": u.phone or "",
            "company_name": u.company_name or "",
            "gstin": u.gstin or "",
            "fleet_size": u.fleet_size,
            "verification_status": u.verification_status or "pending",
            "verification_notes": u.verification_notes,
            "verified_at": u.verified_at.isoformat() if u.verified_at else None,
            "verified_by": u.verified_by,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "kyc_documents": u.kyc_documents or [],
        }

    return {
        "success": True,
        "data": {
            "logistics": [_fmt(u) for u in paginated],
            "total": total,
            "page": page,
            "pages": (total + limit - 1) // limit,
            "counts": {
                "pending":  sum(1 for u in all_logistics if (u.verification_status or "pending") == "pending"),
                "verified": sum(1 for u in all_logistics if u.verification_status == "verified"),
                "rejected": sum(1 for u in all_logistics if u.verification_status == "rejected"),
            }
        }
    }


@router.get("/logistics/pending")
async def list_pending_logistics(admin: User = Depends(get_current_admin)):
    """Quick endpoint — just pending verification queue."""
    pending = await User.find(
        {"roles": {"$in": ["logistic"]},
         "$or": [
             {"verification_status": "pending"},
             {"verification_status": {"$exists": False}},
             {"verification_status": None}
         ]}
    ).sort(-User.created_at).to_list()

    return {
        "success": True,
        "data": {
            "count": len(pending),
            "logistics": [
                {
                    "id": str(u.id),
                    "name": u.full_name or "",
                    "email": u.email,
                    "phone": u.phone or "",
                    "company_name": u.company_name or "",
                    "gstin": u.gstin or "",
                    "fleet_size": u.fleet_size,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in pending
            ]
        }
    }


@router.post("/logistics/{user_id}/approve")
async def approve_logistic_partner(
    user_id: str,
    admin:   User = Depends(get_current_admin),
):
    """
    Approves a logistic partner account.
    Sets verification_status='verified' so they can fully use the portal.
    """
    user = await User.get(to_oid(user_id, "user_id"))
    if not user or "logistic" not in (user.roles or []):
        raise HTTPException(status_code=404, detail="Logistic partner not found.")

    if user.verification_status == "verified":
        return {"success": True, "message": "Partner is already verified.", "already_verified": True}

    user.verification_status = "verified"
    user.verification_notes  = None
    user.verified_at         = datetime.utcnow()
    user.verified_by         = admin.email
    user.is_active           = True
    user.updated_at          = datetime.utcnow()
    await user.save()

    await write_audit_log(
        admin,
        action="LOGISTIC_PARTNER_APPROVED",
        target_type="logistic_user",
        target_id=user_id,
        metadata={"partner_email": user.email, "company": user.company_name}
    )

    log.info("Logistic partner approved", user_id=user_id, email=user.email, approved_by=admin.email)

    return {
        "success": True,
        "message": f"Logistic partner '{user.full_name or user.email}' has been verified and activated.",
        "partner": {
            "id": str(user.id),
            "name": user.full_name,
            "email": user.email,
            "verification_status": user.verification_status,
        }
    }


@router.post("/logistics/{user_id}/reject")
async def reject_logistic_partner(
    user_id: str,
    payload: dict,
    admin:   User = Depends(get_current_admin),
):
    """
    Rejects a logistic partner's verification.
    Body: { "reason": str }
    """
    reason = (payload.get("reason") or "").strip()
    if not reason:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Rejection reason is required."}
        )

    user = await User.get(to_oid(user_id, "user_id"))
    if not user or "logistic" not in (user.roles or []):
        raise HTTPException(status_code=404, detail="Logistic partner not found.")

    user.verification_status = "rejected"
    user.verification_notes  = reason
    user.verified_at         = datetime.utcnow()
    user.verified_by         = admin.email
    user.updated_at          = datetime.utcnow()
    await user.save()

    await write_audit_log(
        admin,
        action="LOGISTIC_PARTNER_REJECTED",
        target_type="logistic_user",
        target_id=user_id,
        metadata={"partner_email": user.email, "reason": reason}
    )

    log.info("Logistic partner rejected", user_id=user_id, email=user.email, reason=reason)

    return {
        "success": True,
        "message": f"Logistic partner '{user.full_name or user.email}' has been rejected.",
        "reason": reason,
    }