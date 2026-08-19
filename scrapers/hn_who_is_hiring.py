"""
Scraper: Hacker News "Who is hiring?" threads
Uses the Algolia HN API to find recent monthly threads,
then parses every top-level comment for company / role / contact info.

Companies appearing in MULTIPLE monthly threads are flagged — that's a
strong signal they're struggling to fill the role.
"""

import re
import json
import time
import html
from datetime import datetime, timezone

import requests
import pandas as pd

# ── Append parent dir so `config` is importable when run standalone ──
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SCRAPER_COLUMNS, ENGINEERING_ROLE_KEYWORDS, TECH_STACK_KEYWORDS, DATA_DIR


# ─── Constants ───────────────────────────────────────────────────────
SOURCE_NAME = "hn_who_is_hiring"
SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL   = "https://hn.algolia.com/api/v1/items/{}"
MONTHS_BACK = 3          # how many monthly threads to pull
REQUEST_TIMEOUT = 30     # seconds
RETRY_LIMIT = 3
RETRY_BACKOFF = 2        # seconds between retries

# Pre-compile once
EMAIL_RE    = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}")
URL_RE      = re.compile(r"https?://[^\s<>\"',;)}\]]+")
SALARY_RE   = re.compile(
    r"\$\s?([\d,]+)\s*[kK]?\s*[-–—to]+\s*\$?\s*([\d,]+)\s*[kK]?", re.IGNORECASE
)

# US states (abbreviations) for location matching
US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY","DC",
}

US_LOCATION_RE = re.compile(
    r"""
    (?:United\s+States|USA|U\.S\.A\.|U\.S\.)
    |(?:San\s+Francisco|New\s+York|Los\s+Angeles|Seattle|Austin|Boston
       |Chicago|Denver|Miami|Portland|Atlanta|Dallas|Houston|Phoenix
       |San\s+Diego|San\s+Jose|Raleigh|Nashville|Minneapolis|Pittsburgh
       |Philadelphia|Charlotte|Salt\s+Lake|Washington\s*,?\s*D\.?C\.?)
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ─── Helpers ─────────────────────────────────────────────────────────
def _get(url, params=None):
    """GET with retries and back-off."""
    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                wait = RETRY_BACKOFF * attempt
                print(f"  ⏳ Rate-limited, waiting {wait}s (attempt {attempt})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == RETRY_LIMIT:
                print(f"  ✗ Request failed after {RETRY_LIMIT} attempts: {exc}")
                return None
            time.sleep(RETRY_BACKOFF * attempt)
    return None


def _clean_html(raw: str) -> str:
    """Strip HTML tags, decode entities, collapse whitespace."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_header_parts(first_line: str):
    """
    HN hiring comments typically start with:
        Company Name | Location | Remote | URL
    Split on '|' and return dict with what we can identify.
    """
    parts = [p.strip() for p in first_line.split("|")]
    info = {"company_name": "", "location": "", "remote": "unknown", "company_url": ""}

    if parts:
        info["company_name"] = parts[0]

    for part in parts[1:]:
        lower = part.lower()
        # Remote detection
        if "remote" in lower:
            info["remote"] = "remote"
        elif "hybrid" in lower:
            info["remote"] = "hybrid"
        elif "onsite" in lower or "on-site" in lower or "in-office" in lower:
            info["remote"] = "onsite"

        # URL detection
        if part.startswith("http://") or part.startswith("https://"):
            info["company_url"] = part
        # Location-ish (contains comma or US state abbreviation)
        elif re.search(r",\s*[A-Z]{2}\b", part):
            info["location"] = part
        elif any(city.lower() in lower for city in [
            "san francisco","new york","los angeles","seattle","austin",
            "boston","chicago","denver","miami","portland","atlanta",
        ]):
            info["location"] = part

    return info


def _find_role_titles(text: str) -> list[str]:
    """Return engineering role keywords found in the text."""
    text_lower = text.lower()
    return [kw for kw in ENGINEERING_ROLE_KEYWORDS if kw in text_lower]


def _find_tech_stack(text: str) -> list[str]:
    """Return known tech stack keywords found in the text."""
    text_lower = text.lower()
    return [kw for kw in TECH_STACK_KEYWORDS if kw in text_lower]


def _extract_salary(text: str):
    """Try to pull salary range from text. Returns (min, max) or (None, None)."""
    match = SALARY_RE.search(text)
    if not match:
        return None, None
    lo = int(match.group(1).replace(",", ""))
    hi = int(match.group(2).replace(",", ""))
    # If values look like they're in K (e.g. "$120k - $180k")
    if lo < 1000:
        lo *= 1000
    if hi < 1000:
        hi *= 1000
    return lo, hi


def _is_us_or_remote(location: str, remote: str, full_text: str) -> bool:
    """Return True if the posting is US-based or remote-friendly."""
    if remote == "remote":
        return True
    if US_LOCATION_RE.search(location):
        return True
    if US_LOCATION_RE.search(full_text):
        return True
    # Check for state abbreviations in location
    for token in re.split(r"[,\s]+", location):
        if token.upper() in US_STATES:
            return True
    return False


# ─── Core Logic ──────────────────────────────────────────────────────
def find_hiring_threads(months: int = MONTHS_BACK) -> list[dict]:
    """Find the most recent N 'Who is hiring?' story threads."""
    print("🔍 Searching for 'Who is hiring?' threads …")
    data = _get(SEARCH_URL, params={
        "query": '"Ask HN: Who is hiring?"',
        "tags": "story",
        "hitsPerPage": 12,   # plenty to cover last few months
    })
    if not data:
        print("  ✗ Could not reach Algolia search API")
        return []

    hits = data.get("hits", [])
    # Filter to actual monthly "Who is hiring" threads (authored by whoishiring)
    threads = []
    for h in hits:
        title = h.get("title", "")
        if "who is hiring" in title.lower() and (
            h.get("author", "") == "whoishiring"
            or "Ask HN" in title
        ):
            threads.append({
                "story_id": h["objectID"],
                "title": title,
                "created_at": h.get("created_at", ""),
            })

    threads = threads[:months]
    print(f"  ✓ Found {len(threads)} threads:")
    for t in threads:
        print(f"    – {t['title']}  (id={t['story_id']})")
    return threads


def fetch_comments(story_id: str) -> list[dict]:
    """Fetch all top-level comments for a story (direct children only)."""
    print(f"  📥 Fetching comments for story {story_id} …")
    data = _get(ITEM_URL.format(story_id))
    if not data:
        return []

    children = data.get("children", [])
    # Only top-level children — ignore nested replies
    comments = []
    for child in children:
        if child.get("type") == "comment" and child.get("text"):
            comments.append({
                "id": child.get("id"),
                "text": child["text"],
                "author": child.get("author", ""),
                "created_at": child.get("created_at", ""),
            })
    print(f"    ✓ {len(comments)} top-level comments")
    return comments


def parse_comment(comment: dict, thread_title: str) -> dict | None:
    """
    Parse a single HN hiring comment into a row dict.
    Returns None if the comment doesn't match engineering role filters
    or isn't US / Remote.
    """
    raw = comment["text"]
    text = _clean_html(raw)

    # ── Role filter ──
    roles = _find_role_titles(text)
    if not roles:
        return None

    # ── Header parsing ──
    first_line = text.split(".")[0] if "." in text else text[:200]
    # Better: split on first sentence-like boundary
    first_line = text.split("\n")[0] if "\n" in text else text[:200]
    header = _extract_header_parts(first_line)

    # ── Remote from full text if header didn't catch it ──
    remote = header["remote"]
    if remote == "unknown":
        text_lower = text.lower()
        if "remote" in text_lower:
            remote = "remote"
        elif "hybrid" in text_lower:
            remote = "hybrid"
        elif "onsite" in text_lower or "on-site" in text_lower:
            remote = "onsite"

    location = header["location"]
    # Try to find location in full text if header didn't have one
    if not location:
        loc_match = US_LOCATION_RE.search(text)
        if loc_match:
            location = loc_match.group(0)

    # ── US / Remote filter ──
    if not _is_us_or_remote(location, remote, text):
        return None

    # ── URL from full text ──
    company_url = header["company_url"]
    if not company_url:
        urls = URL_RE.findall(text)
        # Pick the first URL that isn't an image or HN link
        for u in urls:
            if "ycombinator.com" not in u and not u.endswith((".png", ".jpg", ".gif")):
                company_url = u
                break

    # ── Email ──
    emails = EMAIL_RE.findall(text)
    contact_email = emails[0] if emails else ""

    # ── Tech stack ──
    stack = _find_tech_stack(text)

    # ── Salary ──
    sal_min, sal_max = _extract_salary(text)

    # ── Days open ──
    days_open = -1
    date_posted = ""
    if comment.get("created_at"):
        try:
            posted_dt = datetime.fromisoformat(
                comment["created_at"].replace("Z", "+00:00")
            )
            date_posted = posted_dt.strftime("%Y-%m-%d")
            days_open = (datetime.now(timezone.utc) - posted_dt).days
        except (ValueError, TypeError):
            pass

    # ── Best role title (most specific match) ──
    role_title = max(roles, key=len)

    return {
        "source": SOURCE_NAME,
        "company_name": header["company_name"],
        "company_url": company_url,
        "role_title": role_title,
        "location": location,
        "remote": remote,
        "date_posted": date_posted,
        "days_open": days_open,
        "salary_min": sal_min if sal_min else "",
        "salary_max": sal_max if sal_max else "",
        "contact_name": comment.get("author", ""),
        "contact_email": contact_email,
        "tech_stack": ", ".join(sorted(set(stack))),
        "raw_text": text[:2000],  # cap to keep CSV manageable
        # Extra field for multi-thread flagging (will be merged later)
        "_thread_title": thread_title,
    }


def _flag_repeat_posters(df: pd.DataFrame) -> pd.DataFrame:
    """
    Companies appearing in MULTIPLE monthly threads are extra valuable.
    Add an 'appeared_in_threads' column.
    """
    if df.empty:
        return df

    thread_counts = (
        df.groupby("company_name")["_thread_title"]
        .nunique()
        .reset_index()
        .rename(columns={"_thread_title": "appeared_in_threads"})
    )
    df = df.merge(thread_counts, on="company_name", how="left")
    repeats = df[df["appeared_in_threads"] > 1]["company_name"].nunique()
    print(f"  🔁 {repeats} companies appeared in multiple threads (repeat posters)")
    return df


# ─── Main ────────────────────────────────────────────────────────────
def main() -> pd.DataFrame:
    print("=" * 60)
    print("HN 'Who is Hiring?' Scraper")
    print("=" * 60)

    threads = find_hiring_threads(MONTHS_BACK)
    if not threads:
        print("No threads found. Returning empty DataFrame.")
        return pd.DataFrame(columns=SCRAPER_COLUMNS)

    all_rows: list[dict] = []

    for thread in threads:
        print(f"\n📄 Processing: {thread['title']}")
        comments = fetch_comments(thread["story_id"])

        parsed = 0
        for comment in comments:
            row = parse_comment(comment, thread["title"])
            if row:
                all_rows.append(row)
                parsed += 1

        print(f"  ✓ {parsed} engineering-relevant, US/Remote comments extracted")
        # Be polite to the API
        time.sleep(1)

    if not all_rows:
        print("\n⚠ No matching postings found across all threads.")
        return pd.DataFrame(columns=SCRAPER_COLUMNS)

    df = pd.DataFrame(all_rows)
    df = _flag_repeat_posters(df)

    # Drop the helper column; keep appeared_in_threads as bonus metadata
    df.drop(columns=["_thread_title"], inplace=True)

    # Reorder to match SCRAPER_COLUMNS (+ our extra column)
    extra_cols = [c for c in df.columns if c not in SCRAPER_COLUMNS]
    df = df[[c for c in SCRAPER_COLUMNS if c in df.columns] + extra_cols]

    # ── Save ──
    out_path = os.path.join(DATA_DIR, "hn_who_is_hiring.csv")
    df.to_csv(out_path, index=False)
    print(f"\n✅ Saved {len(df)} rows → {out_path}")
    print(f"   Unique companies: {df['company_name'].nunique()}")
    if "appeared_in_threads" in df.columns:
        multi = df[df["appeared_in_threads"] > 1]
        if not multi.empty:
            print(f"   ⚡ {multi['company_name'].nunique()} companies posted in 2+ threads (high-value leads)")

    return df


if __name__ == "__main__":
    main()
