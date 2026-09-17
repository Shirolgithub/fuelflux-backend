import requests
import json

BASE = 'http://127.0.0.1:8000/api/v1'

# First login as admin to see all users with pump_owner role
admin_creds = ('admin@fuelflux.com', 'Om9523kum@r')
resp = requests.post(f'{BASE}/auth/login', json={'email': admin_creds[0], 'password': admin_creds[1]}, timeout=5)
print(f"Admin login: {resp.status_code}")
if resp.status_code == 200:
    token = resp.json().get('accessToken') or resp.json().get('access_token')
    headers = {'Authorization': f'Bearer {token}'}
    
    # Try admin pumps list or users list
    pumps_resp = requests.get(f'{BASE}/dashboard/pumps', headers=headers, timeout=10)
    print(f"Dashboard pumps status: {pumps_resp.status_code}")
    print(f"Response: {json.dumps(pumps_resp.json(), indent=2)[:1000]}")

# Also try the test user to see the pumps response structure  
test_resp = requests.post(f'{BASE}/auth/login', json={'email': 'test_api_checker@fuelflux.dev', 'password': 'Test@12345'}, timeout=5)
print(f"\nTest user login: {test_resp.status_code}")
if test_resp.status_code == 200:
    token2 = test_resp.json().get('accessToken') or test_resp.json().get('access_token')
    user_data = test_resp.json().get('user', {})
    print(f"User role: {user_data.get('role')} / roles: {user_data.get('roles')}")
    
    headers2 = {'Authorization': f'Bearer {token2}'}
    p_resp = requests.get(f'{BASE}/dashboard/pumps', headers=headers2, timeout=10)
    print(f"Pumps status: {p_resp.status_code}")
    print(f"Pumps raw response: {json.dumps(p_resp.json(), indent=2)[:1000]}")
