import sys, requests, json
sys.path.insert(0, '.')

BASE = 'http://127.0.0.1:8000/api/v1'

# Try different credentials
test_credentials = [
    ('Omkumar@gmail.com', 'password123'),
    ('om123@gmail.com', 'password123'),
    ('logistic@fuelflux.com', 'password123'),
    ('Omkumar@gmail.com', 'Om@12345'),
    ('Omkumar@gmail.com', 'password'),
]

token = None
logged_in_as = None

for email, pwd in test_credentials:
    resp = requests.post(f'{BASE}/auth/login', json={'email': email, 'password': pwd}, timeout=5)
    print(f'  {email} / {pwd} -> {resp.status_code}')
    if resp.status_code == 200:
        token = resp.json().get('accessToken') or resp.json().get('access_token')
        logged_in_as = email
        print(f'  SUCCESS! Logged in as {email}')
        break

if not token:
    print('Could not login with any credential. Exiting.')
    sys.exit(1)

headers = {'Authorization': f'Bearer {token}'}

# Step 2: Vehicles
v_resp = requests.get(f'{BASE}/logistic/vehicles', headers=headers, timeout=10)
print(f'\nVehicles status: {v_resp.status_code}')
if v_resp.status_code == 200:
    vehicles = v_resp.json()
    print(f'Vehicle count: {len(vehicles)}')
    for v in vehicles:
        plate = v.get('vehicle_plate', '?')
        model = v.get('make_model', '?')
        active = v.get('is_active', '?')
        print(f'  {plate} | {model} | active={active}')
else:
    print(v_resp.text)

# Step 3: Dashboard
d_resp = requests.get(f'{BASE}/logistic/dashboard', headers=headers, timeout=10)
print(f'\nDashboard status: {d_resp.status_code}')
if d_resp.status_code == 200:
    print(json.dumps(d_resp.json(), indent=2))
else:
    print(d_resp.text)

# Step 4: Transactions
t_resp = requests.get(f'{BASE}/logistic/transactions', headers=headers, timeout=10)
print(f'\nTransactions status: {t_resp.status_code}')
if t_resp.status_code == 200:
    txns = t_resp.json()
    print(f'Transaction count: {len(txns)}')
    for t in txns[:3]:
        plate = t.get('vehicleNumber', '?')
        pump = t.get('pumpName', '?')
        amt = t.get('amount', 0)
        print(f'  {plate} | {pump} | Rs.{amt}')
else:
    print(t_resp.text)

# Step 5: Wallet
w_resp = requests.get(f'{BASE}/logistic/wallet', headers=headers, timeout=10)
print(f'\nWallet status: {w_resp.status_code}')
if w_resp.status_code == 200:
    w = w_resp.json()
    fleet_id = w.get('fleetId', '?')
    balance = w.get('balance', 0)
    txn_count = len(w.get('transactions', []))
    print(f'  fleetId: {fleet_id}')
    print(f'  balance: Rs.{balance:,.2f}')
    print(f'  transactions: {txn_count}')
else:
    print(w_resp.text)

print(f'\nAll endpoint tests complete (as: {logged_in_as}).')
