"""
rank_deeptech.py — pain-rank the deep-tech ATS scan output.

Reads output/deeptech_raw_jobs.csv (domain, ats, job_title, posted_date) and the
data/deeptech_leads.csv mapping (domain -> company, segment, country), keeps only
deep-tech eng/GTM roles, then scores each company by pain (days-open tier + role
volume) using the shared SCORING weights. Writes output/deeptech_scored.csv.

Run: python -m processing.rank_deeptech
"""
import os
import sys
import csv
from collections import defaultdict
from datetime import datetime, date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from config import DATA_DIR, OUTPUT_DIR, SCORING, TIER_1_MIN_SCORE, TIER_2_MIN_SCORE, TIER_3_MIN_SCORE
from scrapers.ats_deep_scanner import is_engineering_job, score_job_seniority

RAW = os.path.join(OUTPUT_DIR, "deeptech_raw_jobs.csv")
LEADS = os.path.join(DATA_DIR, "deeptech_leads.csv")
OUT = os.path.join(OUTPUT_DIR, "deeptech_scored.csv")


def days_open(posted: str) -> int:
    posted = (posted or "").strip()[:10]
    if not posted:
        return -1
    try:
        return (date.today() - datetime.strptime(posted, "%Y-%m-%d").date()).days
    except Exception:
        return -1


def main():
    if not os.path.exists(RAW):
        print(f"[!] {RAW} not found — run the scan first.")
        return

    # domain -> (company, segment, country)
    meta = {}
    with open(LEADS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            d = (r.get("Domain") or r.get("domain") or "").strip().lower()
            if d:
                meta[d] = (r.get("companyName", ""), r.get("segment", ""), r.get("country", ""))

    # gather eng roles per domain
    by_dom = defaultdict(list)  # domain -> list of (title, days_open, ats)
    with open(RAW, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            title = (r.get("job_title") or "").strip()
            if not title or not is_engineering_job(title):
                continue
            by_dom[r.get("domain", "").strip().lower()].append(
                (title, days_open(r.get("posted_date", "")), r.get("ats", "")))

    rows = []
    for dom, jobs in by_dom.items():
        company, segment, country = meta.get(dom, ("", "", ""))
        titles = [j[0] for j in jobs]
        days = [j[1] for j in jobs if j[1] >= 0]
        oldest = max(days) if days else -1

        score = 0
        if oldest >= 90:   score += SCORING["days_90_plus"]
        elif oldest >= 60: score += SCORING["days_60_89"]
        elif oldest >= 45: score += SCORING["days_45_59"]
        elif oldest >= 30: score += SCORING["days_30_44"]
        # role volume
        counts = defaultdict(int)
        for t in titles:
            counts[t.lower().strip()] += 1
        if any(v >= 3 for v in counts.values()):
            score += SCORING["roles_3_plus_identical"]
        if len(titles) >= 5:
            score += SCORING["roles_5_plus_total"]

        # best (most senior) role for the hook
        best = max(titles, key=score_job_seniority) if titles else ""

        tier = (1 if score >= TIER_1_MIN_SCORE else 2 if score >= TIER_2_MIN_SCORE
                else 3 if score >= TIER_3_MIN_SCORE else 0)

        rows.append({
            "company": company or dom, "domain": dom, "segment": segment,
            "country": country, "score": score, "tier": tier,
            "open_roles": len(titles), "oldest_days_open": oldest,
            "best_role": best, "ats": jobs[0][2],
            "all_roles": " | ".join(sorted(set(titles)))[:500],
        })

    rows.sort(key=lambda r: (r["score"], r["open_roles"]), reverse=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["company", "domain", "segment", "country",
                                          "score", "tier", "open_roles", "oldest_days_open",
                                          "best_role", "ats", "all_roles"])
        w.writeheader()
        w.writerows(rows)

    t1 = sum(1 for r in rows if r["tier"] == 1)
    t2 = sum(1 for r in rows if r["tier"] == 2)
    t3 = sum(1 for r in rows if r["tier"] == 3)
    print(f"deep-tech companies hiring (eng/GTM): {len(rows)}")
    print(f"  Tier 1 (>= {TIER_1_MIN_SCORE}): {t1}   Tier 2 (>= {TIER_2_MIN_SCORE}): {t2}   Tier 3 (>= {TIER_3_MIN_SCORE}): {t3}")
    print(f"  -> {OUT}")
    for r in rows[:12]:
        print(f"    {r['company'][:28]:28} {r['domain'][:24]:24} score={r['score']:<2} "
              f"roles={r['open_roles']:<2} oldest={r['oldest_days_open']:<3} {r['best_role'][:34]}")


if __name__ == "__main__":
    main()
