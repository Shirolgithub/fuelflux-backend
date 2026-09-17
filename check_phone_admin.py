"""
Run from backend folder: python check_admin_phone.py
Shows EXACTLY what's in MongoDB for the admin user right now.
"""
import asyncio, sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

async def main():
    from src.core.database import init_db, close_db
    from src.db.models.user import User

    await init_db()

    # Find ALL users with admin/superadmin role (in case there are duplicates)
    admins = await User.find({"roles": {"$in": ["admin", "superadmin"]}}).to_list()

    print(f"\n{'='*50}")
    print(f"  Found {len(admins)} admin user(s) in MongoDB")
    print(f"{'='*50}")

    for u in admins:
        print(f"\n  ID:    {u.id}")
        print(f"  Email: {u.email}")
        print(f"  Phone: {u.phone}")
        print(f"  Roles: {u.roles}")
        print(f"  Active: {u.is_active}")

    print(f"\n{'='*50}\n")
    await close_db()

if __name__ == "__main__":
    asyncio.run(main())