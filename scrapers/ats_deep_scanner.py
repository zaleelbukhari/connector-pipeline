import os
import re
import csv
import sys
import time
import requests
import concurrent.futures
from urllib.parse import urlparse

# ── Allow running from project root or from scrapers/ ──
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR, OUTPUT_DIR, ENGINEERING_ROLE_KEYWORDS, BLACKLIST_DOMAINS

# ─── API Endpoints ───
LEVER_API = "https://api.lever.co/v0/postings/{slug}"
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
RECRUITEE_API = "https://{slug}.recruitee.com/api/offers"
WORKABLE_API = "https://apply.workable.com/api/v3/accounts/{slug}/jobs"
SMARTRECRUITERS_API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"

# ─── Timeouts ───
FAST_TIMEOUT = 3       # For APIs with known-fast responses (Greenhouse, Lever, Ashby)
SLOW_TIMEOUT = 4       # For slower APIs (Workable, Recruitee, SmartRecruiters)
CAREERS_TIMEOUT = 2.5  # For scraping career pages

# ─── Concurrency ───
MAX_WORKERS = 80

# Cap DNS/connect hangs at the socket level — Windows getaddrinfo ignores the
# requests timeout, so dead domains in the universe were stalling worker threads.
import socket
socket.setdefaulttimeout(6)

# ─── Reusable session for connection pooling ───
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
# Increase connection pool size to match our thread count
adapter = requests.adapters.HTTPAdapter(pool_connections=60, pool_maxsize=60)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)

# ─── ATS URL Patterns (regex to extract slugs from career pages) ───
ATS_PATTERNS = [
    # Greenhouse
    (r'boards\.greenhouse\.io/([a-zA-Z0-9_-]+)', 'greenhouse'),
    (r'job-boards\.greenhouse\.io/([a-zA-Z0-9_-]+)', 'greenhouse'),
    (r'boards-api\.greenhouse\.io/v1/boards/([a-zA-Z0-9_-]+)', 'greenhouse'),
    # Lever
    (r'jobs\.lever\.co/([a-zA-Z0-9_.-]+)', 'lever'),
    (r'api\.lever\.co/v0/postings/([a-zA-Z0-9_.-]+)', 'lever'),
    # Ashby
    (r'jobs\.ashbyhq\.com/([a-zA-Z0-9_.-]+)', 'ashby'),
    (r'api\.ashbyhq\.com/posting-api/job-board/([a-zA-Z0-9_.-]+)', 'ashby'),
    # Recruitee
    (r'([a-zA-Z0-9_-]+)\.recruitee\.com', 'recruitee'),
    # Workable
    (r'apply\.workable\.com/([a-zA-Z0-9_-]+)', 'workable'),
    # SmartRecruiters
    (r'jobs\.smartrecruiters\.com/([a-zA-Z0-9_.-]+)', 'smartrecruiters'),
    # BreezyHR
    (r'([a-zA-Z0-9_-]+)\.breezy\.hr', 'breezy'),
    # Jobvite
    (r'jobs\.jobvite\.com/([a-zA-Z0-9_-]+)', 'jobvite'),
    # BambooHR
    (r'([a-zA-Z0-9_-]+)\.bamboohr\.com', 'bamboohr'),
]


def casualize_job_title(title: str) -> str:
    """
    Format job titles to sound like a human typed them casually in an email.
    e.g. "Senior Software Engineer (Remote - US)" -> "sr swe"
    """
    t = title.lower()
    
    # 1. Strip locations/metadata in parentheses or brackets
    t = re.sub(r'[\(\[].*?[\)\]]', '', t)
    
    # 2. Strip things after dashes or pipes (usually locations or departments)
    t = re.split(r'\s+-\s+|\s+\|\s+', t)[0]
    
    # 3. Specific word replacements
    replacements = {
        r'\bsoftware engineer\b': 'swe',
        r'\bsoftware engineering\b': 'swe',
        r'\bengineer\b': 'eng',
        r'\bengineering\b': 'eng',
        r'\bdeveloper\b': 'dev',
        r'\bdevelopment\b': 'dev',
        r'\bsenior\b': 'sr',
        r'\bmanager\b': 'mgr',
        r'\bmachine learning\b': 'ml',
        r'\bartificial intelligence\b': 'ai',
        r'\bquality assurance\b': 'qa',
        r'\buser experience\b': 'ux',
        r'\buser interface\b': 'ui',
        r'\bfront end\b': 'frontend',
        r'\bback end\b': 'backend',
        r'\bfront-end\b': 'frontend',
        r'\bback-end\b': 'backend',
    }
    
    for pattern, replacement in replacements.items():
        t = re.sub(pattern, replacement, t)
        
    # Clean up excess whitespace and commas
    t = re.sub(r'\s+', ' ', t).strip()
    t = t.strip(',').strip()
    
    return t


def score_job_seniority(title: str) -> int:
    """Assign a higher score to harder-to-fill/more senior roles."""
    t = title.lower()
    score = 0
    if "vp" in t.split() or "head" in t:
        score += 4
    if "director" in t:
        score += 3
    if "principal" in t or "staff" in t or "architect" in t:
        score += 2
    if "senior" in t or "sr" in t.split() or "lead" in t or "manager" in t:
        score += 1
    return score


def is_engineering_job(title: str) -> bool:
    """
    Broad-match filter: catches ANY title with a technical keyword,
    then excludes non-technical and junior/intern roles.
    """
    t = title.lower()
    words = t.split()
    
    # ── HARD EXCLUDE: low-level roles that recruiters won't touch ──
    low_level = ['intern', 'internship', 'student', 'graduate', 'co-op', 'apprentice',
                 'junior', 'entry level', 'entry-level']
    if any(kw in t for kw in low_level) or 'jr' in words or 'jr.' in words:
        return False
    
    # ── HARD EXCLUDE: non-technical roles that contain "engineer" ──
    # NOTE: tuned for deep-tech HW. "field application engineer" (FAE) is kept
    # on purpose (it's a key semiconductor GTM role); bare "designer" is NOT
    # excluded anymore so "Analog Designer" / "IC Designer" survive.
    non_technical = [
        'sales engineer', 'solutions engineer',
        'support engineer', 'customer engineer', 'customer success',
        'pre-sales', 'implementation engineer', 'account engineer',
        'field service', 'service engineer', 'technical writer',
        'technical recruiter', 'recruiter', 'technical account',
        'project manager', 'product manager', 'program manager',
        'marketing', 'human resources', 'office manager',
        'business development', 'copywriter',
        'graphic design', 'web design', 'ux design', 'ui design',
        'product design', 'game design', 'social media',
    ]
    if any(kw in t for kw in non_technical):
        return False

    # ── BROAD MATCH: any title containing core technical words ──
    # software terms (kept for the SaaS flow) + deep-tech HW terms (new)
    technical_core = [
        # software
        'engineer', 'developer', 'architect', 'sre', 'sdet',
        'devops', 'dev ops', 'swe', 'programmer',
        'cto', 'chief technology officer',
        'vp of engineering', 'vp, engineering', 'vp engineering',
        'head of engineering', 'director of engineering',
        'director, engineering', 'engineering manager',
        'tech lead', 'technical lead',
        # deep-tech hardware / semiconductor (titles that may lack "engineer")
        'analog', 'rfic', 'mixed-signal', 'mixed signal', 'asic', 'fpga',
        'soc', 'vlsi', 'rtl', 'physical design', 'layout', 'dsp', 'mems',
        'photonic', 'silicon', 'semiconductor', 'ic design', 'design engineer',
        'design lead', 'verification', 'characterization', 'signal integrity',
        'firmware', 'embedded', 'hardware', 'field application engineer',
    ]

    return any(kw in t for kw in technical_core)


# ═══════════════════════════════════════════════════════════════
#  ATS FETCHERS — ordered by API response speed
# ═══════════════════════════════════════════════════════════════

def _ms_to_iso(ms) -> str:
    """Lever createdAt is epoch-ms -> 'YYYY-MM-DD'."""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).date().isoformat()
    except Exception:
        return ""


def fetch_greenhouse_jobs(slug: str) -> list[dict]:
    """Greenhouse: fastest API, always responds quickly even on 404."""
    try:
        r = SESSION.get(GREENHOUSE_API.format(slug=slug), timeout=FAST_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            jobs = data.get("jobs", [])
            return [{"title": job.get("title", ""),
                     "posted": (job.get("updated_at") or "")[:10]} for job in jobs]
    except Exception:
        pass
    return []


def fetch_lever_jobs(slug: str) -> list[dict]:
    """Lever: fast API responses."""
    try:
        r = SESSION.get(LEVER_API.format(slug=slug), timeout=FAST_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            return [{"title": job.get("text", ""),
                     "posted": _ms_to_iso(job.get("createdAt"))} for job in data]
    except Exception:
        pass
    return []


def fetch_ashby_jobs(slug: str) -> list[dict]:
    """Ashby: fast API responses."""
    try:
        r = SESSION.get(ASHBY_API.format(slug=slug), timeout=FAST_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            jobs = data.get("jobs", [])
            return [{"title": job.get("title", ""),
                     "posted": (job.get("publishedAt") or "")[:10]} for job in jobs]
    except Exception:
        pass
    return []


def fetch_smartrecruiters_jobs(slug: str) -> list[dict]:
    """SmartRecruiters: mid-speed, returns empty content array on miss."""
    try:
        r = SESSION.get(SMARTRECRUITERS_API.format(slug=slug), timeout=SLOW_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            content = data.get("content", [])
            if content:  # Empty array = no jobs, skip
                return [{"title": job.get("name", "")} for job in content]
    except Exception:
        pass
    return []


def fetch_workable_jobs(slug: str) -> list[dict]:
    """Workable: slower (POST), only check via career page discovery."""
    try:
        payload = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        r = SESSION.post(WORKABLE_API.format(slug=slug), json=payload, timeout=SLOW_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            jobs = data.get("results", [])
            return [{"title": job.get("title", "")} for job in jobs]
    except Exception:
        pass
    return []


def fetch_recruitee_jobs(slug: str) -> list[dict]:
    """Recruitee: slowest (DNS resolution on subdomain), only check via career page discovery."""
    try:
        r = SESSION.get(RECRUITEE_API.format(slug=slug), timeout=SLOW_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            jobs = data.get("offers", [])
            return [{"title": job.get("title", "")} for job in jobs]
    except Exception:
        pass
    return []


def _workday_posted_to_date(s: str) -> str:
    """Workday postedOn is relative: 'Posted Today' / 'Posted 30+ Days Ago' -> ISO date."""
    from datetime import date, timedelta
    s = (s or "").lower()
    if "today" in s:
        return date.today().isoformat()
    if "yesterday" in s:
        return (date.today() - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*day", s)
    if m:
        return (date.today() - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\+?\s*month", s)
    if m:
        return (date.today() - timedelta(days=int(m.group(1)) * 30)).isoformat()
    return ""


def fetch_workday_jobs(tenant: str, dc: str, site: str) -> list[dict]:
    """Workday: POST to the public cxs jobs endpoint. tenant/dc/site come from
    career-page discovery. Caps a few pages — we only need enough to find eng roles."""
    url = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    out = []
    try:
        for offset in (0, 20, 40, 60):
            r = SESSION.post(url, json={"limit": 20, "offset": offset,
                                        "searchText": "", "appliedFacets": {}},
                             timeout=SLOW_TIMEOUT)
            if r.status_code != 200:
                break
            posts = r.json().get("jobPostings", [])
            if not posts:
                break
            for j in posts:
                out.append({"title": j.get("title", ""),
                            "posted": _workday_posted_to_date(j.get("postedOn", ""))})
            if len(posts) < 20:
                break
    except Exception:
        pass
    return out


# ─── Fetcher Registry ───
# FAST: checked via brute-force (respond quickly even on 404)
FAST_FETCHERS = {
    "Greenhouse": fetch_greenhouse_jobs,
    "Lever": fetch_lever_jobs,
    "Ashby": fetch_ashby_jobs,
    "SmartRecruiters": fetch_smartrecruiters_jobs,
}

# SLOW: only checked when career page discovery finds an exact slug
# (Recruitee/Workable use subdomains that cause DNS timeouts on misses)
SLOW_FETCHERS = {
    "Workable": fetch_workable_jobs,
    "Recruitee": fetch_recruitee_jobs,
}

ALL_FETCHERS = {**FAST_FETCHERS, **SLOW_FETCHERS}


# ═══════════════════════════════════════════════════════════════
#  SLUG GENERATION — more variations = more hits
# ═══════════════════════════════════════════════════════════════

def get_domain_slugs(domain: str) -> list[str]:
    """
    Generate potential ATS slugs from a domain.
    e.g. outset.ai -> ['outset', 'outsetai', 'outset-ai']
    """
    clean = domain.lower().replace("http://", "").replace("https://", "").replace("www.", "").split('/')[0]
    
    parts = clean.split('.')
    if len(parts) < 2:
        return [clean]
        
    name = parts[0]
    tld = parts[1]
    
    slugs = [
        name,                    # outset
        f"{name}{tld}",          # outsetai
        f"{name}-{tld}",         # outset-ai
    ]
    
    # Strip common suffixes for alternative slugs
    for suffix in ['hq', 'app', 'inc', 'co', 'io', 'labs', 'tech', 'ai']:
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            stripped = name[:-len(suffix)]
            slugs.append(stripped)
            # Also try with hyphen: e.g. "datadoghq" -> "datadog"
            if suffix in ['hq', 'inc', 'co']:
                slugs.append(f"{stripped}-{suffix}")
    
    # CamelCase split: e.g. "pureStorage" -> "pure-storage"
    hyphenated = re.sub(r'([a-z])([A-Z])', r'\1-\2', name).lower()
    if hyphenated != name:
        slugs.append(hyphenated)
    
    # Ensure uniqueness while preserving order
    seen = set()
    unique = []
    for s in slugs:
        if s and s not in seen:
            seen.add(s)
            unique.append(s)
            
    return unique


# ═══════════════════════════════════════════════════════════════
#  PHASE 1: FAST BRUTE-FORCE (Greenhouse, Lever, Ashby, SmartRecruiters)
# ═══════════════════════════════════════════════════════════════

def fast_brute_force(domain: str) -> tuple[str, list[dict]] | None:
    """
    Try all slug variations against FAST ATS APIs only.
    These APIs respond quickly even on 404, so brute-forcing is cheap.
    """
    slugs = get_domain_slugs(domain)
    
    for slug in slugs:
        for ats_name, fetcher in FAST_FETCHERS.items():
            jobs = fetcher(slug)
            if jobs:
                return (ats_name, jobs)
    
    return None


# ═══════════════════════════════════════════════════════════════
#  PHASE 2: CAREER PAGE DISCOVERY (finds exact slugs for ANY platform)
# ═══════════════════════════════════════════════════════════════

def discover_ats_from_careers_page(domain: str) -> tuple[str, str] | None:
    """
    Scrape the company's website for ATS links.
    Returns (ats_platform_key, exact_slug) or None.
    """
    clean = domain.lower().replace("http://", "").replace("https://", "").replace("www.", "").split("/")[0]
    
    # Try the most productive career page URLs (trimmed for speed — each miss
    # costs a full request; the long tail of patterns wasn't worth the stall)
    urls_to_try = [
        f"https://{clean}/careers",
        f"https://{clean}/company/careers",
        f"https://{clean}",
    ]
    
    for url in urls_to_try:
        try:
            r = SESSION.get(url, timeout=CAREERS_TIMEOUT, allow_redirects=True)
            if r.status_code != 200:
                continue
            
            text = r.text[:50000]  # Only scan first 50KB for speed
            combined = r.url + " " + text

            # ── Workday (3-part: tenant/dc/site) — the dominant deep-tech ATS ──
            wd = re.search(
                r'https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/([^"\'\s?<>]+)',
                combined, re.IGNORECASE)
            if wd:
                tenant, dc, path = wd.group(1).lower(), wd.group(2).lower(), wd.group(3)
                segs = [s for s in path.split('/')
                        if s and not re.match(r'^[a-z]{2}-[A-Z]{2}$', s)]
                site = segs[0] if segs else ""
                if site and site.lower() not in ('wday', 'job', 'jobs'):
                    return ('workday', f'{tenant}|{dc}|{site}')

            # Check the final URL (redirects often point straight to ATS)
            for pattern, platform in ATS_PATTERNS:
                match = re.search(pattern, r.url, re.IGNORECASE)
                if match:
                    slug = match.group(1).lower().rstrip('/')
                    if slug and slug not in ('api', 'www', 'jobs', 'apply'):
                        return (platform, slug)

            # Scan the HTML body for ATS links
            for pattern, platform in ATS_PATTERNS:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    slug = match.group(1).lower().rstrip('/')
                    if slug and slug not in ('api', 'www', 'jobs', 'apply'):
                        return (platform, slug)
                    
        except Exception:
            continue
    
    return None


# ═══════════════════════════════════════════════════════════════
#  MAIN DOMAIN PROCESSOR
# ═══════════════════════════════════════════════════════════════

def extract_best_role(jobs: list[dict]) -> dict | None:
    """From a list of jobs, find the most senior engineering role."""
    eng_jobs = [j for j in jobs if is_engineering_job(j["title"])]
    
    if not eng_jobs:
        return None
    
    best_job = None
    best_score = -1
    for job in eng_jobs:
        score = score_job_seniority(job["title"])
        if score > best_score:
            best_score = score
            best_job = job["title"]
    
    return {"title": best_job, "score": best_score} if best_job else None


def process_domain(domain: str) -> dict:
    """
    Three-phase ATS discovery:
      Phase 1: Fast brute-force (Greenhouse/Lever/Ashby/SmartRecruiters — instant 404s)
      Phase 2: Career page discovery (scrapes /careers for exact ATS slug)
      Phase 3: Targeted slow API call (Workable/Recruitee — only if Phase 2 found the slug)
    """
    clean = domain.lower().replace("http://", "").replace("https://", "").replace("www.", "").split("/")[0]
    
    if not clean.strip():
        return None
    
    if clean in BLACKLIST_DOMAINS:
        return None
    
    ats_used = ""
    jobs = []
    method = ""
    
    # ── Phase 1: Fast brute-force ──
    result = fast_brute_force(clean)
    if result:
        ats_used, jobs = result
        method = "brute"
    
    # ── Phase 2: Career page discovery (only if Phase 1 failed) ──
    if not jobs:
        discovery = discover_ats_from_careers_page(clean)
        if discovery:
            platform, slug = discovery

            # Workday needs a 3-part slug (tenant|dc|site) and its own fetcher
            if platform == 'workday':
                try:
                    tenant, dc, site = slug.split('|')
                    wd_jobs = fetch_workday_jobs(tenant, dc, site)
                    if wd_jobs:
                        jobs = wd_jobs
                        ats_used = 'Workday'
                        method = 'discovery'
                except Exception:
                    pass

            fetcher = ALL_FETCHERS.get(platform.capitalize()) or ALL_FETCHERS.get(platform)

            # Normalize platform key for fetcher lookup
            platform_map = {
                'greenhouse': 'Greenhouse',
                'lever': 'Lever',
                'ashby': 'Ashby',
                'recruitee': 'Recruitee',
                'workable': 'Workable',
                'smartrecruiters': 'SmartRecruiters',
            }
            ats_key = platform_map.get(platform, platform)
            fetcher = ALL_FETCHERS.get(ats_key)
            
            if fetcher:
                jobs = fetcher(slug)
                if jobs:
                    ats_used = ats_key
                    method = "discovery"
    
    # ── Extract best engineering role ──
    if jobs:
        best = extract_best_role(jobs)
        if best:
            casual = casualize_job_title(best["title"])
            return {
                "domain": clean,
                "ats": ats_used,
                "raw_role": best["title"],
                "casual_role": casual,
                "method": method,
                "total_jobs": len(jobs),
                "all_jobs": jobs,  # full dicts (title + posted) for re-filtering + dates
            }
        else:
            # Found jobs but none matched engineering filter — save for re-filtering
            return {
                "domain": clean,
                "ats": ats_used,
                "raw_role": "",
                "casual_role": "",
                "method": method,
                "total_jobs": len(jobs),
                "all_jobs": jobs,
                "has_jobs_no_eng": True,
            }
    
    return None


# ═══════════════════════════════════════════════════════════════
#  MAIN — with incremental save + resume support
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  ATS DEEP SCANNER v3.0")
    print("  Phase 1: Fast Brute-Force (Greenhouse/Lever/Ashby/SmartRecruiters)")
    print("  Phase 2: Career Page Discovery (all platforms)")
    print("  Phase 3: Targeted Slow API (Workable/Recruitee)")
    print("=" * 60)
    
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="apollo_demand_leads.csv",
                    help="input CSV filename in data/ (needs a domain column)")
    ap.add_argument("--out-prefix", default="ats",
                    help="output prefix; writes <prefix>_enriched_roles.csv + <prefix>_raw_jobs.csv")
    cli, _ = ap.parse_known_args()

    input_file = os.path.join(DATA_DIR, cli.input)
    output_file = os.path.join(OUTPUT_DIR, f"{cli.out_prefix}_enriched_roles.csv")
    
    if not os.path.exists(input_file):
        print(f"[!] Input file not found: {input_file}")
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(input_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["domain"])
            writer.writerow(["outset.ai"])
        print(f"    Created a template at {input_file}")
        return

    # ── Read domains ──
    domains = []
    with open(input_file, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        domain_col = None
        if reader.fieldnames:
            for col in reader.fieldnames:
                if "domain" in col.lower() or "url" in col.lower() or "website" in col.lower():
                    domain_col = col
                    break
            if not domain_col:
                domain_col = reader.fieldnames[0]
                
            seen = set()
            for row in reader:
                val = row.get(domain_col, "").strip().lower()
                val = val.replace("http://", "").replace("https://", "").replace("www.", "")
                if "/" in val:
                    val = val.split("/")[0]
                if val and val not in seen and val not in BLACKLIST_DOMAINS:
                    seen.add(val)
                    domains.append(val)
    
    if not domains:
        print("[!] No domains found.")
        return
    
    # ── Check for resume capability ──
    already_done = set()
    if os.path.exists(output_file):
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    d = row.get("domain", "").strip().lower()
                    if d:
                        already_done.add(d)
        except Exception:
            pass
    
    if already_done:
        original_count = len(domains)
        domains = [d for d in domains if d not in already_done]
        print(f"RESUME MODE: {len(already_done)} domains already processed. {len(domains)} remaining (of {original_count} total).\n")
    
    if not domains:
        print("All domains already processed! Nothing to do.")
        return
        
    print(f"Scanning {len(domains)} unique domains with {MAX_WORKERS} threads...\n")
    
    found_count = 0
    brute_count = 0
    discovery_count = 0
    jobs_no_eng_count = 0
    start_time = time.time()
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # ── Open output files for incremental writing ──
    raw_file = os.path.join(OUTPUT_DIR, f"{cli.out_prefix}_raw_jobs.csv")
    
    write_mode = "a" if already_done else "w"
    outfile = open(output_file, write_mode, newline="", encoding="utf-8")
    writer = csv.DictWriter(outfile, fieldnames=["domain", "ats", "raw_role", "casual_role", "total_jobs"])
    if not already_done:
        writer.writeheader()
    
    rawfile = open(raw_file, write_mode, newline="", encoding="utf-8")
    raw_writer = csv.DictWriter(rawfile, fieldnames=["domain", "ats", "job_title", "posted_date"])
    if not already_done:
        raw_writer.writeheader()
    
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_domain = {executor.submit(process_domain, d): d for d in domains}
            
            completed = 0
            for future in concurrent.futures.as_completed(future_to_domain):
                domain = future_to_domain[future]
                completed += 1
                
                try:
                    result = future.result()
                    if result and result.get("raw_role"):
                        # ── HIT: found engineering role ──
                        found_count += 1
                        if result.get("method") == "discovery":
                            discovery_count += 1
                            icon = "🎯"
                        else:
                            brute_count += 1
                            icon = "🔍"
                        print(f"[{completed}/{len(domains)}] {domain} {icon} {result['ats']} -> {result['casual_role']} ({result['total_jobs']} jobs)")
                        
                        writer.writerow({
                            "domain": result["domain"],
                            "ats": result["ats"],
                            "raw_role": result["raw_role"],
                            "casual_role": result["casual_role"],
                            "total_jobs": result["total_jobs"],
                        })
                        outfile.flush()
                        
                    elif result and result.get("has_jobs_no_eng"):
                        # ── PARTIAL: found ATS with jobs but no engineering match ──
                        jobs_no_eng_count += 1
                        writer.writerow({
                            "domain": result["domain"],
                            "ats": result["ats"],
                            "raw_role": "",
                            "casual_role": "",
                            "total_jobs": result["total_jobs"],
                        })
                        outfile.flush()
                        
                    else:
                        # ── MISS: no ATS found at all ──
                        writer.writerow({"domain": domain, "ats": "", "raw_role": "", "casual_role": "", "total_jobs": 0})
                        outfile.flush()
                        
                        if completed % 10 == 0:
                            print(f"[{completed}/{len(domains)}] ... ✗ (last 10 no hits)")
                    
                    # ── Save ALL raw job titles to raw file ──
                    if result and result.get("all_jobs"):
                        for job in result["all_jobs"]:
                            raw_writer.writerow({
                                "domain": result["domain"],
                                "ats": result.get("ats", ""),
                                "job_title": job.get("title", ""),
                                "posted_date": job.get("posted", ""),
                            })
                        rawfile.flush()
                            
                except Exception as exc:
                    writer.writerow({"domain": domain, "ats": "", "raw_role": "", "casual_role": "", "total_jobs": 0})
                    outfile.flush()
                
                # Progress stats every 200 domains
                if completed % 200 == 0:
                    elapsed = time.time() - start_time
                    rate = completed / elapsed
                    remaining = (len(domains) - completed) / rate if rate > 0 else 0
                    hit_pct = (found_count / completed * 100) if completed > 0 else 0
                    print(f"\n{'─'*60}")
                    print(f"  Progress: {completed}/{len(domains)} | Eng Hits: {found_count} ({hit_pct:.1f}%)")
                    print(f"  Brute: {brute_count} | Discovery: {discovery_count}")
                    print(f"  Has Jobs (no eng match): {jobs_no_eng_count}")
                    print(f"  Speed: {rate:.1f}/sec | ETA: {remaining/60:.1f} min")
                    print(f"{'─'*60}\n")
    
    finally:
        outfile.close()
        rawfile.close()
    
    elapsed = time.time() - start_time
    hit_pct = (found_count / len(domains) * 100) if domains else 0
    total_found = found_count + len(already_done)
    
    print("\n" + "=" * 60)
    print(f"  SCAN COMPLETE in {elapsed/60:.1f} minutes")
    print(f"  Domains Scanned:       {len(domains)}")
    print(f"  Engineering Roles:     {found_count} ({hit_pct:.1f}%)")
    print(f"    via Brute-Force:     {brute_count}")
    print(f"    via Discovery:       {discovery_count}")
    print(f"  Jobs Found (no eng):   {jobs_no_eng_count}")
    if already_done:
        print(f"  Previously Done:       {len(already_done)}")
        print(f"  TOTAL in CSV:          {total_found}")
    print(f"  Speed:                 {len(domains)/elapsed:.1f} domains/sec")
    print(f"  Saved to: {output_file}")
    print(f"  Raw jobs: {raw_file}")
    print("=" * 60)

if __name__ == "__main__":
    main()

