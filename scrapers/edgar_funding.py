"""
Scraper: SEC EDGAR Form D Filings
Pulls recent Form D filings (equity/funding announcements) to identify
companies that recently raised capital — a timing signal for hiring needs.

This is a supplementary data source. The scoring engine uses it to add
a "recent_funding" bonus when a company appears in both EDGAR and a
hiring source.
"""

import re
import time
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
from bs4 import BeautifulSoup

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SCRAPER_COLUMNS, DATA_DIR


# ─── Constants ───────────────────────────────────────────────────────
SOURCE_NAME = "edgar_funding"
REQUEST_TIMEOUT = 30
RETRY_LIMIT = 3
RETRY_BACKOFF = 2

# SEC EDGAR FULL-TEXT SEARCH API (EFTS)
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

# Fallback: EDGAR full-text search (newer, more reliable)
EDGAR_FTS_URL = "https://efts.sec.gov/LATEST/search-index"

# SEC EDGAR company search (traditional, always works)
EDGAR_BROWSE_URL = "https://www.sec.gov/cgi-bin/browse-edgar"

# SEC requires a descriptive User-Agent
HEADERS = {
    "User-Agent": "ConnectorPipeline research@example.com",
    "Accept": "text/html, application/json",
}

AMOUNT_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion|M|B|k|K)?", re.IGNORECASE)


# ─── Helpers ─────────────────────────────────────────────────────────
def _get(url: str, params: dict = None) -> requests.Response | None:
    """GET with retries, returns Response object (not parsed)."""
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            resp = requests.get(
                url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
            )
            if resp.status_code == 429:
                wait = RETRY_BACKOFF * attempt
                print(f"  ⏳ Rate-limited by SEC, waiting {wait}s …")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt == RETRY_LIMIT:
                print(f"  ✗ Request failed after {RETRY_LIMIT} attempts: {exc}")
                return None
            time.sleep(RETRY_BACKOFF * attempt)
    return None


def _parse_amount(text: str):
    """Try to extract a dollar amount from text."""
    match = AMOUNT_RE.search(text)
    if not match:
        return ""
    value = float(match.group(1).replace(",", ""))
    suffix = (match.group(2) or "").lower()
    if suffix in ("million", "m"):
        value *= 1_000_000
    elif suffix in ("billion", "b"):
        value *= 1_000_000_000
    elif suffix in ("k",):
        value *= 1_000
    return int(value)


# ─── Strategy 1: EDGAR Full-Text Search API ─────────────────────────
def _fetch_via_efts(months_back: int = 6) -> list[dict]:
    """
    Use the EDGAR full-text search API to find recent Form D filings.
    """
    today = datetime.now(timezone.utc)
    start_date = (today - timedelta(days=months_back * 30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    print(f"  📥 Querying EDGAR EFTS for Form D filings ({start_date} to {end_date}) …")

    resp = _get("https://efts.sec.gov/LATEST/search-index", params={
        "q": "*",
        "forms": "D",
        "dateRange": "custom",
        "startdt": start_date,
        "enddt": end_date,
    })

    if resp and resp.status_code == 200:
        try:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            print(f"    ✓ EFTS returned {len(hits)} results")
            return _parse_efts_results(hits)
        except (ValueError, KeyError):
            print("    ⚠ Could not parse EFTS JSON response")
    return []


def _parse_efts_results(hits: list) -> list[dict]:
    """Parse EFTS search results into row dicts."""
    rows = []
    for hit in hits:
        source = hit.get("_source", {})
        company = source.get("display_names", [""])[0] if source.get("display_names") else ""
        if not company:
            company = source.get("entity_name", "")

        date_filed = source.get("file_date", "")
        days_open = -1
        if date_filed:
            try:
                dt = datetime.strptime(date_filed, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                days_open = (datetime.now(timezone.utc) - dt).days
            except ValueError:
                pass

        rows.append({
            "source": SOURCE_NAME,
            "company_name": company,
            "company_url": "",
            "role_title": "Form D Filing (Fundraise)",
            "location": "",
            "remote": "unknown",
            "date_posted": date_filed,
            "days_open": days_open,
            "salary_min": "",
            "salary_max": "",
            "contact_name": "",
            "contact_email": "",
            "tech_stack": "",
            "raw_text": str(source)[:1000],
        })
    return rows


# ─── Strategy 2: EDGAR Browse (HTML scraping fallback) ───────────────
def _fetch_via_browse(count: int = 100) -> list[dict]:
    """
    Fallback: scrape the traditional EDGAR company filing search.
    Fetches recent Form D filings via the browse-edgar endpoint.
    """
    print(f"  📥 Fetching {count} recent Form D filings via EDGAR browse …")

    resp = _get(EDGAR_BROWSE_URL, params={
        "action": "getcompany",
        "type": "D",
        "dateb": "",
        "owner": "include",
        "count": str(count),
        "search_text": "",
    })

    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    rows = []

    # EDGAR returns results in a table
    table = soup.select_one("table.tableFile2")
    if not table:
        # Try alternate table format
        tables = soup.select("table")
        for t in tables:
            if "company" in t.get_text().lower() and "filed" in t.get_text().lower():
                table = t
                break

    if not table:
        print("    ⚠ Could not find results table in EDGAR response")
        return []

    trs = table.select("tr")[1:]  # skip header
    print(f"    Found {len(trs)} filing rows")

    for tr in trs:
        tds = tr.select("td")
        if len(tds) < 4:
            continue

        # Typical columns: CIK, Company Name, Form Type, Date Filed, ...
        company_name = ""
        date_filed = ""
        filing_url = ""

        # Try to find company name (usually 2nd column with a link)
        for td in tds:
            link = td.select_one("a")
            text = td.get_text(strip=True)

            if link and "company" in (link.get("href", "").lower()):
                company_name = text
            elif re.match(r"\d{4}-\d{2}-\d{2}", text):
                date_filed = text
            elif link and "Archives" in (link.get("href", "")):
                filing_url = "https://www.sec.gov" + link["href"]

        if not company_name:
            # Fallback: grab text from second td
            if len(tds) >= 2:
                company_name = tds[1].get_text(strip=True)

        days_open = -1
        if date_filed:
            try:
                dt = datetime.strptime(date_filed, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                days_open = (datetime.now(timezone.utc) - dt).days
            except ValueError:
                pass

        rows.append({
            "source": SOURCE_NAME,
            "company_name": company_name,
            "company_url": filing_url,
            "role_title": "Form D Filing (Fundraise)",
            "location": "",
            "remote": "unknown",
            "date_posted": date_filed,
            "days_open": days_open,
            "salary_min": "",
            "salary_max": "",
            "contact_name": "",
            "contact_email": "",
            "tech_stack": "",
            "raw_text": tr.get_text(separator=" ", strip=True)[:500],
        })

    return rows


# ─── Strategy 3: EDGAR Full-Text Search (newer endpoint) ────────────
def _fetch_via_fulltext(months_back: int = 6) -> list[dict]:
    """
    Use the newer EDGAR full-text search at efts.sec.gov/LATEST/search-index.
    This is the most reliable modern endpoint.
    """
    today = datetime.now(timezone.utc)
    start_date = (today - timedelta(days=months_back * 30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    print(f"  📥 Trying EDGAR full-text search ({start_date} to {end_date}) …")

    resp = _get("https://efts.sec.gov/LATEST/search-index", params={
        "q": '"form d"',
        "dateRange": "custom",
        "startdt": start_date,
        "enddt": end_date,
        "forms": "D",
    })

    if resp and resp.status_code == 200:
        try:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            print(f"    ✓ Full-text search returned {len(hits)} results")
            return _parse_efts_results(hits)
        except (ValueError, KeyError):
            print("    ⚠ Could not parse full-text search response")
    return []


# ─── Main ────────────────────────────────────────────────────────────
def main() -> pd.DataFrame:
    print("=" * 60)
    print("SEC EDGAR Form D Scraper (Funding Signal)")
    print("=" * 60)

    rows = []

    # Try the EFTS API first (most structured)
    rows = _fetch_via_efts(months_back=6)

    # Fallback to browse endpoint if EFTS didn't work
    if not rows:
        print("\n  Falling back to EDGAR browse endpoint …")
        rows = _fetch_via_browse(count=100)

    # Last resort: full-text search
    if not rows:
        print("\n  Falling back to EDGAR full-text search …")
        rows = _fetch_via_fulltext(months_back=6)

    if not rows:
        print("\n⚠ Could not retrieve any Form D filings from any endpoint.")
        print("  This may be due to SEC rate limiting. Try again later.")
        return pd.DataFrame(columns=SCRAPER_COLUMNS)

    df = pd.DataFrame(rows, columns=SCRAPER_COLUMNS)

    # Deduplicate by company name (keep most recent filing)
    before = len(df)
    df.sort_values("date_posted", ascending=False, inplace=True)
    df.drop_duplicates(subset=["company_name"], keep="first", inplace=True)
    dupes = before - len(df)
    if dupes:
        print(f"\n  🔄 Removed {dupes} duplicate company filings (kept most recent)")

    # ── Save ──
    out_path = os.path.join(DATA_DIR, "edgar_funding.csv")
    df.to_csv(out_path, index=False)
    print(f"\n✅ Saved {len(df)} rows → {out_path}")
    print(f"   Unique companies with recent Form D filings: {df['company_name'].nunique()}")
    print(f"   Date range: {df['date_posted'].min()} to {df['date_posted'].max()}")

    return df


if __name__ == "__main__":
    main()
