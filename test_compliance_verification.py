import sys
import os
import requests
import json

sys.path.insert(0, '.')

BASE = 'http://127.0.0.1:8000/api/v1'

credentials_to_try = [
    ('testowner@gmail.com', 'password123'),
    ('owner@fuelflux.com', 'password123'),
    ('Om9523kumar@gmail.com', 'password123'),
    ('test_api_checker@fuelflux.dev', 'Test@12345'),
]

token = None
for email, pwd in credentials_to_try:
    print(f"Trying login for {email}...")
    resp = requests.post(f'{BASE}/auth/login', json={'email': email, 'password': pwd}, timeout=5)
    if resp.status_code == 200:
        token = resp.json().get('accessToken') or resp.json().get('access_token')
        print(f"Login successful for {email}!")
        break

if not token:
    print("Could not login with any test credentials.")
    sys.exit(1)

headers = {'Authorization': f'Bearer {token}'}

# Get pump ID
p_resp = requests.get(f'{BASE}/dashboard/pumps', headers=headers, timeout=10)
if p_resp.status_code != 200 or not p_resp.json():
    print(f"Failed to fetch pumps: {p_resp.status_code}")
    sys.exit(1)

pumps_data = p_resp.json()
if isinstance(pumps_data, list):
    pumps = pumps_data
else:
    pumps = pumps_data.get('pumps', [])

if not pumps:
    print(f"No pumps found for this user.")
    import sys; sys.exit(1)

pump_id = pumps[0]['id']
print(f"Selected Pump ID: {pump_id}")

# Fetch document types to get one doc_type_id
t_resp = requests.get(f'{BASE}/compliance/types?pump_id={pump_id}', headers=headers, timeout=10)
if t_resp.status_code != 200 or not t_resp.json():
    print(f"Failed to fetch document types: {t_resp.status_code}")
    sys.exit(1)

doc_type = t_resp.json()[0]
doc_type_id = doc_type.get('id') or doc_type.get('_id')
print(f"Selected Doc Type: {doc_type['name']} (ID: {doc_type_id})")

# Create a temporary dummy file to upload
dummy_file_path = 'dummy_cert.pdf'
with open(dummy_file_path, 'wb') as f:
    f.write(b'%PDF-1.4 dummy pdf content for testing')

try:
    # Upload new certificate (should start as pending_verification)
    print("\nUploading new document...")
    files = {
        'file': (os.path.basename(dummy_file_path), open(dummy_file_path, 'rb'), 'application/pdf')
    }
    data = {
        'pump_id': pump_id,
        'doc_type_id': doc_type_id,
        'certificate_number': 'TEST-VERIFY-123',
        'issuing_authority': 'Test Authority',
        'issue_date': '2026-01-01T00:00:00Z',
        'expiry_date': '2027-01-01T00:00:00Z'
    }
    
    upload_resp = requests.post(f'{BASE}/compliance/documents/upload', headers=headers, data=data, files=files, timeout=10)
    if upload_resp.status_code != 200:
        print(f"Upload failed: {upload_resp.status_code} {upload_resp.text}")
        sys.exit(1)
        
    doc = upload_resp.json()
    doc_id = doc['id']
    print(f"Uploaded successfully! Doc ID: {doc_id}, Initial Status: {doc.get('status')}")
    assert doc.get('status') == 'pending_verification', "Expected initial status to be pending_verification"

    # Fetch stats and confirm it is unverified (pending_verification == 1)
    stats_resp = requests.get(f'{BASE}/compliance/dashboard?pump_id={pump_id}', headers=headers, timeout=10)
    print(f"\nStats before verification: {stats_resp.json()}")
    stats = stats_resp.json()
    assert stats.get('pending_verification', 0) >= 1, "Expected pending_verification count to be >= 1"

    # Verify the document (Approve it)
    print(f"\nApproving document {doc_id}...")
    verify_resp = requests.patch(
        f'{BASE}/compliance/documents/{doc_id}/verify',
        headers=headers,
        json={'status': 'verified'},
        timeout=10
    )
    if verify_resp.status_code != 200:
        print(f"Verification approval failed: {verify_resp.status_code} {verify_resp.text}")
        sys.exit(1)
    print(f"Approved! Response: {verify_resp.json()}")
    assert verify_resp.json().get('status') != 'pending_verification', "Expected status to update"

    # Fetch stats and confirm it is now verified
    stats_resp = requests.get(f'{BASE}/compliance/dashboard?pump_id={pump_id}', headers=headers, timeout=10)
    print(f"\nStats after verification: {stats_resp.json()}")
    stats_after = stats_resp.json()
    assert stats_after.get('active', 0) >= 1, "Expected active count to increase"

    # Reject the document
    print(f"\nRejecting document {doc_id}...")
    reject_resp = requests.patch(
        f'{BASE}/compliance/documents/{doc_id}/verify',
        headers=headers,
        json={'status': 'rejected', 'rejection_reason': 'Invalid seal'},
        timeout=10
    )
    if reject_resp.status_code != 200:
        print(f"Verification rejection failed: {reject_resp.status_code} {reject_resp.text}")
        sys.exit(1)
    print(f"Rejected! Response: {reject_resp.json()}")
    assert reject_resp.json().get('status') == 'rejected', "Expected status to be rejected"

    # Clean up document
    print("\nDeleting document...")
    del_resp = requests.delete(f'{BASE}/compliance/documents/{doc_id}', headers=headers, timeout=10)
    print(f"Delete response: {del_resp.status_code}")

finally:
    if os.path.exists(dummy_file_path):
        os.remove(dummy_file_path)

print("\nAll compliance verification flow API tests PASSED successfully! 🎉")
