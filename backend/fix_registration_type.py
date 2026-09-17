"""One-off script: fix invalid registration_type values in udhaar_vehicles"""
from src.core.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    result = conn.execute(text(
        "SELECT id, number_plate, registration_type FROM udhaar_vehicles "
        "WHERE registration_type NOT IN ('private', 'commercial')"
    ))
    rows = result.fetchall()
    print(f"Found {len(rows)} invalid rows:")
    for r in rows:
        print(f"  id={r[0]}, plate={r[1]}, type={r[2]!r}")

    conn.execute(text(
        "UPDATE udhaar_vehicles SET registration_type='commercial' "
        "WHERE registration_type NOT IN ('private', 'commercial')"
    ))
    conn.commit()
    print("Done — all invalid rows set to 'commercial'.")
