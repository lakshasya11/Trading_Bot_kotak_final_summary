import json
import os
import time
import threading
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

class DatabaseSync:
    def __init__(self):
        # Use env var or fallback, but allow overriding the hardcoded 104
        # The user wants PC2 (102).
        base_url = os.getenv('CENTRAL_API_URL', 'http://43.204.210.211:5004')
        self.pc2_url = f"{base_url}/api/sync-signup"
        self.pending_file = 'pending_sync.json'
        # Start in a separate thread so we don't block import
        self.start_thread = threading.Thread(target=self.start_background_sync, daemon=True)
        self.start_thread.start()
    
    def store_signup_data(self, signup_data):
        """Queue signup data for remote sync to PC2"""
        try:
            print(f"✓ Queuing signup for sync: {signup_data['username']}")
            self.queue_for_sync('signup', signup_data)
            return True
        except Exception as e:
            print(f"Failed to queue signup: {e}")
            return False
    
    def queue_for_sync(self, data_type, data):
        """Add data to sync queue"""
        pending = self.load_pending()
        pending.append({
            'type': data_type,
            'data': data,
            'timestamp': datetime.now().isoformat()
        })
        self.save_pending(pending)
    
    def load_pending(self):
        """Load pending sync data"""
        if os.path.exists(self.pending_file):
            with open(self.pending_file, 'r') as f:
                return json.load(f)
        return []
    
    def save_pending(self, pending):
        """Save pending sync data"""
        with open(self.pending_file, 'w') as f:
            json.dump(pending, f, indent=2)
    
    def sync_to_remote(self):
        """Sync pending data to PC2 via HTTP API"""
        pending = self.load_pending()
        if not pending:
            return
            
        # Add headers for authentication
        api_key = os.getenv("API_KEY", "")
        headers = {
            "Content-Type": "application/json",
            "X-API-KEY": api_key,
            "Authorization": f"Bearer {api_key}"
        }
        
        synced = []
        for item in pending:
            if item['type'] == 'signup':
                try:
                    # Use self.pc2_url which now points to correct IP
                    response = requests.post(self.pc2_url, json=item['data'], headers=headers, timeout=5)
                    if response.status_code in [200, 201]:
                        print(f"✓ Synced to PC2: {item['data']['username']}")
                        synced.append(item)
                    else:
                        print(f"✗ Sync failed: {response.status_code} - {response.text}")
                except Exception as e:
                    print(f"✗ HTTP sync error: {e}")
        
        # Remove synced items
        if synced:
            remaining = [item for item in pending if item not in synced]
            self.save_pending(remaining)
            print(f"✓ Synced {len(synced)} items, {len(remaining)} pending")

    def start_background_sync(self):
        """Start background sync loop (run in thread)"""
        while True:
            try:
                self.sync_to_remote()
            except Exception as e:
                print(f"Sync loop error: {e}")
            time.sleep(10)  # Try sync every 10 seconds

# Global sync instance
db_sync = DatabaseSync()
