"""
score_companies.py — Cross-referencing and scoring engine.

Reads all CSV files from data/ (scraper outputs), cross-references companies
by normalized name, calculates a composite score based on source overlap +
pain depth + role volume + funding, and outputs a tiered scored_companies.csv.

Usage:
    python -m processing.score_companies
    python processing/score_companies.py
"""

import os
import re
import sys
import glob
from urllib.parse import urlparse
from datetime import datetime, timedelta

import pandas as pd

# ── Allow running from project root or from processing/ ──
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from config import (
    DATA_DIR,
    OUTPUT_DIR,
    COMPANY_SUFFIXES_TO_STRIP,
    BLACKLIST_DOMAINS,
    ENGINEERING_ROLE_KEYWORDS,
    SCORING,
    TIER_1_MIN_SCORE,
    TIER_2_MIN_SCORE,
    TIER_3_MIN_SCORE,
)


# ─────────────────────────────────────────────
# Company name normalization
# ─────────────────────────────────────────────

def normalize_company_name(raw_name: str) -> str:
    """
    Normalize a company name for cross-referencing.

    Steps:
      1. Lowercase
      2. Strip leading/trailing whitespace
      3. Remove known suffixes (Inc, LLC, Ltd …)
      4. Remove trailing punctuation (, . -)
      5. Collapse multiple spaces into one
    """
    if not isinstance(raw_name, str) or not raw_name.strip():
        return ""

    name = raw_name.lower().strip()

    # Strip known suffixes (longest first so ", inc." is tried before " inc")
    suffixes_sorted = sorted(COMPANY_SUFFIXES_TO_STRIP, key=len, reverse=True)
    for suffix in suffixes_sorted:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break  # only strip one suffix per pass

    # Remove trailing punctuation left over after stripping
    name = re.sub(r"[,.\-\s]+$", "", name)

    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()

    return name


def extract_base_domain(url: str) -> str:
    """Extract clean company domain from URL, ignoring ATS domains."""
    if not url or not isinstance(url, str):
        return ""
        
    # Split by whitespace to drop any trailing text from HN posts
    url = url.strip().split()[0]
    
    if not url.startswith("http"):
        url = "http://" + url
    
    try:
        netloc = urlparse(url).netloc.lower()
        
        # Remove common subdomains
        for prefix in ["www.", "jobs.", "careers.", "boards.", "app."]:
            if netloc.startswith(prefix):
                netloc = netloc[len(prefix):]
                
        # If it's an ATS domain, it's not the company's domain
        ats_domains = {
            "lever.co", "greenhouse.io", "workable.com", "ashbyhq.com", 
            "breezy.hr", "ycombinator.com", "wellfound.com", "angel.co",
            "remoteok.com", "weworkremotely.com"
        }
        if any(ats in netloc for ats in ats_domains):
            return ""
            
        # Return just the domain
        return netloc
    except Exception:
        return ""


# ─────────────────────────────────────────────
# Read & merge all scraper CSVs
# ─────────────────────────────────────────────

def load_all_scraper_csvs() -> pd.DataFrame:
    """Read every CSV in DATA_DIR and return a single DataFrame."""
    csv_paths = glob.glob(os.path.join(DATA_DIR, "*.csv"))
    if not csv_paths:
        print(f"[!] No CSV files found in {DATA_DIR}")
        return pd.DataFrame()

    frames = []
    for path in csv_paths:
        try:
            df = pd.read_csv(path, dtype=str)
            df.fillna("", inplace=True)
            frames.append(df)
            print(f"    Loaded {os.path.basename(path):40s}  ({len(df)} rows)")
        except Exception as exc:
            print(f"    [WARN] Could not read {path}: {exc}")

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    print(f"\n  Total raw rows across all sources: {len(combined)}")
    return combined


# ─────────────────────────────────────────────
# Check if a role title is an engineering role
# ─────────────────────────────────────────────

def is_engineering_role(title: str) -> bool:
    """Return True if the title matches any known engineering keyword."""
    title_lower = title.lower() if isinstance(title, str) else ""
    return any(kw in title_lower for kw in ENGINEERING_ROLE_KEYWORDS)


# ─────────────────────────────────────────────
# Score a single company group
# ─────────────────────────────────────────────

def score_company(group: pd.DataFrame) -> dict:
    """
    Given all rows belonging to one normalized company, return a dict with
    aggregated fields and a total score.
    """
    score = 0

    # --- Source overlap ---
    sources = set(group["source"].str.lower().unique()) - {""}
    source_count = len(sources)

    if source_count >= 3:
        score += SCORING["source_triple_plus"]
    elif source_count == 2:
        score += SCORING["source_double"]
    elif source_count == 1:
        score += SCORING["source_single"]

    # HN bonus
    hn_sources = {"hn_who_is_hiring", "hn_whos_hiring", "hn", "hackernews"}
    if sources & hn_sources:
        score += SCORING["hn_bonus"]

    # --- Days open (pain depth) ---
    days_values = pd.to_numeric(group["days_open"], errors="coerce")
    days_values = days_values.dropna()
    days_values = days_values[days_values >= 0]
    oldest_days = int(days_values.max()) if len(days_values) > 0 else -1

    if oldest_days >= 90:
        score += SCORING["days_90_plus"]
    elif oldest_days >= 60:
        score += SCORING["days_60_89"]
    elif oldest_days >= 45:
        score += SCORING["days_45_59"]
    elif oldest_days >= 30:
        score += SCORING["days_30_44"]

    # --- Role volume ---
    role_titles = group["role_title"].str.lower().str.strip()
    role_counts = role_titles.value_counts()

    # 3+ identical role titles
    if (role_counts >= 3).any():
        score += SCORING["roles_3_plus_identical"]

    # 5+ total engineering roles
    eng_mask = role_titles.apply(is_engineering_role)
    if eng_mask.sum() >= 5:
        score += SCORING["roles_5_plus_total"]

    # --- Funding (EDGAR presence) ---
    has_funding = False
    edgar_sources = {"sec_edgar", "edgar", "sec"}
    if sources & edgar_sources:
        has_funding = True
        score += SCORING["recent_funding"]

    # --- Pick representative data ---
    # Use the first non-empty value for each field
    def first_nonempty(col):
        vals = group[col].dropna() if col in group.columns else pd.Series(dtype=str)
        vals = vals[vals.astype(str).str.strip() != ""]
        return str(vals.iloc[0]) if len(vals) > 0 else ""

    company_url = extract_base_domain(first_nonempty("company_url"))
    contact_name = first_nonempty("contact_name")
    contact_email = first_nonempty("contact_email")
    location = first_nonempty("location")

    # Remote: prefer "remote" if any row says remote
    remote_vals = group["remote"].str.lower().unique() if "remote" in group.columns else []
    remote = "remote" if "remote" in remote_vals else (
        "hybrid" if "hybrid" in remote_vals else (
            "onsite" if "onsite" in remote_vals else "unknown"
        )
    )

    # All unique non-empty roles
    unique_roles = sorted(
        set(group["role_title"].dropna().str.strip().unique()) - {""}
    )

    # --- Tier ---
    if score >= TIER_1_MIN_SCORE:
        tier = 1
    elif score >= TIER_2_MIN_SCORE:
        tier = 2
    elif score >= TIER_3_MIN_SCORE:
        tier = 3
    else:
        tier = 0  # below threshold – still output for reference

    return {
        "company_name": first_nonempty("company_name"),  # original casing
        "company_url": company_url,
        "total_score": score,
        "tier": tier,
        "sources": ", ".join(sorted(sources)),
        "source_count": source_count,
        "roles": ", ".join(unique_roles),
        "role_count": len(unique_roles),
        "oldest_days_open": oldest_days,
        "contact_name": contact_name,
        "contact_email": contact_email,
        "location": location,
        "remote": remote,
        "has_funding": has_funding,
    }


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  CONNECTOR PIPELINE — Company Scoring Engine")
    print("=" * 60)

    # 1. Load all CSVs
    print("\n[1/4] Loading scraper CSVs from data/ …")
    df = load_all_scraper_csvs()

    if df.empty:
        print("[!] Nothing to process. Exiting.")
        return

    # 2. Normalize company names for grouping
    print("\n[2/4] Normalizing company names …")
    df["_norm_name"] = df["company_name"].apply(normalize_company_name)

    # Drop rows with no usable company name
    before = len(df)
    df = df[df["_norm_name"] != ""].copy()
    dropped = before - len(df)
    if dropped:
        print(f"    Dropped {dropped} rows with empty company names")

    unique_names = df["_norm_name"].nunique()
    print(f"    {unique_names} unique companies after normalization")

    # 3. Score each company
    print("\n[3/4] Scoring companies …")
    results = []
    dropped_blacklisted = 0
    for _norm_name, group_df in df.groupby("_norm_name"):
        result = score_company(group_df)
        
        # Filter out blacklisted enterprise domains
        if result["company_url"] in BLACKLIST_DOMAINS:
            dropped_blacklisted += 1
            continue
            
        results.append(result)

    if dropped_blacklisted > 0:
        print(f"    Dropped {dropped_blacklisted} massive enterprise companies (Google, Apple, etc.)")

    scored = pd.DataFrame(results)

    # Sort by score descending, then company name
    scored.sort_values(
        by=["total_score", "company_name"],
        ascending=[False, True],
        inplace=True,
    )
    scored.reset_index(drop=True, inplace=True)

    # 4. Write output
    out_path = os.path.join(OUTPUT_DIR, "scored_companies.csv")
    scored.to_csv(out_path, index=False)
    print(f"\n[4/4] Wrote {len(scored)} companies → {out_path}")

    # ── Summary stats ──
    tier1 = (scored["tier"] == 1).sum()
    tier2 = (scored["tier"] == 2).sum()
    tier3 = (scored["tier"] == 3).sum()
    below = (scored["tier"] == 0).sum()

    print("\n" + "─" * 40)
    print("  SUMMARY")
    print("─" * 40)
    print(f"  Tier 1  (score >= {TIER_1_MIN_SCORE:>2}):  {tier1:>4} companies")
    print(f"  Tier 2  (score >= {TIER_2_MIN_SCORE:>2}):  {tier2:>4} companies")
    print(f"  Tier 3  (score >= {TIER_3_MIN_SCORE:>2}):  {tier3:>4} companies")
    print(f"  Below threshold:       {below:>4} companies")
    print(f"  TOTAL:                 {len(scored):>4} companies")
    print("─" * 40)

    if tier1 > 0:
        print("\n  Top Tier-1 companies:")
        top = scored[scored["tier"] == 1].head(10)
        for _, row in top.iterrows():
            print(
                f"    • {row['company_name']:<30s}  "
                f"score={row['total_score']:<3}  "
                f"sources={row['source_count']}  "
                f"roles={row['role_count']}  "
                f"days_open={row['oldest_days_open']}"
            )

    print("\nDone.\n")


if __name__ == "__main__":
    main()
