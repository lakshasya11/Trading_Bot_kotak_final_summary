import json
import requests

# Load existing users from PC3
with open('users.json', 'r') as f:
    users = json.load(f)

pc2_url = 'http://192.168.0.104:5004/api/sync-signup'

print(f"Found {len(users)} users to sync to PC2\n")

for user in users:
    signup_data = {
        'client_id': user['client_id'],
        'username': user['username'],
        'email': user['email'],
        'phone': user['mobile'],
        'aadhar': user['aadhar'],
        'qr_key': user['totp_secret']
    }
    
    print(f"Syncing: {user['username']} ({user['client_id']})...")
    
    try:
        response = requests.post(pc2_url, json=signup_data, timeout=5)
        if response.status_code in [200, 201]:
            print(f"  ✓ Success: {response.json()}")
        else:
            print(f"  ✗ Failed: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    
    print()

print("Sync complete!")
