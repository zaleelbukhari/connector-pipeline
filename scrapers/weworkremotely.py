"""
Scraper: We Work Remotely (weworkremotely.com)
Scrapes the Programming and DevOps/SysAdmin job categories.
Parses HTML listings and filters for engineering roles.
"""

import re
import time
from datetime import datetime, timezone

import requests
import pandas as pd
from bs4 import BeautifulSoup

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SCRAPER_COLUMNS, ENGINEERING_ROLE_KEYWORDS, TECH_STACK_KEYWORDS, DATA_DIR


# ─── Constants ───────────────────────────────────────────────────────
SOURCE_NAME = "weworkremotely"
BASE_URL    = "https://weworkremotely.com"
CATEGORIES  = [
    ("Programming", "/categories/remote-programming-jobs"),
    ("DevOps/SysAdmin", "/categories/remote-devops-sysadmin-jobs"),
]
REQUEST_TIMEOUT = 30
RETRY_LIMIT = 3
RETRY_BACKOFF = 2

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}")


# ─── Helpers ─────────────────────────────────────────────────────────
def _get_page(url: str) -> BeautifulSoup | None:
    """Fetch a page with retries, return parsed soup."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ConnectorPipeline/1.0)",
        "Accept": "text/html",
    }
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                wait = RETRY_BACKOFF * attempt
                print(f"  ⏳ Rate-limited, waiting {wait}s …")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except requests.RequestException as exc:
            if attempt == RETRY_LIMIT:
                print(f"  ✗ Failed after {RETRY_LIMIT} attempts: {exc}")
                return None
            time.sleep(RETRY_BACKOFF * attempt)
    return None


def _is_engineering_role(title: str) -> bool:
    """Check if the role title matches any engineering keyword."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in ENGINEERING_ROLE_KEYWORDS)


def _find_tech_stack(text: str) -> list[str]:
    """Extract tech stack keywords from text."""
    text_lower = text.lower()
    return sorted(set(kw for kw in TECH_STACK_KEYWORDS if kw in text_lower))


def _parse_date(date_str: str) -> tuple[str, int]:
    """Try to parse a date string, return (iso_date, days_open)."""
    if not date_str:
        return "", -1

    # WWR uses relative dates like "2d", "1w", "3w" or datetime attributes
    # Try ISO format first
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%b %d, %Y"]:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            dt = dt.replace(tzinfo=timezone.utc)
            date_posted = dt.strftime("%Y-%m-%d")
            days_open = (datetime.now(timezone.utc) - dt).days
            return date_posted, days_open
        except ValueError:
            continue
    return "", -1


def _scrape_category(category_name: str, path: str) -> list[dict]:
    """Scrape a single WWR category page for job listings."""
    url = BASE_URL + path
    print(f"\n  📥 Scraping: {category_name} ({url})")
    soup = _get_page(url)
    if not soup:
        return []

    rows = []

    # WWR lists jobs in <li> elements within <ul> with class "jobs"
    # Each job link is inside an <li> that contains <a> with job details
    job_sections = soup.select("section.jobs article ul li")
    if not job_sections:
        # Fallback: try alternative selectors
        job_sections = soup.select("li.feature, li.new-listing")
    if not job_sections:
        # Another fallback
        job_sections = soup.select("article ul li")

    print(f"    Found {len(job_sections)} raw listing elements")

    for li in job_sections:
        # Find the main job link
        link = li.select_one("a")
        if not link:
            continue

        href = link.get("href", "")
        if not href or href == "#":
            continue

        # Build full URL
        if href.startswith("/"):
            job_url = BASE_URL + href
        elif href.startswith("http"):
            job_url = href
        else:
            continue

        # Extract company name
        company_el = li.select_one(".company") or li.select_one("span.company")
        company_name = company_el.get_text(strip=True) if company_el else ""

        # Extract role title
        title_el = li.select_one(".title") or li.select_one("span.title")
        role_title = title_el.get_text(strip=True) if title_el else ""

        # If we couldn't find structured elements, try link text
        if not role_title:
            role_title = link.get_text(strip=True)

        # Skip non-engineering roles
        if not _is_engineering_role(role_title):
            continue

        # Extract region/location
        region_el = li.select_one(".region") or li.select_one("span.region")
        location = region_el.get_text(strip=True) if region_el else ""

        # Date — look for datetime attribute or text
        date_el = li.select_one("time") or li.select_one(".date")
        date_str = ""
        if date_el:
            date_str = date_el.get("datetime", "") or date_el.get_text(strip=True)
        date_posted, days_open = _parse_date(date_str)

        # Collect raw text from the listing
        raw_text = li.get_text(separator=" ", strip=True)[:1000]
        stack = _find_tech_stack(raw_text)

        rows.append({
            "source": SOURCE_NAME,
            "company_name": company_name,
            "company_url": job_url,
            "role_title": role_title,
            "location": location if location else "Remote",
            "remote": "remote",  # all WWR jobs are remote
            "date_posted": date_posted,
            "days_open": days_open,
            "salary_min": "",
            "salary_max": "",
            "contact_name": "",
            "contact_email": "",
            "tech_stack": ", ".join(stack),
            "raw_text": raw_text,
        })

    print(f"    ✓ {len(rows)} engineering roles extracted")
    return rows


def _scrape_job_detail(url: str) -> dict:
    """
    Optionally scrape a job detail page for more info (salary, contact, stack).
    Returns a dict of additional fields.
    """
    soup = _get_page(url)
    if not soup:
        return {}

    info = {}
    # Try to find the full description
    desc_el = soup.select_one(".listing-container") or soup.select_one(".job-listing")
    if desc_el:
        text = desc_el.get_text(separator=" ", strip=True)
        # Emails
        emails = EMAIL_RE.findall(text)
        if emails:
            info["contact_email"] = emails[0]
        # Tech stack
        stack = _find_tech_stack(text)
        if stack:
            info["tech_stack"] = ", ".join(stack)
        info["raw_text"] = text[:2000]

    return info


# ─── Main ────────────────────────────────────────────────────────────
def main() -> pd.DataFrame:
    print("=" * 60)
    print("We Work Remotely Scraper")
    print("=" * 60)

    all_rows: list[dict] = []

    for cat_name, cat_path in CATEGORIES:
        rows = _scrape_category(cat_name, cat_path)
        all_rows.extend(rows)
        time.sleep(1)  # polite delay between categories

    if not all_rows:
        print("\n⚠ No matching engineering listings found.")
        return pd.DataFrame(columns=SCRAPER_COLUMNS)

    # Deduplicate by (company_name, role_title)
    df = pd.DataFrame(all_rows, columns=SCRAPER_COLUMNS)
    before = len(df)
    df.drop_duplicates(subset=["company_name", "role_title"], keep="first", inplace=True)
    dupes = before - len(df)
    if dupes:
        print(f"\n  🔄 Removed {dupes} duplicate listings")

    # ── Save ──
    out_path = os.path.join(DATA_DIR, "weworkremotely.csv")
    df.to_csv(out_path, index=False)
    print(f"\n✅ Saved {len(df)} rows → {out_path}")
    print(f"   Unique companies: {df['company_name'].nunique()}")

    return df


if __name__ == "__main__":
    main()
