"""
Shared configuration for the connector pipeline.
Scoring weights, role keywords, data format specs.
"""

import os

# ─── Directories ───
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── Role Keywords (engineering roles with scale + volume) ───
ENGINEERING_ROLE_KEYWORDS = [
    # Backend
    "backend engineer", "backend developer", "back-end engineer", "back-end developer",
    "server engineer", "api engineer",
    # Senior / Staff / Principal
    "senior engineer", "senior developer", "senior software",
    "staff engineer", "staff software", "principal engineer",
    # Full-stack
    "full-stack engineer", "full-stack developer", "fullstack engineer", "fullstack developer",
    "full stack engineer", "full stack developer",
    # Frontend
    "frontend engineer", "frontend developer", "front-end engineer", "front-end developer",
    # AI / ML
    "machine learning engineer", "ml engineer", "ai engineer",
    "deep learning", "nlp engineer", "computer vision engineer",
    # Data Engineering
    "data engineer", "data platform", "analytics engineer",
    # DevOps / Platform / SRE
    "devops engineer", "site reliability", "sre", "platform engineer",
    "infrastructure engineer", "cloud engineer",
    # Software Engineer (generic but high-volume)
    "software engineer", "software developer",
    # Mobile
    "mobile engineer", "mobile developer",
    "ios engineer", "ios developer",
    "android engineer", "android developer",
    # Security
    "security engineer", "appsec engineer", "application security",
    # Embedded / Firmware
    "embedded engineer", "embedded developer", "firmware engineer",
    # Leadership (hard-to-fill, high-fee roles)
    "engineering manager", "director of engineering", "vp of engineering",
    "head of engineering", "technical lead", "tech lead",
    # Architecture
    "software architect", "solutions architect", "systems architect",
    # QA / Test (SDET-level only)
    "sdet", "software development engineer in test", "qa engineer", "test engineer",
    # Release / Build
    "release engineer", "build engineer",
    # ─── Deep-tech / semiconductor (for the hardware flow) ───
    "analog", "mixed-signal", "mixed signal", "rfic", "rf engineer",
    "radio frequency", "microwave", "mmwave", "asic", "fpga", "soc",
    "vlsi", "rtl", "ic design", "analog design", "analog designer",
    "physical design", "layout engineer", "dsp", "verification engineer",
    "design verification", "signal integrity", "characterization",
    "validation engineer", "mems", "photonics", "silicon", "semiconductor",
    "hardware engineer", "hardware design", "field application engineer",
]

# ─── Tech Stack Keywords (for identifying tech companies) ───
TECH_STACK_KEYWORDS = [
    "python", "javascript", "typescript", "react", "node", "go", "golang",
    "rust", "java", "kotlin", "swift", "ruby", "rails", "django", "flask",
    "aws", "gcp", "azure", "kubernetes", "docker", "terraform",
    "postgresql", "mongodb", "redis", "kafka", "elasticsearch",
    "graphql", "rest api", "microservices", "distributed systems",
    "machine learning", "deep learning", "pytorch", "tensorflow",
    "nextjs", "vue", "angular", "svelte",
]

# ─── Scoring Weights ───
SCORING = {
    # Source overlap
    "source_single": 2,
    "source_double": 5,
    "source_triple_plus": 9,
    "hn_bonus": 4,  # HN Who's Hiring is a self-selected desperation signal

    # Pain depth (days open)
    "days_30_44": 3,
    "days_45_59": 5,
    "days_60_89": 7,
    "days_90_plus": 9,

    # Role volume
    "roles_3_plus_identical": 3,
    "roles_5_plus_total": 2,

    # Timing
    "recent_funding": 2,

    # Company size (lean team = more likely to need outside help)
    "size_20_100": 2,
    "size_101_250": 1,
    "size_251_500": 0,
}

# ─── Tier Thresholds ───
TIER_1_MIN_SCORE = 12
TIER_2_MIN_SCORE = 8
TIER_3_MIN_SCORE = 5

# ─── Company Name Normalization (strip these for matching) ───
COMPANY_SUFFIXES_TO_STRIP = [
    " inc", " inc.", " llc", " ltd", " ltd.", " co", " co.",
    " corp", " corp.", " corporation", " limited",
    " technologies", " technology", " tech",
    " software", " solutions", " labs", " studio", " studios",
    ", inc", ", inc.", ", llc", ", ltd",
]

# ─── Enterprise Blacklist (Companies that don't use boutique agencies) ───
BLACKLIST_DOMAINS = {
    "google.com", "apple.com", "meta.com", "amazon.com", "microsoft.com", 
    "netflix.com", "uber.com", "lyft.com", "airbnb.com", "doordash.com",
    "stripe.com", "salesforce.com", "oracle.com", "ibm.com", "intel.com",
    "cisco.com", "adobe.com", "paypal.com", "intuit.com", "nvidia.com",
    "tiktok.com", "bytedance.com", "spotify.com", "twitter.com", "x.com",
    "snapchat.com", "snap.com", "pinterest.com", "reddit.com", "bloomberg.com",
    "cloudflare.com", "databricks.com", "snowflake.com", "palantir.com"
}

# ─── Email Copy Templates ───
COPY_TEMPLATES = {
    # Tier 1: HN + ATS (highest confidence)
    "tier1_hn_ats": {
        "subject": "{role_title}",
        "body": (
            "{first_name} — noticed your {role_title} has been open "
            "since {month_posted}, and you posted it in HN Who's Hiring "
            "alongside your careers page.\n\n"
            "I work with a tech recruiter who fills {role_category} roles "
            "at companies your size. If you want an intro, happy to set "
            "it up. No cost to you.\n\n"
            "Mohammed"
        ),
    },
    # Tier 1: Multi-source, no date
    "tier1_multi": {
        "subject": "filling {role_title} at {company_name}",
        "body": (
            "{first_name} — I've seen {company_name} posting for "
            "{role_title} across a few places and figured you might "
            "still be looking.\n\n"
            "I have a recruiter who focuses on {role_category} hires "
            "for companies at your stage. Want me to make an intro? "
            "Zero cost on your end.\n\n"
            "Mohammed"
        ),
    },
    # Tier 2: Single source with pain signal
    "tier2_pain": {
        "subject": "{role_title} role",
        "body": (
            "{first_name} — saw your {role_title} position has been "
            "live for about {weeks_open} weeks.\n\n"
            "I connect companies with specialized tech recruiters. "
            "If this seat is still a priority and you'd find an intro "
            "useful, just say the word.\n\n"
            "Mohammed"
        ),
    },
    # Tier 2-3: Generic (minimal data)
    "generic": {
        "subject": "your engineering hiring",
        "body": (
            "{first_name} — saw {company_name} has open engineering "
            "roles right now.\n\n"
            "I work with specialized tech recruiters. If you're finding "
            "any of those seats hard to fill, I can intro you to one who "
            "focuses on exactly this. No cost to you.\n\n"
            "Mohammed"
        ),
    },
}

# ─── Follow-up Templates ───
FOLLOWUP_TEMPLATES = {
    "followup_1": {
        "subject": "Re: {original_subject}",
        "body": (
            "{first_name} — quick follow-up. If {role_title} is "
            "covered, ignore this.\n\n"
            "If not, the intro is still on the table.\n\n"
            "Mohammed"
        ),
    },
    "followup_2": {
        "subject": "Re: {original_subject}",
        "body": (
            "Last note — reply \"yes\" if you want the intro. "
            "Otherwise I won't follow up again.\n\n"
            "Mohammed"
        ),
    },
}

# ─── Intermediate CSV Columns (shared format for all scrapers) ───
SCRAPER_COLUMNS = [
    "source",           # e.g., "hn_who_is_hiring", "remoteok", "weworkremotely"
    "company_name",
    "company_url",
    "role_title",
    "location",
    "remote",           # "remote", "onsite", "hybrid", "unknown"
    "date_posted",      # ISO format YYYY-MM-DD or empty
    "days_open",        # calculated from date_posted, or -1 if unknown
    "salary_min",
    "salary_max",
    "contact_name",
    "contact_email",
    "tech_stack",       # comma-separated
    "raw_text",         # original posting text for reference
]

# ─── Output CSV Columns (for Reachinbox) ───
OUTREACH_COLUMNS = [
    "email",
    "first_name",
    "last_name",
    "company_name",
    "company_url",
    "role_title",
    "role_category",
    "weeks_open",
    "sources_found_on",
    "source_count",
    "tier",
    "score",
    "subject",
    "body",
    "followup_1_subject",
    "followup_1_body",
    "followup_2_subject",
    "followup_2_body",
]
