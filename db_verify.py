import sys, sqlite3
sys.path.insert(0, '.')
from src.core.config import settings

db_url = settings.DATABASE_URL
db_path = db_url.replace('sqlite:///', '')
print(f'DB path: {db_path}')

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# ── payment_requests migration ─────────────────────────────────────────────────
cursor.execute('PRAGMA table_info(payment_requests)')
cols = cursor.fetchall()
col_names = [c[1] for c in cols]
print(f'\npayment_requests columns: {col_names}')

if 'screenshot_url' not in col_names:
    cursor.execute('ALTER TABLE payment_requests ADD COLUMN screenshot_url TEXT')
    print('  [MIGRATED] Added screenshot_url column')
else:
    print('  [OK] screenshot_url already exists')

conn.commit()

# ── vehicles columns check ─────────────────────────────────────────────────────
cursor.execute('PRAGMA table_info(vehicles)')
v_cols = [c[1] for c in cursor.fetchall()]
print(f'\nvehicles columns: {v_cols}')

# ── transactions columns check ─────────────────────────────────────────────────
cursor.execute('PRAGMA table_info(transactions)')
t_cols = [c[1] for c in cursor.fetchall()]
print(f'\ntransactions columns: {t_cols}')

# ── Logistic user data ─────────────────────────────────────────────────────────
cursor.execute("SELECT id, email FROM users WHERE roles LIKE '%logistic%'")
logistic_users = cursor.fetchall()
print(f'\nLogistic users: {logistic_users}')

for uid, email in logistic_users:
    cursor.execute('SELECT vehicle_plate, is_active FROM vehicles WHERE partner_id=?', (uid,))
    veh = cursor.fetchall()
    print(f'  {email} (id={uid}) -> {len(veh)} vehicles: {veh}')

cursor.execute('SELECT COUNT(*) FROM transactions')
txn_count = cursor.fetchone()[0]
print(f'\nTotal transactions in DB: {txn_count}')

cursor.execute('SELECT COUNT(*) FROM payment_requests')
pay_count = cursor.fetchone()[0]
print(f'Total payment_requests in DB: {pay_count}')

conn.close()
print('\nAll checks complete.')
