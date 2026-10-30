"""Interactive daily Kite Connect login. Run locally; never paste tokens into chat."""
import os
import json
import getpass
from pathlib import Path
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()
api_key = os.getenv("KITE_API_KEY", "").strip()
api_secret = os.getenv("KITE_API_SECRET", "").strip()
if not api_key or not api_secret:
    raise SystemExit("Set KITE_API_KEY and KITE_API_SECRET in .env first.")

kite = KiteConnect(api_key=api_key)
print("\nOpen this URL and complete Kite login:\n")
print(kite.login_url())
print("\nAfter login, copy ONLY the request_token query parameter from the redirect URL.")
request_token = getpass.getpass("Paste request_token here (input hidden): ").strip()
if not request_token:
    raise SystemExit("No request_token provided.")

session = kite.generate_session(request_token, api_secret=api_secret)
token_path = Path("token.json")
token_path.write_text(json.dumps({
    "access_token": session["access_token"],
    "created_at": session.get("login_time"),
}, indent=2), encoding="utf-8")
try:
    os.chmod(token_path, 0o600)
except OSError:
    pass
print("Access token saved to token.json with restricted permissions where supported.")
print("It expires daily; rerun `python auth.py` after expiry.")
