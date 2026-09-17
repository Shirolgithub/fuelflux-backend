import sys, requests, json
sys.path.insert(0, '.')

BASE = 'http://127.0.0.1:8000/api/v1'

# Try different credentials
test_credentials = [
    ('testowner@gmail.com', 'password123'),
    ('owner@fuelflux.com', 'password123'),
    ('Om9523kumar@gmail.com', 'password123'),
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
    print('Could not login with any pump owner credential. Exiting.')
    sys.exit(1)

headers = {'Authorization': f'Bearer {token}'}

# Step 2: Dashboard Overview
do_resp = requests.get(f'{BASE}/dashboard/overview', headers=headers, timeout=10)
print(f'\nDashboard overview status: {do_resp.status_code}')
if do_resp.status_code == 200:
    print(json.dumps(do_resp.json(), indent=2))
else:
    print(do_resp.text)

# Step 3: Owner Pumps
p_resp = requests.get(f'{BASE}/dashboard/pumps', headers=headers, timeout=10)
print(f'\nOwner pumps status: {p_resp.status_code}')
if p_resp.status_code == 200:
    print(json.dumps(p_resp.json(), indent=2))
else:
    print(p_resp.text)

print(f'\nAll pump owner endpoint tests complete (as: {logged_in_as}).')
