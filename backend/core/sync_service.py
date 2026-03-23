import os
import requests
from dotenv import load_dotenv

# Load .env from backend directory
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

CENTRAL_API_URL = os.getenv('CENTRAL_API_URL', 'http://192.168.0.102:5004')
API_KEY = os.getenv('API_KEY', '')

_HEADERS = {
    "Content-Type": "application/json",
    "X-API-KEY": API_KEY,
    "Authorization": f"Bearer {API_KEY}"
}


class ClientBotSync:
    """Syncs session data from PC3 (Client Bot) to PC2 (Central DB) via HTTP."""

    def sync_session_to_central(self, session_payload: dict):
        """POST session data to PC2's /api/sync-session endpoint."""
        url = f"{CENTRAL_API_URL}/api/sync-session"
        try:
            response = requests.post(url, json=session_payload, headers=_HEADERS, timeout=5)
            if response.status_code in (200, 201):
                print(f"[Sync] ✓ Session synced: {session_payload.get('client_id')} - {session_payload.get('mode')}")
            else:
                print(f"[Sync] ✗ Sync failed ({response.status_code}): {response.text}")
        except requests.exceptions.ConnectionError:
            print(f"[Sync] ✗ Cannot reach Central DB at {CENTRAL_API_URL} — is PC2 running?")
        except requests.exceptions.Timeout:
            print(f"[Sync] ✗ Timeout syncing to {CENTRAL_API_URL}")
        except Exception as e:
            print(f"[Sync] ✗ Error: {e}")
