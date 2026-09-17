from datetime import datetime, timedelta
from jose import jwt
import bcrypt

def hash_password(password: str) -> str:
    """Simple and reliable password hashing"""
    # Truncate to 72 bytes (bcrypt limit)
    password = password[:72].encode('utf-8')
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password, salt)
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password"""
    plain_password = plain_password[:72].encode('utf-8')
    hashed_password = hashed_password.encode('utf-8')
    return bcrypt.checkpw(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=30))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, "supersecretkeychangethisinproduction2026", algorithm="HS256")


def create_refresh_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=30))
    to_encode.update({"exp": expire, "scope": "refresh"})
    return jwt.encode(to_encode, "supersecretkeychangethisinproduction2026", algorithm="HS256")


def create_password_reset_token(email: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode = {
        "sub": email,
        "scope": "password_reset",
        "exp": expire
    }
    return jwt.encode(to_encode, "supersecretkeychangethisinproduction2026", algorithm="HS256")