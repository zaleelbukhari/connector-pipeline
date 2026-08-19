import os
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.environ.get("ARCSEND_BASE_URL", "https://app.arcsend.io/api/v1/external")
token = os.environ.get("ARCSEND_API_KEY")
cid = os.environ.get("ARCSEND_CAMPAIGN_ID")
if not token or not cid:
    sys.exit("Set ARCSEND_API_KEY and ARCSEND_CAMPAIGN_ID in your environment.")

headers = {"Authorization": f"Bearer {token}"}

r = requests.get(f"{BASE}/campaigns/{cid}/leads", headers=headers, params={"page_size": 10})
leads = r.json()

for lead in leads.get("data", []):
    print("=" * 60)
    email = lead.get("email", "?")
    first = lead.get("first_name", "?")
    last = lead.get("last_name", "?")
    company = lead.get("company", "?")
    status = lead.get("status", "?")
    cf = lead.get("custom_fields", {}) or {}
    body = cf.get("body", "NO BODY FIELD")

    print(f"Email: {email}")
    print(f"Name: {first} {last}")
    print(f"Company: {company}")
    print(f"Status: {status}")
    print(f"Body: {body}")
    print()
