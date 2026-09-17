"""
create_admin.py
─────────────────────────────────────────────────────────
Standalone script to create (or promote) an admin user in
MongoDB via Beanie.

USAGE:
    python create_admin.py

Run this from your backend root (where you can import `src.*`),
with your virtualenv activated, e.g.:

    (.venv) PS D:\\Fuelflux\\backend> python create_admin.py

It will:
  1. Connect to MongoDB using your existing init_db()
  2. Ask for email / password / full name on the console
     (or you can hardcode them below — see CONFIG section)
  3. If a user with that email already exists:
        - adds "admin" to their roles (if missing)
        - optionally resets their password
  4. If no such user exists:
        - creates a brand-new admin user

SAFETY:
  - Never commit real passwords into this file if you hardcode them.
  - Delete/secure this script after use in production environments.
─────────────────────────────────────────────────────────
"""

import asyncio
import getpass
import sys
from typing import Optional

# ─────────────────────────────────────────────
# CONFIG — set these to skip interactive prompts
# Leave as None to be asked on the console instead.
# ─────────────────────────────────────────────
EMAIL: Optional[str] = "admin@fuelflux.com"       # e.g. "admin@fuelflux.com"
PASSWORD: Optional[str] = "Om9523kum@r"     # e.g. "StrongPass123!"
FULL_NAME: Optional[str] = "FuelFlux Admin"    # e.g. "Super Admin"
PHONE: Optional[str] = "+919523698906"        # optional, e.g. "+919999999999"
ROLE: str = "superadmin"           # "admin" or "superadmin"


async def main():
    # Import here so the script fails fast with a clear venv/path error
    # if run from the wrong directory, before touching the DB.
    from src.core.database import init_db, close_db
    from src.db.models.user import User

    # Try the common hashing function names — adjust if yours differs.
    hash_fn = None
    hash_fn_name = None
    try:
        from src.core.security import get_password_hash as hash_fn
        hash_fn_name = "get_password_hash"
    except ImportError:
        try:
            from src.core.security import hash_password as hash_fn
            hash_fn_name = "hash_password"
        except ImportError:
            print(
                "[ERROR] Could not import a password-hashing function from "
                "src.core.security (tried get_password_hash, hash_password).\n"
                "Open src/core/security.py, find the function that hashes "
                "passwords on signup, and update the import in this script."
            )
            sys.exit(1)

    print(f"[INFO] Using password hasher: src.core.security.{hash_fn_name}()")

    print("[INFO] Connecting to MongoDB...")
    await init_db()
    print("[INFO] Connected.\n")

    # ── Gather inputs ──────────────────────────────────────────────
    email = EMAIL or input("Admin email: ").strip()
    if not email:
        print("[ERROR] Email is required.")
        await close_db()
        sys.exit(1)

    full_name = FULL_NAME if FULL_NAME is not None else input("Full name (optional): ").strip()
    phone = PHONE if PHONE is not None else input("Phone (optional): ").strip()

    role = ROLE
    if role not in ("admin", "superadmin"):
        role = "admin"

    # ── Check if user already exists ───────────────────────────────
    existing = await User.find_one(User.email == email)

    if existing:
        print(f"\n[INFO] A user with email '{email}' already exists.")
        print(f"       Current roles: {existing.roles}")

        change_password = input("Reset their password too? (y/N): ").strip().lower() == "y"

        if role not in (existing.roles or []):
            existing.roles = list(existing.roles or []) + [role]
            print(f"[INFO] Added role '{role}'.")
        else:
            print(f"[INFO] User already has role '{role}'.")

        existing.is_active = True

        if change_password:
            password = PASSWORD or getpass.getpass("New password: ").strip()
            if not password:
                print("[ERROR] Password cannot be empty. Skipping password reset.")
            else:
                existing.hashed_password = hash_fn(password)
                print("[INFO] Password updated.")

        await existing.save()
        print(f"\n✅ User '{email}' updated successfully. Roles: {existing.roles}")

    else:
        password = PASSWORD or getpass.getpass("New password: ").strip()
        if not password:
            print("[ERROR] Password is required to create a new user.")
            await close_db()
            sys.exit(1)

        confirm = PASSWORD or getpass.getpass("Confirm password: ").strip()
        if password != confirm:
            print("[ERROR] Passwords do not match.")
            await close_db()
            sys.exit(1)

        new_user = User(
            email=email,
            phone=phone or None,
            full_name=full_name or "Administrator",
            hashed_password=hash_fn(password),
            roles=[role],
            is_active=True,
        )
        await new_user.insert()
        print(f"\n✅ New admin user created successfully!")
        print(f"   Email: {new_user.email}")
        print(f"   Roles: {new_user.roles}")
        print(f"   ID:    {new_user.id}")

    await close_db()
    print("\n[INFO] Done. You can now log in via POST /admin/auth/login")


if __name__ == "__main__":
    asyncio.run(main())