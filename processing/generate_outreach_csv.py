"""
generate_outreach_csv.py — Reachinbox CSV generator with personalized copy.

Reads scored_companies.csv from output/, optionally merges enriched contacts
from Apollo (enriched_contacts.csv), selects the best email template per
contact, and generates outreach_ready.csv that can be uploaded directly
to Reachinbox.

Usage:
    python -m processing.generate_outreach_csv
    python processing/generate_outreach_csv.py
"""

import os
import re
import sys
import math
from datetime import datetime, timedelta

import pandas as pd

# ── Allow running from project root or from processing/ ──
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from config import (
    OUTPUT_DIR,
    COPY_TEMPLATES,
    FOLLOWUP_TEMPLATES,
    OUTREACH_COLUMNS,
    TIER_1_MIN_SCORE,
)


# ─────────────────────────────────────────────
# Role category mapping
# ─────────────────────────────────────────────

# Order matters — first match wins, so more specific patterns come first.
ROLE_CATEGORY_RULES = [
    # ML / AI
    (r"machine learning|ml |ml$|ai |ai$|deep learning|nlp|computer vision", "ML/AI"),
    # Data engineering
    (r"data engineer|data platform|analytics engineer", "data engineering"),
    # DevOps / Infra
    (r"devops|site reliability|sre|platform engineer|infrastructure|cloud engineer",
     "DevOps/infrastructure"),
    # Backend
    (r"backend|back-end|back end|server engineer|api engineer", "backend engineering"),
    # Frontend
    (r"frontend|front-end|front end", "frontend engineering"),
    # Full-stack
    (r"full-stack|fullstack|full stack", "full-stack development"),
    # Catch-all for software/senior/staff/principal
    (r"software engineer|software developer|senior engineer|staff engineer|principal engineer",
     "software engineering"),
]


def classify_role_category(role_title: str) -> str:
    """Map a role title to a human-readable category."""
    if not isinstance(role_title, str) or not role_title.strip():
        return "software engineering"
    title_lower = role_title.lower()
    for pattern, category in ROLE_CATEGORY_RULES:
        if re.search(pattern, title_lower):
            return category
    return "software engineering"


# ─────────────────────────────────────────────
# Time helpers
# ─────────────────────────────────────────────

def days_to_weeks_label(days_open) -> str:
    """
    Convert days_open to a human-friendly weeks label.
    45 days → '~6 weeks',  67 days → '~10 weeks'
    """
    try:
        days = int(float(days_open))
    except (ValueError, TypeError):
        return ""
    if days < 0:
        return ""
    weeks = round(days / 7)
    if weeks < 1:
        weeks = 1
    return f"~{weeks} weeks"


def days_to_month_posted(days_open) -> str:
    """
    Estimate when the role was posted and return a human-readable label.
    'early April', 'mid-March', 'late January'
    """
    try:
        days = int(float(days_open))
    except (ValueError, TypeError):
        return ""
    if days < 0:
        return ""

    posted_date = datetime.now() - timedelta(days=days)
    month_name = posted_date.strftime("%B")
    day = posted_date.day

    if day <= 10:
        prefix = "early"
    elif day <= 20:
        prefix = "mid-"
    else:
        prefix = "late"

    return f"{prefix}{month_name}" if prefix == "mid-" else f"{prefix} {month_name}"


# ─────────────────────────────────────────────
# Pick the best primary role to feature in copy
# ─────────────────────────────────────────────

def pick_primary_role(roles_str: str) -> str:
    """
    From a comma-separated roles string, pick the first one (scored data
    is already sorted by relevance / first encountered).
    """
    if not isinstance(roles_str, str) or not roles_str.strip():
        return "engineering"
    roles = [r.strip() for r in roles_str.split(",") if r.strip()]
    return roles[0] if roles else "engineering"


# ─────────────────────────────────────────────
# Template selection
# ─────────────────────────────────────────────

def select_template_key(row: pd.Series) -> str:
    """
    Choose the best email template key based on tier and data signals.

    Priority:
      1. Tier 1 + HN source + has date data → tier1_hn_ats
      2. Tier 1 + multi-source             → tier1_multi
      3. Tier 2 + has days_open             → tier2_pain
      4. Everything else                    → generic
    """
    tier = int(row.get("tier", 0))
    sources_str = str(row.get("sources", "")).lower()
    oldest_days = _safe_int(row.get("oldest_days_open", -1))

    hn_present = any(
        kw in sources_str
        for kw in ("hn_who_is_hiring", "hn_whos_hiring", "hn", "hackernews")
    )
    source_count = int(row.get("source_count", 1))

    if tier == 1 and hn_present and oldest_days > 0:
        return "tier1_hn_ats"
    if tier == 1 and source_count >= 2:
        return "tier1_multi"
    if tier <= 2 and oldest_days > 0:
        return "tier2_pain"
    return "generic"


def _safe_int(val, default=-1) -> int:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


# ─────────────────────────────────────────────
# Render a template with available fields
# ─────────────────────────────────────────────

def render_template(template_str: str, ctx: dict) -> str:
    """
    Render a Python format-string template using ctx.
    Missing keys are replaced with a sensible fallback so we never crash.
    """
    # Build a safe dict that returns "" for missing keys
    class SafeDict(dict):
        def __missing__(self, key):
            return f"{{{key}}}"  # leave placeholder if truly missing

    return template_str.format_map(SafeDict(ctx))


# ─────────────────────────────────────────────
# Build a single outreach row
# ─────────────────────────────────────────────

def build_outreach_row(scored_row: pd.Series, contact: dict) -> dict:
    """
    Merge scored company data with contact info, select template,
    render all copy fields, and return a dict matching OUTREACH_COLUMNS.
    """
    first_name = contact.get("first_name", "")
    last_name = contact.get("last_name", "")
    email = contact.get("email", "")

    role_title = pick_primary_role(str(scored_row.get("roles", "")))
    role_category = classify_role_category(role_title)
    oldest_days = _safe_int(scored_row.get("oldest_days_open", -1))
    weeks_open = days_to_weeks_label(oldest_days)
    month_posted = days_to_month_posted(oldest_days)

    company_name = str(scored_row.get("company_name", ""))

    # Template context
    ctx = {
        "first_name": first_name if first_name else "Hi",
        "last_name": last_name,
        "company_name": company_name,
        "role_title": role_title,
        "role_category": role_category,
        "weeks_open": weeks_open,
        "month_posted": month_posted,
    }

    # --- Primary email ---
    template_key = select_template_key(scored_row)
    tpl = COPY_TEMPLATES[template_key]
    subject = render_template(tpl["subject"], ctx)
    body = render_template(tpl["body"], ctx)

    # --- Follow-ups ---
    fu1 = FOLLOWUP_TEMPLATES["followup_1"]
    fu2 = FOLLOWUP_TEMPLATES["followup_2"]

    ctx["original_subject"] = subject

    fu1_subject = render_template(fu1["subject"], ctx)
    fu1_body = render_template(fu1["body"], ctx)
    fu2_subject = render_template(fu2["subject"], ctx)
    fu2_body = render_template(fu2["body"], ctx)

    return {
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "company_name": company_name,
        "company_url": str(scored_row.get("company_url", "")),
        "role_title": role_title,
        "role_category": role_category,
        "weeks_open": weeks_open,
        "sources_found_on": str(scored_row.get("sources", "")),
        "source_count": int(scored_row.get("source_count", 1)),
        "tier": int(scored_row.get("tier", 0)),
        "score": int(scored_row.get("total_score", 0)),
        "subject": subject,
        "body": body,
        "followup_1_subject": fu1_subject,
        "followup_1_body": fu1_body,
        "followup_2_subject": fu2_subject,
        "followup_2_body": fu2_body,
    }


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  CONNECTOR PIPELINE — Outreach CSV Generator")
    print("=" * 60)

    # 1. Load scored companies
    scored_path = os.path.join(OUTPUT_DIR, "scored_companies.csv")
    if not os.path.isfile(scored_path):
        print(f"\n[!] scored_companies.csv not found at {scored_path}")
        print("    Run score_companies.py first.")
        return

    scored = pd.read_csv(scored_path, dtype=str)
    scored.fillna("", inplace=True)
    print(f"\n[1/4] Loaded {len(scored)} scored companies from {scored_path}")

    # Filter to Tier 1/2/3 only (tier > 0)
    scored["tier"] = pd.to_numeric(scored["tier"], errors="coerce").fillna(0).astype(int)
    actionable = scored[scored["tier"] > 0].copy()
    print(f"       {len(actionable)} companies at Tier 3 or above")

    if actionable.empty:
        print("[!] No actionable companies. Exiting.")
        return

    # 2. Check for enriched contacts
    enriched_path = os.path.join(OUTPUT_DIR, "enriched_contacts.csv")
    has_enriched = os.path.isfile(enriched_path)

    if has_enriched:
        enriched = pd.read_csv(enriched_path, dtype=str)
        enriched.fillna("", inplace=True)
        print(f"\n[2/4] Loaded {len(enriched)} enriched contacts from {enriched_path}")
        mode = "enriched"
    else:
        enriched = pd.DataFrame()
        print(f"\n[2/4] No enriched_contacts.csv found — using scraped contact data")
        mode = "scraped"

    # 3. Build outreach rows
    print("\n[3/4] Generating personalized outreach copy …")
    outreach_rows = []

    for _, comp_row in actionable.iterrows():
        company_name = str(comp_row.get("company_name", "")).strip()

        if mode == "enriched" and not enriched.empty:
            # Match enriched contacts to this company (case-insensitive)
            mask = enriched["company_name"].str.lower().str.strip() == company_name.lower().strip()
            matched_contacts = enriched[mask]

            if not matched_contacts.empty:
                for _, crow in matched_contacts.iterrows():
                    contact = {
                        "email": str(crow.get("email", "")),
                        "first_name": str(crow.get("first_name", "")),
                        "last_name": str(crow.get("last_name", "")),
                    }
                    outreach_rows.append(build_outreach_row(comp_row, contact))
                continue  # done with this company

        # Fallback: use scraped contact info
        contact_name = str(comp_row.get("contact_name", ""))
        contact_email = str(comp_row.get("contact_email", ""))

        # Split contact_name into first/last
        parts = contact_name.strip().split() if contact_name.strip() else []
        first_name = parts[0] if len(parts) >= 1 else ""
        last_name = " ".join(parts[1:]) if len(parts) >= 2 else ""

        contact = {
            "email": contact_email,
            "first_name": first_name,
            "last_name": last_name,
        }
        outreach_rows.append(build_outreach_row(comp_row, contact))

    outreach_df = pd.DataFrame(outreach_rows)

    # Ensure all expected columns exist (in order)
    for col in OUTREACH_COLUMNS:
        if col not in outreach_df.columns:
            outreach_df[col] = ""
    outreach_df = outreach_df[OUTREACH_COLUMNS]

    # 4. Write output
    out_path = os.path.join(OUTPUT_DIR, "outreach_ready.csv")
    outreach_df.to_csv(out_path, index=False)
    print(f"\n[4/4] Wrote {len(outreach_df)} outreach rows → {out_path}")

    # ── Summary ──
    with_email = (outreach_df["email"].str.strip() != "").sum()
    without_email = len(outreach_df) - with_email

    tier_counts = outreach_df["tier"].astype(int).value_counts().sort_index()
    template_counts = {}
    for _, row in outreach_df.iterrows():
        subj = str(row["subject"])
        # Infer which template was used from subject pattern
        for key in COPY_TEMPLATES:
            tpl_subj = COPY_TEMPLATES[key]["subject"]
            # Simple heuristic: the template subject minus placeholders
            if "{" not in tpl_subj:
                if subj == tpl_subj:
                    template_counts[key] = template_counts.get(key, 0) + 1
                    break
            else:
                # Template has placeholders — check the static parts
                static = re.sub(r"\{[^}]+\}", "", tpl_subj).strip()
                if static and static in subj:
                    template_counts[key] = template_counts.get(key, 0) + 1
                    break
        else:
            template_counts["generic"] = template_counts.get("generic", 0) + 1

    print("\n" + "─" * 40)
    print("  SUMMARY")
    print("─" * 40)
    print(f"  Total outreach rows:  {len(outreach_df)}")
    print(f"  With email:           {with_email}")
    print(f"  Missing email:        {without_email}  (enrich manually)")
    print()
    print("  By tier:")
    for t, cnt in tier_counts.items():
        print(f"    Tier {t}: {cnt}")
    print()
    print("  By template:")
    for tkey, cnt in sorted(template_counts.items()):
        print(f"    {tkey}: {cnt}")
    print("─" * 40)

    if without_email > 0:
        print(
            f"\n  💡 {without_email} rows are missing emails."
            f"\n     Upload company names to Apollo to enrich, then re-run."
        )

    print("\nDone. Upload outreach_ready.csv to Reachinbox.\n")


if __name__ == "__main__":
    main()
