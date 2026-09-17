import sys, requests, json

BASE = 'http://127.0.0.1:8000/api/v1'

# Login
resp = requests.post(f'{BASE}/auth/login', json={'email': 'owner@fuelflux.com', 'password': 'password123'}, timeout=30)
if resp.status_code != 200:
    print("Login failed")
    sys.exit(1)

token = resp.json().get('accessToken') or resp.json().get('access_token')
headers = {'Authorization': f'Bearer {token}'}

# Get current subscription status
sub_resp = requests.get(f'{BASE}/subscriptions/my-subscription?pump_id=3', headers=headers)
print("My subscription response status:", sub_resp.status_code)
print(json.dumps(sub_resp.json(), indent=2))

# Get plans list
plans_resp = requests.get(f'{BASE}/subscriptions/plans', headers=headers)
print("Plans response status:", plans_resp.status_code)
plans = plans_resp.json().get('data', {}).get('plans', [])
if not plans:
    print("No plans available to test")
    sys.exit(1)
plan_id = plans[0]['id']
print(f"Testing with Plan ID: {plan_id} ({plans[0]['name']})")

# Create Order
order_resp = requests.post(
    f'{BASE}/subscriptions/create-order',
    json={'pump_id': 3, 'plan_id': plan_id, 'billing_cycle': 'monthly'},
    headers=headers
)
print("Create order response status:", order_resp.status_code)
order_data = order_resp.json()
print(json.dumps(order_data, indent=2))

# Verify Payment
# We will simulate verifying payment. If it's real, we pass mock signature as if we are in dev/mock.
# Wait! In verify_razorpay_payment:
# is_mock = order_id.startswith("mock_order_")
# Let's see what happens.
order_info = order_data.get('data', {})
order_id = order_info.get('order_id')
is_mock = order_data.get('mock', False)

verify_payload = {
    'razorpay_order_id': order_id,
    'razorpay_payment_id': 'mock_pay_1234567890',
    'razorpay_signature': 'mock_sig',
    'pump_id': 3,
    'plan_id': plan_id,
    'billing_cycle': 'monthly'
}

# If it's a real order, we want signature verification to be bypassed or test it.
# Wait, for testing, we can simulate verification. Let's send the request.
verify_resp = requests.post(
    f'{BASE}/subscriptions/verify-payment',
    json=verify_payload,
    headers=headers
)
print("Verify payment response status:", verify_resp.status_code)
print(json.dumps(verify_resp.json(), indent=2))
