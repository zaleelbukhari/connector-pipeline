"""
Scraper: RemoteOK (remoteok.com)
Uses the public JSON API to pull all current remote job listings,
then filters to engineering roles.
"""

import re
import time
from datetime import datetime, timezone

import requests
import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SCRAPER_COLUMNS, ENGINEERING_ROLE_KEYWORDS, TECH_STACK_KEYWORDS, DATA_DIR


# ─── Constants ───────────────────────────────────────────────────────
SOURCE_NAME = "remoteok"
API_URL     = "https://remoteok.com/api"
REQUEST_TIMEOUT = 30
RETRY_LIMIT = 3
RETRY_BACKOFF = 3

SALARY_RE = re.compile(r"(\d[\d,]*)")
EMAIL_RE  = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}")


# ─── Helpers ─────────────────────────────────────────────────────────
def _fetch_jobs() -> list[dict]:
    """Fetch all jobs from the RemoteOK API with retries."""
    headers = {
        "User-Agent": "ConnectorPipeline/1.0 (research scraper; contact: research@example.com)",
        "Accept": "application/json",
    }
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            print(f"  📥 Fetching RemoteOK API (attempt {attempt}) …")
            resp = requests.get(API_URL, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                wait = RETRY_BACKOFF * attempt
                print(f"  ⏳ Rate-limited, waiting {wait}s …")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            # The API returns a legal notice as the first element
            if isinstance(data, list) and len(data) > 0:
                if isinstance(data[0], dict) and "legal" in str(data[0]).lower():
                    data = data[1:]
            return data
        except requests.RequestException as exc:
            if attempt == RETRY_LIMIT:
                print(f"  ✗ Failed after {RETRY_LIMIT} attempts: {exc}")
                return []
            time.sleep(RETRY_BACKOFF * attempt)
    return []


def _is_engineering_role(job: dict) -> bool:
    """Check if the job matches any engineering role keyword."""
    text = " ".join([
        job.get("position", ""),
        job.get("description", ""),
        " ".join(job.get("tags", [])),
    ]).lower()
    return any(kw in text for kw in ENGINEERING_ROLE_KEYWORDS)


def _find_tech_stack(job: dict) -> list[str]:
    """Extract tech stack keywords from the job."""
    text = " ".join([
        job.get("description", ""),
        " ".join(job.get("tags", [])),
    ]).lower()
    return sorted(set(kw for kw in TECH_STACK_KEYWORDS if kw in text))


def _parse_salary(job: dict):
    """Extract salary range. RemoteOK sometimes has salary_min/salary_max fields."""
    sal_min = job.get("salary_min", "")
    sal_max = job.get("salary_max", "")
    # Clean up
    if sal_min:
        try:
            sal_min = int(str(sal_min).replace(",", ""))
        except (ValueError, TypeError):
            sal_min = ""
    if sal_max:
        try:
            sal_max = int(str(sal_max).replace(",", ""))
        except (ValueError, TypeError):
            sal_max = ""
    return sal_min, sal_max


def _parse_job(job: dict) -> dict:
    """Convert a RemoteOK job dict to our standard row format."""
    # Date handling
    date_posted = ""
    days_open = -1
    raw_date = job.get("date", "")
    if raw_date:
        try:
            # RemoteOK dates look like "2026-06-10T00:00:00+00:00" or ISO format
            posted_dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            date_posted = posted_dt.strftime("%Y-%m-%d")
            days_open = (datetime.now(timezone.utc) - posted_dt).days
        except (ValueError, TypeError):
            pass

    sal_min, sal_max = _parse_salary(job)
    stack = _find_tech_stack(job)

    # Build job URL
    slug = job.get("slug", "")
    job_url = job.get("url", "")
    if not job_url and slug:
        job_url = f"https://remoteok.com/remote-jobs/{slug}"

    # Company URL
    company_url = job.get("company_url", "") or job.get("apply_url", "") or ""

    # Location — RemoteOK jobs are remote by definition
    location = job.get("location", "Worldwide") or "Worldwide"

    # Description for raw text
    description = job.get("description", "")
    # Try to find emails in description
    emails = EMAIL_RE.findall(description)
    contact_email = emails[0] if emails else ""

    return {
        "source": SOURCE_NAME,
        "company_name": job.get("company", ""),
        "company_url": company_url,
        "role_title": job.get("position", ""),
        "location": location,
        "remote": "remote",  # all RemoteOK jobs are remote
        "date_posted": date_posted,
        "days_open": days_open,
        "salary_min": sal_min,
        "salary_max": sal_max,
        "contact_name": "",
        "contact_email": contact_email,
        "tech_stack": ", ".join(stack),
        "raw_text": description[:2000],
    }


# ─── Main ────────────────────────────────────────────────────────────
def main() -> pd.DataFrame:
    print("=" * 60)
    print("RemoteOK Scraper")
    print("=" * 60)

    jobs = _fetch_jobs()
    if not jobs:
        print("No jobs returned from API. Returning empty DataFrame.")
        return pd.DataFrame(columns=SCRAPER_COLUMNS)

    print(f"  ✓ {len(jobs)} total listings from API")

    # Filter to engineering roles
    engineering_jobs = [j for j in jobs if _is_engineering_role(j)]
    print(f"  🔧 {len(engineering_jobs)} match engineering role keywords")

    rows = [_parse_job(j) for j in engineering_jobs]

    if not rows:
        print("\n⚠ No matching engineering listings found.")
        return pd.DataFrame(columns=SCRAPER_COLUMNS)

    df = pd.DataFrame(rows, columns=SCRAPER_COLUMNS)

    # ── Save ──
    out_path = os.path.join(DATA_DIR, "remoteok.csv")
    df.to_csv(out_path, index=False)
    print(f"\n✅ Saved {len(df)} rows → {out_path}")
    print(f"   Unique companies: {df['company_name'].nunique()}")

    # Quick stats
    with_salary = df[(df["salary_min"] != "") & (df["salary_min"].notna())].shape[0]
    print(f"   Listings with salary data: {with_salary}")

    return df


if __name__ == "__main__":
    main()
