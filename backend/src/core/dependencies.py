from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError

from src.core.config import settings
from src.db.models.user import User, BlacklistedToken
from src.db.schemas.auth import TokenData

# OAuth2 Scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ── EXISTING (NO CHANGES) ─────────────────────────────────────────────────────

async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    blacklisted = await BlacklistedToken.find_one(BlacklistedToken.token == token)
    if blacklisted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been invalidated (logged out)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        roles: list = payload.get("roles", [])
        scope: str = payload.get("scope")

        # Block attendant tokens from accessing pump_owner routes
        if scope in ("password_reset", "refresh", "attendant"):
            raise credentials_exception

        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email, roles=roles)
    except JWTError:
        raise credentials_exception

    user = await User.find_one(User.email == token_data.email)
    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def require_role(required_roles: list, allow_unverified: bool = False):
    async def role_checker(current_user: User = Depends(get_current_active_user)) -> User:
        matching_roles = [role for role in required_roles if role in current_user.roles]
        if not matching_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role(s) {required_roles} required"
            )
        if not allow_unverified and "logistic" in matching_roles and current_user.verification_status != "verified":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your logistic account is pending admin verification."
            )
        return current_user
    return role_checker


# ── NEW — Employee (Attendant) Auth ───────────────────────────────────────────

async def get_current_attendant(token: str = Depends(oauth2_scheme)):
    """
    Dependency for employee-only routes.
    Validates attendant JWT (scope == "attendant").
    Returns Attendant object — pump_id is always available for data isolation.

    IMPORTANT: This will REJECT pump_owner tokens.
    Pump owner cannot accidentally call employee routes.
    """
    from src.db.models.attendant import Attendant
    from beanie import PydanticObjectId

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate employee credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Blacklist check
    blacklisted = await BlacklistedToken.find_one(BlacklistedToken.token == token)
    if blacklisted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been invalidated (logged out)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        scope: str = payload.get("scope")
        attendant_id: str = payload.get("sub")
        pump_id: str = payload.get("pump_id")

        # Must be attendant scope — pump_owner token will be rejected here
        if scope != "attendant":
            raise credentials_exception

        if not attendant_id or not pump_id:
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    attendant = await Attendant.find_one(Attendant.id == PydanticObjectId(attendant_id))

    if attendant is None:
        raise credentials_exception

    if not attendant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated. Contact your pump owner."
        )

    return attendant