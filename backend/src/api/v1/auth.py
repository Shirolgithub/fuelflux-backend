from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
import structlog
import traceback
import random
from datetime import datetime, timezone, timedelta
from jose import jwt, JWTError

from src.core.config import settings
from src.db.models.user import User, BlacklistedToken, OTPCode
from src.db.schemas.auth import (
    UserLogin, Token, UserCreate, ForgotPasswordRequest, ResetPasswordRequest,
    SelectRoleRequest, RefreshTokenRequest, SendOTPRequest, VerifyOTPRequest
)
from src.core.security import hash_password, verify_password, create_access_token, create_password_reset_token, create_refresh_token
from src.core.dependencies import get_current_active_user, oauth2_scheme
from src.db.schemas.auth import EmployeeLogin
from src.db.models.attendant import Attendant

log = structlog.get_logger()

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_dict(user: User) -> dict:
    return {
        "id": str(user.id),
        "name": user.full_name or "",
        "email": user.email,
        "phone": user.phone or "",
        "roles": user.roles,
        "is_active": user.is_active,
        "company_name": user.company_name or "",
        "gstin": user.gstin or "",
        "fleet_size": user.fleet_size,
        "verification_status": user.verification_status,   # None | "pending" | "verified" | "rejected"
        "verification_notes": user.verification_notes,
        "created_at": user.created_at.isoformat() if user.created_at else "",
        "updated_at": user.updated_at.isoformat() if user.updated_at else ""
    }


@router.post("/register")
async def register(user_data: UserCreate):
    try:
        log.info("Register attempt", email=user_data.email)

        # Check if user already exists
        existing = await User.find_one(User.email == user_data.email)
        if existing:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "Email already registered"}
            )

        # Check if phone already exists
        if user_data.phone:
            existing_phone = await User.find_one(User.phone == user_data.phone)
            if existing_phone:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"success": False, "message": "Phone already registered"}
                )

        name_val = user_data.name or user_data.full_name or ""
        roles_val = user_data.roles
        if user_data.role:
            roles_val = [user_data.role]
        if not roles_val:
            roles_val = ["pump_owner"]

        hashed_pw = hash_password(user_data.password)

        # Logistic partners require admin verification before portal access
        is_logistic = "logistic" in roles_val
        verification_status = "pending" if is_logistic else None

        user = User(
            email=user_data.email,
            phone=user_data.phone,
            full_name=name_val,
            hashed_password=hashed_pw,
            roles=roles_val,
            company_name=getattr(user_data, 'company_name', None),
            gstin=getattr(user_data, 'gstin', None),
            fleet_size=getattr(user_data, 'fleet_size', None),
            verification_status=verification_status,
        )
        await user.insert()

        log.info("User registered successfully", user_id=str(user.id), email=user.email,
                 verification_status=verification_status)

        ud = _user_dict(user)

        access_token = create_access_token({"sub": user.email, "roles": user.roles})
        refresh_token = create_refresh_token({"sub": user.email})

        return {
            "success": True,
            "user": ud,
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "data": {"user": ud, "accessToken": access_token, "refreshToken": refresh_token}
        }

    except Exception as e:
        log.error("Registration failed", error=str(e), traceback=traceback.format_exc())
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Registration failed: {str(e)}"}
        )


@router.post("/login")
async def login(user_data: UserLogin):
    try:
        identifier = user_data.emailOrPhone or user_data.email
        if not identifier:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "Email or Phone is required"}
            )

        # Search by email or phone using $or
        from beanie.operators import Or
        user = await User.find_one(Or(User.email == identifier, User.phone == identifier))

        if not user or not verify_password(user_data.password, user.hashed_password):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"success": False, "message": "Invalid credentials"}
            )

        if not user.is_active:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "Inactive user"}
            )

        # ── Logistic partner verification check ──────────────────────────────
        if "logistic" in (user.roles or []):
            vs = user.verification_status
            if vs == "pending" or vs is None and "logistic" in (user.roles or []):
                # Allow login but flag as pending — frontend will show gate screen
                access_token = create_access_token({"sub": user.email, "roles": user.roles})
                refresh_token = create_refresh_token({"sub": user.email})
                ud = _user_dict(user)
                return {
                    "success": True,
                    "pending_verification": True,
                    "verification_status": "pending",
                    "message": "Your account is pending admin verification.",
                    "user": ud,
                    "accessToken": access_token,
                    "refreshToken": refresh_token,
                    "data": {
                        "user": ud,
                        "accessToken": access_token,
                        "refreshToken": refresh_token,
                        "pending_verification": True,
                        "verification_status": "pending"
                    }
                }
            elif vs == "rejected":
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "success": False,
                        "verification_status": "rejected",
                        "message": user.verification_notes or "Your account has been rejected. Please contact FuelFlux support."
                    }
                )

        access_token = create_access_token({"sub": user.email, "roles": user.roles})
        refresh_token = create_refresh_token({"sub": user.email})

        ud = _user_dict(user)
        return {
            "success": True,
            "user": ud,
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "data": {
                "user": ud,
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "verification_status": user.verification_status
            }
        }

    except Exception as e:
        log.error("Login failed", error=str(e))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": "Internal server error"}
        )


@router.post("/select-role")
async def select_role(
    request_data: SelectRoleRequest,
    current_user: User = Depends(get_current_active_user)
):
    role = request_data.role
    if role not in current_user.roles:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"success": False, "message": "You don't have this role"}
        )

    token = create_access_token({"sub": current_user.email, "roles": [role]})
    return {
        "success": True,
        "accessToken": token,
        "data": {"accessToken": token, "activeRole": role}
    }


@router.post("/refresh")
async def refresh_token(request_data: RefreshTokenRequest):
    try:
        try:
            payload = jwt.decode(
                request_data.refreshToken,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM]
            )
            email: str = payload.get("sub")
            scope: str = payload.get("scope")
            if email is None or scope != "refresh":
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
        except JWTError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Expired or invalid refresh token")

        user = await User.find_one(User.email == email)
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

        access_token = create_access_token({"sub": user.email, "roles": user.roles})
        new_refresh_token = create_refresh_token({"sub": user.email})

        return {
            "success": True,
            "accessToken": access_token,
            "refreshToken": new_refresh_token,
            "data": {"accessToken": access_token, "refreshToken": new_refresh_token}
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("Refresh token failed", error=str(e), traceback=traceback.format_exc())
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": "Internal server error"}
        )


@router.post("/forgot-password")
async def forgot_password(request_data: ForgotPasswordRequest):
    try:
        user = await User.find_one(User.email == request_data.email)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User with this email does not exist")

        reset_token = create_password_reset_token(user.email)
        log.info("Password reset token generated", email=user.email, token=reset_token)

        return {
            "message": "Password reset token generated successfully. In production, this would be sent to your email.",
            "reset_token": reset_token
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("Forgot password failed", error=str(e))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to initiate password reset")


@router.post("/reset-password")
async def reset_password(request_data: ResetPasswordRequest):
    try:
        try:
            payload = jwt.decode(request_data.token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            email: str = payload.get("sub")
            scope: str = payload.get("scope")
            exp = payload.get("exp")
            if email is None or scope != "password_reset":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
        except JWTError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token has expired or is invalid")

        # Check if reset token is blacklisted
        blacklisted = await BlacklistedToken.find_one(BlacklistedToken.token == request_data.token)
        if blacklisted:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This reset token has already been used")

        user = await User.find_one(User.email == email)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        user.hashed_password = hash_password(request_data.new_password)
        user.updated_at = datetime.utcnow()
        await user.save()

        expires_at = datetime.fromtimestamp(exp, timezone.utc).replace(tzinfo=None) if exp else datetime.utcnow() + timedelta(minutes=15)
        bl = BlacklistedToken(token=request_data.token, expires_at=expires_at)
        await bl.insert()

        log.info("Password reset successfully", email=email)
        return {"message": "Password reset successful"}

    except HTTPException:
        raise
    except Exception as e:
        log.error("Reset password failed", error=str(e))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reset password")


@router.post("/logout")
async def logout(
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_active_user)
):
    try:
        existing = await BlacklistedToken.find_one(BlacklistedToken.token == token)
        if existing:
            return {"message": "Successfully logged out"}

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            exp = payload.get("exp")
            expires_at = datetime.fromtimestamp(exp, timezone.utc).replace(tzinfo=None) if exp else datetime.utcnow() + timedelta(days=7)
        except Exception:
            expires_at = datetime.utcnow() + timedelta(days=7)

        bl = BlacklistedToken(token=token, expires_at=expires_at)
        await bl.insert()

        log.info("User logged out successfully", email=current_user.email)
        return {"message": "Successfully logged out"}

    except Exception as e:
        log.error("Logout failed", error=str(e))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to log out")


@router.post("/send-otp")
async def send_otp(request_data: SendOTPRequest):
    """OTP generate karo aur DB mein store karo."""
    try:
        identifier = request_data.identifier.strip()
        purpose = request_data.purpose or "verification"

        if purpose == "reset":
            from beanie.operators import Or
            user = await User.find_one(Or(User.email == identifier, User.phone == identifier))
            if not user:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"success": False, "message": "No account found with this email or phone"}
                )

        # Purane unused OTPs invalidate karo
        old_otps = await OTPCode.find(
            OTPCode.identifier == identifier,
            OTPCode.purpose == purpose,
            OTPCode.is_used == False
        ).to_list()
        for otp in old_otps:
            await otp.delete()

        otp_code = str(random.randint(100000, 999999))
        expires_at = datetime.utcnow() + timedelta(minutes=10)
        otp_type = "email" if "@" in identifier else "sms"

        otp_entry = OTPCode(
            identifier=identifier,
            code=otp_code,
            otp_type=otp_type,
            purpose=purpose,
            is_used=False,
            expires_at=expires_at
        )
        await otp_entry.insert()

        log.info("OTP Generated [DEV MODE]", identifier=identifier, otp=otp_code, purpose=purpose)
        print(f"\n{'='*40}")
        print(f"  OTP for {identifier}: {otp_code}")
        print(f"  Purpose: {purpose} | Expires in: 10 mins")
        print(f"{'='*40}\n")

        return {
            "success": True,
            "message": f"OTP sent to {identifier}. Valid for 10 minutes.",
            "otp": otp_code  # DEV ONLY
        }

    except Exception as e:
        log.error("Send OTP failed", error=str(e), traceback=traceback.format_exc())
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"Failed to send OTP: {str(e)}"}
        )


@router.post("/verify-otp")
async def verify_otp(request_data: VerifyOTPRequest):
    """OTP verify karo."""
    try:
        identifier = request_data.identifier.strip()
        code = request_data.code.strip()

        otp_entry = await OTPCode.find_one(
            OTPCode.identifier == identifier,
            OTPCode.code == code,
            OTPCode.is_used == False,
            OTPCode.expires_at > datetime.utcnow()
        )

        if not otp_entry:
            # Check if expired
            expired = await OTPCode.find_one(
                OTPCode.identifier == identifier,
                OTPCode.code == code,
                OTPCode.is_used == False
            )
            if expired:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"success": False, "message": "OTP has expired. Please request a new one."}
                )
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "message": "Invalid OTP. Please check and try again."}
            )

        otp_entry.is_used = True
        await otp_entry.save()

        log.info("OTP verified successfully", identifier=identifier, purpose=otp_entry.purpose)

        reset_token = None
        if otp_entry.purpose == "reset":
            from beanie.operators import Or
            user = await User.find_one(Or(User.email == identifier, User.phone == identifier))
            if user:
                reset_token = create_password_reset_token(user.email)

        return {
            "success": True,
            "message": "OTP verified successfully",
            "purpose": otp_entry.purpose,
            "reset_token": reset_token
        }

    except Exception as e:
        log.error("Verify OTP failed", error=str(e), traceback=traceback.format_exc())
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": f"OTP verification failed: {str(e)}"}
        )

@router.post("/employee-login")
async def employee_login(data: EmployeeLogin):
    """
    Employee login using employee_id + password.
    Returns a JWT with scope='attendant' and pump_id embedded.
    This token ONLY works on /employee/* routes.
    """
    try:
        # Find attendant by employee_id
        attendant = await Attendant.find_one(Attendant.employee_id == data.employee_id)
 
        if not attendant or not verify_password(data.password, attendant.hashed_password):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"success": False, "message": "Invalid employee ID or password"}
            )
 
        if not attendant.is_active:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"success": False, "message": "Your account is deactivated. Contact your pump owner."}
            )
 
        # JWT with attendant scope + pump_id for data isolation
        access_token = create_access_token({
            "sub": str(attendant.id),
            "scope": "attendant",
            "pump_id": str(attendant.pump_id),
            "role": "attendant",
            "employee_id": attendant.employee_id,
        })
 
        # Refresh token for employee
        refresh_token = create_refresh_token({
            "sub": str(attendant.id),
            "scope_hint": "attendant",           # hint for refresh endpoint
            "pump_id": str(attendant.pump_id),
        })
 
        log.info("Employee logged in", employee_id=attendant.employee_id, pump_id=str(attendant.pump_id))
 
        return {
            "success": True,
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "user": {
                "id": str(attendant.id),
                "name": attendant.name,
                "employee_id": attendant.employee_id,
                "designation": attendant.designation,
                "shift": attendant.shift,
                "pump_id": str(attendant.pump_id),
                "role": "attendant",
            },
            "data": {
                "accessToken": access_token,
                "refreshToken": refresh_token,
            }
        }
 
    except Exception as e:
        log.error("Employee login failed", error=str(e))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"success": False, "message": "Internal server error"}
        )