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

r = requests.get(f"{BASE}/campaigns/{cid}/replies", headers=headers, params={"page_size": 50})
replies = r.json()

ooo_count = 0
bounce_count = 0
interested_count = 0
other_count = 0

for reply in replies.get("data", []):
    email = reply.get("contact_email", "?")
    name = reply.get("contact_name", "?")
    subject = reply.get("subject", "?")
    sentiment = reply.get("sentiment_category", "?")
    is_ooo = reply.get("is_out_of_office", False)
    is_interested = reply.get("is_interested", False)
    status = reply.get("lead_status", "?")
    content = (reply.get("content", "") or "")[:400]
    from_email = reply.get("from_email", "?")

    print("=" * 60)
    print(f"From: {name} <{email}>")
    print(f"Reply-from: {from_email}")
    print(f"Subject: {subject}")
    print(f"Sentiment: {sentiment}")
    print(f"OOO: {is_ooo} | Interested: {is_interested} | Status: {status}")
    print("Content preview:")
    print(content)
    print()

    if is_ooo:
        ooo_count += 1
    elif "bounce" in str(sentiment).lower() or "mailer-daemon" in str(from_email).lower() or "undeliverable" in str(subject).lower():
        bounce_count += 1
    elif is_interested:
        interested_count += 1
    else:
        other_count += 1

total = len(replies.get("data", []))
print("=" * 60)
print("\nSUMMARY")
print(f"Total replies: {total}")
print(f"Out of Office: {ooo_count}")
print(f"Bounces/NDR: {bounce_count}")
print(f"Interested: {interested_count}")
print(f"Other: {other_count}")
