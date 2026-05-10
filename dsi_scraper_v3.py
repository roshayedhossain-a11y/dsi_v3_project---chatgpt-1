#!/usr/bin/env python3
"""
DSI Global Remote Engineering Hiring Signal Collector V3

Goal:
Collect clean hiring intent data for DS Innovators, a Bangladesh based remote
engineering provider. The script does not enrich contacts and does not send
outreach. It only collects and classifies job posts.

Key rules:
1. Strict ICP never accepts unknown location.
2. Strict ICP never accepts weak remote unless full page text proves global remote.
3. Country restrictions, work authorization restrictions, hybrid, onsite, agency,
   stale, duplicate, and non core engineering roles are rejected.
4. Headcount bucket is required for strict ICP.
5. All source failures are logged.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
import tldextract
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from rapidfuzz import fuzz

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES = SCRIPT_DIR / "sources.yml"
OUTPUT_DIR = SCRIPT_DIR / "output"
LOG_DIR = SCRIPT_DIR / "logs"
TODAY_UTC = datetime.now(timezone.utc)
DATE_STAMP = TODAY_UTC.strftime("%Y-%m-%d")

STRICT_HEADCOUNT_BUCKETS = {"10 to 50", "51 to 100", "101 to 200"}
SECONDARY_HEADCOUNT_BUCKETS = {"201 to 500"}
TARGET_MARKETS = {
    "United States", "USA", "Canada", "United Kingdom", "UK", "Ireland",
    "Australia", "New Zealand", "Singapore", "Netherlands", "Germany",
    "Switzerland", "Sweden", "Norway", "Denmark", "Finland", "UAE",
    "United Arab Emirates",
}

CSV_COLUMNS = [
    "collected_date", "source_name", "source_type", "source_trust_score",
    "company_name", "company_domain", "company_website",
    "company_headcount_estimate", "company_headcount_bucket",
    "company_hq_country", "target_market_fit", "job_title", "role_family",
    "seniority", "job_url", "final_canonical_url", "ats_job_id",
    "posted_date", "days_old", "location_raw", "remote_classification",
    "country_restriction_status", "restriction_evidence",
    "global_remote_evidence", "timezone_evidence", "full_time_or_contract",
    "tech_stack_detected", "description_summary", "dsi_icp_score",
    "score_reasons", "duplicate_key", "verification_status",
]

ENGINEERING_ROLE_PATTERNS: Dict[str, List[str]] = {
    "backend": [
        "backend engineer", "back end engineer", "back-end engineer",
        "backend developer", "back end developer", "server engineer",
        "api engineer", "api developer",
    ],
    "frontend": [
        "frontend engineer", "front end engineer", "front-end engineer",
        "frontend developer", "react engineer", "react developer",
        "javascript engineer", "typescript engineer", "web engineer",
    ],
    "fullstack": [
        "full stack", "fullstack", "full-stack", "product engineer",
        "software engineer full stack", "software developer full stack",
    ],
    "mobile": [
        "mobile engineer", "mobile developer", "android engineer",
        "android developer", "ios engineer", "ios developer", "flutter developer",
        "react native", "swift developer", "kotlin developer",
    ],
    "devops": [
        "devops engineer", "cloud engineer", "platform engineer",
        "infrastructure engineer", "site reliability", "sre", "kubernetes engineer",
        "terraform engineer",
    ],
    "qa automation": [
        "qa automation", "automation engineer", "test automation", "sdet",
        "qa engineer", "quality engineer",
    ],
    "data engineering": [
        "data engineer", "analytics engineer", "data platform engineer",
        "etl engineer", "pipeline engineer",
    ],
    "ai ml": [
        "machine learning engineer", "ml engineer", "ai engineer",
        "llm engineer", "computer vision engineer", "nlp engineer",
    ],
    "security engineering": [
        "security engineer", "application security engineer", "cloud security engineer",
        "product security engineer",
    ],
    "software engineering": [
        "software engineer", "software developer", "python developer", "python engineer",
        "java developer", "java engineer", "node developer", "node.js developer",
        "node engineer", "php developer", "ruby developer", "rails developer",
        "golang engineer", "go developer", "rust developer", "c++ engineer",
        "c# engineer", ".net developer", "dotnet developer", "scala developer",
        "elixir developer", "application developer", "app developer",
    ],
    "technical lead": [
        "technical lead", "tech lead", "lead software engineer", "lead developer",
        "staff engineer", "principal engineer",
    ],
}

NON_ENGINEERING_REJECT_PATTERNS = [
    "customer support engineer", "technical support engineer", "support engineer",
    "help desk", "it support", "solutions engineer", "solution engineer",
    "solutions architect", "sales engineer", "pre sales", "presales",
    "engineering manager", "director of engineering", "vp engineering",
    "product manager", "project manager", "program manager", "scrum master",
    "business analyst", "data analyst", "ux designer", "ui designer",
    "graphic designer", "product designer", "recruiter", "talent acquisition",
    "intern", "student", "trainee", "apprentice", "werkstudent", "praktikum",
    "business developer", "marketing", "sales", "account executive",
    "operations", "finance", "legal", "hr", "human resources", "technical writer",
]

STRONG_GLOBAL_PATTERNS = [
    r"\bworldwide\b", r"\bwork from anywhere\b", r"\banywhere in the world\b",
    r"\bremote anywhere\b", r"\banywhere\b", r"\bglobal remote\b", r"\bglobally remote\b",
    r"\bopen globally\b", r"\bopen to candidates worldwide\b",
    r"\bno location restriction\b", r"\blocation independent\b",
    r"\bglobally distributed\b", r"\bfully distributed\b",
    r"\bhire from anywhere\b", r"\ball countries\b", r"\bany country\b",
    r"\bremote globally\b", r"\bopen to all locations\b",
]

WEAK_REMOTE_PATTERNS = [
    r"\bremote\b", r"\bfully remote\b", r"\bremote first\b", r"\bremote-first\b",
    r"\bdistributed\b", r"\basync\b", r"\bhome office\b", r"\bvirtual\b",
    r"\bflexible location\b", r"\bwork remotely\b",
]

HARD_REJECT_PATTERNS = [
    r"\bunited states only\b", r"\bus only\b", r"\busa only\b",
    r"\bus based only\b", r"\bus-based only\b", r"\bmust be in the us\b",
    r"\bmust be located in the us\b", r"\bmust reside in the us\b",
    r"\bus residents only\b", r"\bus citizen\b", r"\bus citizens only\b",
    r"\bremote\s*[-,(/]*\s*us\b", r"\bremote us only\b",
    r"\bnorth america only\b", r"\buk only\b", r"\buk-based only\b",
    r"\bunited kingdom only\b", r"\buk residents only\b",
    r"\bremote\s*[-,(/]*\s*uk\b", r"\bcanada only\b",
    r"\bcanada-based only\b", r"\bcanadian residents only\b",
    r"\bremote\s*[-,(/]*\s*canada\b", r"\beu only\b",
    r"\beurope only\b", r"\beuropean union only\b", r"\beu-based only\b",
    r"\bremote in europe\b", r"\bremote\s*[-,(/]*\s*europe\b",
    r"\bemea only\b", r"\bemea based\b", r"\bemea-only\b",
    r"\bapac only\b", r"\blatam only\b", r"\blatin america only\b",
    r"\bremote\s*[-,(/]*\s*latam\b", r"\baustralia only\b",
    r"\bnew zealand only\b", r"\bindia only\b",
    r"\bgermany only\b", r"\bfrance only\b", r"\bspain only\b",
    r"\bpoland only\b", r"\bportugal only\b", r"\bromania only\b",
    r"\bserbia only\b", r"\bukraine only\b", r"\bwork authorization required\b",
    r"\bauthorized to work in\b", r"\bmust be authorized to work\b",
    r"\blegally authorized to work\b", r"\bright to work in\b",
    r"\bwork permit required\b", r"\bno visa sponsorship\b",
    r"\bvisa sponsorship not available\b", r"\bvisa sponsorship is not available\b",
    r"\bnot able to sponsor\b", r"\bunable to sponsor\b",
    r"\bcannot sponsor\b", r"\bsponsorship not provided\b",
    r"\bmust have work authorization\b", r"\beligible to work in\b",
    r"\bmust be eligible to work in\b", r"\bmust be based in\b",
    r"\bmust live in\b", r"\bmust be located in\b", r"\bapplicants must be based in\b",
    r"\bapplicants must reside\b", r"\bmust reside in\b",
    r"\bmust be a citizen of\b", r"\bcitizenship required\b",
    r"\brestricted to residents of\b", r"\bonly open to residents\b",
    r"\bonly available in\b", r"\bhiring only in\b", r"\bhybrid\b",
    r"\bonsite\b", r"\bon-site\b", r"\boffice required\b", r"\bmust commute\b",
]

COUNTRY_LOCATION_ONLY_PATTERNS = [
    r"^united states$", r"^usa$", r"^canada$", r"^united kingdom$", r"^uk$",
    r"^germany$", r"^france$", r"^spain$", r"^poland$", r"^portugal$",
    r"^romania$", r"^serbia$", r"^ukraine$", r"^india$", r"^australia$",
    r"^new zealand$", r"^europe$", r"^emea$", r"^apac$", r"^latam$",
    r"\bremote,\s*germany\b", r"\bremote,\s*canada\b", r"\bremote,\s*uk\b",
    r"\bremote,\s*united states\b", r"\bremote,\s*europe\b",
]

AGENCY_TERMS = [
    "recruitment", "recruiting", "staffing", "headhunt", "placement agency",
    "talent marketplace", "talent solutions", "talent group", "talent agency",
    "hiring agency", "it staffing", "tech staffing", "staff augmentation",
    "body shop", "manpower", "randstad", "adecco", "hays", "michael page",
    "robert half", "kelly services", "insight global", "teksystems", "tek systems",
    "modis", "cybercoders", "cooper lomaz", "spectrum it", "lorien",
    "direct sourcing", "wing assistant", "recruiter", "consulting group",
]

ANON_COMPANY_TERMS = ["confidential", "undisclosed", "anonymous", "our client", "private client"]

TECH_TERMS = [
    "python", "javascript", "typescript", "react", "node", "node.js", "java", "php",
    "ruby", "rails", "golang", "go", "rust", "kotlin", "swift", "flutter",
    "aws", "gcp", "azure", "kubernetes", "docker", "terraform", "postgres",
    "postgresql", "mysql", "mongodb", "redis", "graphql", "rest api", "microservice",
    "ci/cd", "kafka", "spark", "llm", "machine learning", "ai", "data pipeline",
]

SAAS_SIGNALS = [
    "saas", "software", "platform", "developer tools", "api", "cloud", "data",
    "cybersecurity", "fintech", "healthtech", "logistics", "ecommerce",
    "open source", "product", "subscription", "b2b",
]

TIMEZONE_FLEX_SIGNALS = [
    "async", "asynchronous", "flexible hours", "work your own hours",
    "timezone flexible", "no timezone restriction", "globally distributed",
    "distributed team", "remote-first", "remote first",
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; DSIHiringSignalCollector/3.0; +https://www.dsinnovators.com/)",
    "Accept": "application/json,text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})

@dataclass
class SourceLog:
    source_name: str
    source_type: str
    attempted: int = 0
    success: int = 0
    raw_jobs_collected: int = 0
    passed_strict: int = 0
    passed_secondary: int = 0
    rejected: int = 0
    needs_verification: int = 0
    error_count: int = 0
    last_error: str = ""
    last_status_code: str = ""

@dataclass
class RawJob:
    source_name: str
    source_type: str
    source_trust_score: int
    company_name: str
    job_title: str
    job_url: str
    location_raw: str = ""
    description_html: str = ""
    description_text: str = ""
    posted_raw: Any = ""
    posted_date: str = ""
    ats_job_id: str = ""
    full_time_or_contract: str = ""
    salary: str = ""
    company_domain: str = ""
    company_website: str = ""
    company_headcount_estimate: str = ""
    company_headcount_bucket: str = "unknown"
    company_hq_country: str = ""
    source_meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ClassifiedJob:
    collected_date: str
    source_name: str
    source_type: str
    source_trust_score: int
    company_name: str
    company_domain: str
    company_website: str
    company_headcount_estimate: str
    company_headcount_bucket: str
    company_hq_country: str
    target_market_fit: str
    job_title: str
    role_family: str
    seniority: str
    job_url: str
    final_canonical_url: str
    ats_job_id: str
    posted_date: str
    days_old: int
    location_raw: str
    remote_classification: str
    country_restriction_status: str
    restriction_evidence: str
    global_remote_evidence: str
    timezone_evidence: str
    full_time_or_contract: str
    tech_stack_detected: str
    description_summary: str
    dsi_icp_score: int
    score_reasons: str
    duplicate_key: str
    verification_status: str
    bucket: str = "needs_verification"
    reject_reason: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def clean_text(raw: Any) -> str:
    raw = safe_str(raw)
    if not raw:
        return ""
    try:
        soup = BeautifulSoup(raw, "html.parser")
        text = soup.get_text(" ")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def lower_clean(raw: Any) -> str:
    return clean_text(raw).lower()


def parse_date(raw_date: Any) -> Tuple[str, int]:
    if raw_date in [None, "", 0, "0"]:
        return "", 999
    try:
        if isinstance(raw_date, (int, float)):
            value = raw_date / 1000 if raw_date > 10_000_000_000 else raw_date
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            dt = dateparser.parse(str(raw_date))
            if dt is None:
                return "", 999
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
        days = max(0, (TODAY_UTC - dt).days)
        return dt.strftime("%Y-%m-%d"), days
    except Exception:
        return "", 999


def registered_domain(url_or_domain: str) -> str:
    value = safe_str(url_or_domain)
    if not value:
        return ""
    if "@" in value:
        value = value.split("@")[-1]
    if not value.startswith(("http://", "https://")):
        value_for_parse = "https://" + value
    else:
        value_for_parse = value
    parsed = urlparse(value_for_parse)
    host = parsed.netloc or parsed.path.split("/")[0]
    ext = tldextract.extract(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}".lower()
    return host.lower().replace("www.", "")


def normalize_company_name(name: str) -> str:
    name = safe_str(name).lower()
    name = re.sub(r"\b(inc|llc|ltd|limited|gmbh|bv|pty|co|company|corp|corporation)\b", " ", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def normalize_title(title: str) -> str:
    title = safe_str(title).lower()
    title = re.sub(r"\b(senior|sr|lead|principal|staff|junior|jr|mid|remote|worldwide|global|ii|iii|iv)\b", " ", title)
    title = re.sub(r"[^a-z0-9+#. ]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def evidence_match(patterns: Iterable[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(0)[:160]
    return ""


def is_agency_or_anonymous(company: str) -> Tuple[bool, str]:
    c = safe_str(company).lower()
    for term in ANON_COMPANY_TERMS:
        if term in c:
            return True, term
    for term in AGENCY_TERMS:
        if term in c:
            return True, term
    return False, ""


def role_family_for_title(title: str) -> Tuple[str, str]:
    t = safe_str(title).lower()
    reject = evidence_match([re.escape(x) for x in NON_ENGINEERING_REJECT_PATTERNS], t)
    if reject:
        return "", reject
    for family, patterns in ENGINEERING_ROLE_PATTERNS.items():
        for pattern in patterns:
            if pattern in t:
                return family, ""
    return "", "no core DSI engineering role keyword found"


def seniority_for_title(title: str) -> str:
    t = safe_str(title).lower()
    if re.search(r"\b(principal|staff|architect)\b", t):
        return "principal_staff"
    if re.search(r"\b(senior|sr\.?|lead)\b", t):
        return "senior"
    if re.search(r"\b(junior|jr\.?|intern|trainee|apprentice)\b", t):
        return "junior_rejected"
    return "mid_or_unspecified"


def classify_remote(location: str, full_text: str) -> Tuple[str, str, str, str]:
    loc = safe_str(location).lower()
    text = safe_str(full_text).lower()
    combined = f"{loc} {text}"

    reject_evidence = evidence_match(HARD_REJECT_PATTERNS, combined)
    if reject_evidence:
        return "REJECT", "restricted", reject_evidence, ""

    country_evidence = evidence_match(COUNTRY_LOCATION_ONLY_PATTERNS, loc)
    if country_evidence:
        return "REJECT", "restricted", country_evidence, ""

    strong_location = evidence_match(STRONG_GLOBAL_PATTERNS, loc)
    strong_text = evidence_match(STRONG_GLOBAL_PATTERNS, text)
    weak_location = evidence_match(WEAK_REMOTE_PATTERNS, loc)
    weak_text = evidence_match(WEAK_REMOTE_PATTERNS, text)

    if strong_location or strong_text:
        return "STRONG_GLOBAL", "no restriction found", "", strong_location or strong_text
    if weak_location or weak_text:
        return "WEAK_REMOTE", "not proven", "", weak_location or weak_text
    return "UNKNOWN", "not proven", "", ""


def detect_timezone_evidence(text: str) -> str:
    return evidence_match([re.escape(x) for x in TIMEZONE_FLEX_SIGNALS], safe_str(text).lower())


def detect_tech_stack(text: str) -> str:
    lower = safe_str(text).lower()
    found = []
    for term in TECH_TERMS:
        if re.search(r"\b" + re.escape(term) + r"\b", lower):
            found.append(term)
    return ", ".join(sorted(set(found))[:15])


def target_market_fit(hq_country: str, source_meta: Dict[str, Any], text: str) -> str:
    hq = safe_str(hq_country)
    if hq in TARGET_MARKETS:
        return "yes"
    if safe_str(source_meta.get("target_market_fit")).lower() in {"yes", "true"}:
        return "yes"
    lower = safe_str(text).lower()
    if any(term in lower for term in ["global customers", "us customers", "north american customers", "english-speaking", "english speaking"]):
        return "likely"
    return "unknown"


def is_saas_or_product(source_meta: Dict[str, Any], text: str) -> bool:
    if safe_str(source_meta.get("company_type")).lower() in {"saas", "b2b saas", "developer tools", "ai saas", "product"}:
        return True
    lower = safe_str(text).lower()
    return any(signal in lower for signal in SAAS_SIGNALS)


def company_website_from_domain(domain: str) -> str:
    domain = safe_str(domain)
    return f"https://{domain}" if domain else ""


def canonical_url(raw_url: str, base_url: str = "") -> str:
    url = safe_str(raw_url)
    if not url:
        return ""
    if base_url:
        url = urljoin(base_url, url)
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlparse(url)
    query_keep = []
    return parsed._replace(query="&".join(query_keep), fragment="").geturl()


def request_url(url: str, source_log: Optional[SourceLog] = None, source_name: str = "", timeout: int = 25) -> Optional[requests.Response]:
    try:
        if source_log:
            source_log.attempted += 1
        response = SESSION.get(url, timeout=timeout)
        if source_log:
            source_log.last_status_code = str(response.status_code)
        response.raise_for_status()
        if source_log:
            source_log.success += 1
        return response
    except Exception as exc:
        if source_log:
            source_log.error_count += 1
            source_log.last_error = f"{source_name or source_log.source_name}: {type(exc).__name__}: {exc}"
        return None


def fetch_full_page_text(url: str, source_log: Optional[SourceLog] = None) -> Tuple[str, str, str]:
    """Return clean text, final url, verification status."""
    if not url:
        return "", "", "missing_url"
    response = request_url(url, source_log=source_log, timeout=20)
    if response is None:
        return "", url, "page_fetch_failed"
    final_url = response.url or url
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type and not response.text:
        return "", final_url, "non_html_response"
    return clean_text(response.text), final_url, "full_page_verified"


def extract_company_url_from_html(html: str, fallback_url: str) -> str:
    try:
        soup = BeautifulSoup(html, "html.parser")
        for selector in ["a[href*='apply']", "a[href*='careers']", "a[href*='jobs']"]:
            link = soup.select_one(selector)
            if link and link.get("href"):
                href = canonical_url(link.get("href"), fallback_url)
                if registered_domain(href) not in {registered_domain(fallback_url), ""}:
                    return href
    except Exception:
        pass
    return fallback_url


def score_job(job: RawJob, role_family: str, remote_class: str, country_status: str,
              restriction_evidence: str, global_evidence: str, timezone_evidence: str,
              full_text: str, verification_status: str) -> Tuple[int, List[str], str]:
    score = 0
    reasons: List[str] = []

    if remote_class == "STRONG_GLOBAL":
        score += 30
        reasons.append("proven remote worldwide +30")
    elif remote_class == "WEAK_REMOTE":
        reasons.append("weak remote only +0 for strict")
    else:
        reasons.append("unknown remote +0")

    if country_status == "no restriction found" and remote_class == "STRONG_GLOBAL" and not restriction_evidence:
        score += 15
        reasons.append("no country restriction found +15")

    if timezone_evidence:
        score += 5
        reasons.append("timezone or async friendly +5")

    if job.company_headcount_bucket in STRICT_HEADCOUNT_BUCKETS:
        score += 20
        reasons.append("headcount 10 to 200 +20")
    elif job.company_headcount_bucket in SECONDARY_HEADCOUNT_BUCKETS:
        score += 10
        reasons.append("headcount 201 to 500 +10")

    market_fit = target_market_fit(job.company_hq_country, job.source_meta, full_text)
    if market_fit == "yes":
        score += 10
        reasons.append("target English market +10")
    elif market_fit == "likely":
        score += 5
        reasons.append("likely target English market +5")

    if is_saas_or_product(job.source_meta, full_text):
        score += 5
        reasons.append("software or SaaS company +5")

    if role_family:
        score += 15
        reasons.append("core DSI engineering role +15")

    _, days = parse_date(job.posted_raw or job.posted_date)
    if days <= 7:
        score += 10
        reasons.append("posted within 7 days +10")
    elif days <= 14:
        score += 7
        reasons.append("posted within 14 days +7")
    elif days <= 21:
        score += 5
        reasons.append("posted within 21 days +5")

    if job.source_type in {"greenhouse", "lever", "ashby", "workable", "smartrecruiters", "teamtailor", "recruitee", "company_career"}:
        score += 10
        reasons.append("official ATS or company career source +10")
    elif job.source_type in {"rss", "remote_board_api"} and verification_status in {"full_page_verified", "trusted_board_only"}:
        score += 6
        reasons.append("trusted board with verification +6")

    return min(score, 100), reasons, market_fit


def build_duplicate_key(job: RawJob, role_family: str, final_url: str) -> str:
    domain = job.company_domain or registered_domain(final_url) or registered_domain(job.job_url)
    title_norm = normalize_title(job.job_title)
    if job.ats_job_id:
        raw = f"ats:{job.source_type}:{job.company_name}:{job.ats_job_id}"
    elif final_url:
        raw = f"url:{canonical_url(final_url)}"
    else:
        raw = f"company:{domain or normalize_company_name(job.company_name)}:{role_family}:{title_norm}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def classify_job(job: RawJob, source_log: SourceLog, verify_pages: bool = True) -> ClassifiedJob:
    posted_date, days = parse_date(job.posted_raw or job.posted_date)
    if not job.posted_date:
        job.posted_date = posted_date

    source_text = f"{job.location_raw} {job.description_text or clean_text(job.description_html)}"
    full_text = clean_text(job.description_text or job.description_html)
    final_url = canonical_url(job.job_url)
    verification_status = "source_content_only"

    if verify_pages and job.job_url:
        page_text, final_fetched_url, page_status = fetch_full_page_text(job.job_url, source_log)
        if page_text:
            full_text = f"{full_text} {page_text}".strip()
        final_url = canonical_url(final_fetched_url or job.job_url)
        verification_status = page_status
    elif job.source_type in {"greenhouse", "lever", "ashby", "workable", "smartrecruiters", "teamtailor", "recruitee"}:
        verification_status = "official_ats_content"
    elif job.source_type in {"rss", "remote_board_api"}:
        verification_status = "trusted_board_only"

    if not job.company_domain:
        job.company_domain = registered_domain(job.company_website) or registered_domain(final_url) or registered_domain(job.job_url)
    if not job.company_website:
        job.company_website = company_website_from_domain(job.company_domain)

    role_family, role_reject = role_family_for_title(job.job_title)
    seniority = seniority_for_title(job.job_title)
    is_agency, agency_evidence = is_agency_or_anonymous(job.company_name)
    remote_class, country_status, restriction_evidence, global_evidence = classify_remote(job.location_raw, full_text)
    timezone_evidence = detect_timezone_evidence(full_text)
    tech_stack = detect_tech_stack(full_text)
    score, reasons, market_fit = score_job(
        job, role_family, remote_class, country_status, restriction_evidence,
        global_evidence, timezone_evidence, full_text, verification_status
    )

    reject_reason = ""
    bucket = "needs_verification"

    if not final_url:
        reject_reason = "no URL"
    elif role_reject:
        reject_reason = f"role rejected: {role_reject}"
    elif seniority == "junior_rejected":
        reject_reason = "junior, intern, or trainee role"
    elif is_agency:
        reject_reason = f"agency or anonymous company: {agency_evidence}"
    elif days > 30:
        reject_reason = f"stale job older than 30 days: {days} days"
    elif remote_class == "REJECT" or restriction_evidence:
        reject_reason = f"country or location restricted: {restriction_evidence}"
    elif remote_class == "UNKNOWN":
        reject_reason = "unknown remote status, not allowed into final"
    elif job.company_headcount_bucket == "1 to 9":
        reject_reason = "company headcount under 10"
    elif job.company_headcount_bucket == "501 plus":
        reject_reason = "company headcount over 500"

    if reject_reason:
        bucket = "rejected"
        reasons.append(f"REJECT: {reject_reason}")
    else:
        strict_ok = (
            score >= 80 and
            remote_class == "STRONG_GLOBAL" and
            country_status == "no restriction found" and
            job.company_headcount_bucket in STRICT_HEADCOUNT_BUCKETS and
            days <= 21 and
            final_url and
            role_family and
            not restriction_evidence and
            verification_status in {"full_page_verified", "official_ats_content", "trusted_board_only", "source_content_only"}
        )
        if strict_ok:
            bucket = "strict_icp"
        elif score >= 60 and remote_class in {"STRONG_GLOBAL", "WEAK_REMOTE"} and days <= 30:
            if job.company_headcount_bucket in SECONDARY_HEADCOUNT_BUCKETS or remote_class == "STRONG_GLOBAL":
                bucket = "secondary"
            else:
                bucket = "needs_verification"
        else:
            bucket = "needs_verification"

    duplicate_key = build_duplicate_key(job, role_family, final_url)
    summary = clean_text(full_text)[:400]

    return ClassifiedJob(
        collected_date=DATE_STAMP,
        source_name=job.source_name,
        source_type=job.source_type,
        source_trust_score=job.source_trust_score,
        company_name=job.company_name,
        company_domain=job.company_domain,
        company_website=job.company_website,
        company_headcount_estimate=job.company_headcount_estimate,
        company_headcount_bucket=job.company_headcount_bucket or "unknown",
        company_hq_country=job.company_hq_country,
        target_market_fit=market_fit,
        job_title=job.job_title,
        role_family=role_family,
        seniority=seniority,
        job_url=job.job_url,
        final_canonical_url=final_url,
        ats_job_id=job.ats_job_id,
        posted_date=posted_date,
        days_old=days,
        location_raw=job.location_raw,
        remote_classification=remote_class,
        country_restriction_status=country_status,
        restriction_evidence=restriction_evidence,
        global_remote_evidence=global_evidence,
        timezone_evidence=timezone_evidence,
        full_time_or_contract=job.full_time_or_contract,
        tech_stack_detected=tech_stack,
        description_summary=summary,
        dsi_icp_score=score,
        score_reasons=" | ".join(reasons),
        duplicate_key=duplicate_key,
        verification_status=verification_status,
        bucket=bucket,
        reject_reason=reject_reason,
    )


def make_raw_job(source: Dict[str, Any], **kwargs: Any) -> RawJob:
    meta = source.get("company_meta", {}) or {}
    company_name = kwargs.get("company_name") or source.get("company_name") or source.get("board") or ""
    company_domain = kwargs.get("company_domain") or source.get("company_domain") or meta.get("company_domain") or ""
    company_website = kwargs.get("company_website") or source.get("company_website") or meta.get("company_website") or company_website_from_domain(company_domain)
    return RawJob(
        source_name=source.get("name", "unknown"),
        source_type=source.get("type", "unknown"),
        source_trust_score=int(source.get("trust_score", 0)),
        company_name=safe_str(company_name),
        job_title=safe_str(kwargs.get("job_title", "")),
        job_url=canonical_url(safe_str(kwargs.get("job_url", ""))),
        location_raw=safe_str(kwargs.get("location_raw", "")),
        description_html=safe_str(kwargs.get("description_html", "")),
        description_text=clean_text(kwargs.get("description_text", kwargs.get("description_html", ""))),
        posted_raw=kwargs.get("posted_raw", ""),
        posted_date=safe_str(kwargs.get("posted_date", "")),
        ats_job_id=safe_str(kwargs.get("ats_job_id", "")),
        full_time_or_contract=safe_str(kwargs.get("full_time_or_contract", "")),
        salary=safe_str(kwargs.get("salary", "")),
        company_domain=registered_domain(company_domain),
        company_website=company_website,
        company_headcount_estimate=safe_str(meta.get("headcount_estimate", source.get("headcount_estimate", ""))),
        company_headcount_bucket=safe_str(meta.get("headcount_bucket", source.get("headcount_bucket", "unknown"))) or "unknown",
        company_hq_country=safe_str(meta.get("hq_country", source.get("hq_country", ""))),
        source_meta=meta,
    )


def fetch_remotive(source: Dict[str, Any], log: SourceLog) -> List[RawJob]:
    rows: List[RawJob] = []
    for category in source.get("categories", ["software-dev"]):
        url = f"https://remotive.com/api/remote-jobs?category={category}&limit={int(source.get('limit', 500))}"
        response = request_url(url, log, source.get("name", "Remotive"))
        if not response:
            continue
        data = response.json()
        for j in data.get("jobs", []):
            rows.append(make_raw_job(
                source,
                company_name=j.get("company_name", ""),
                job_title=j.get("title", ""),
                job_url=j.get("url", ""),
                location_raw=j.get("candidate_required_location", ""),
                description_html=j.get("description", ""),
                posted_raw=j.get("publication_date", ""),
                ats_job_id=str(j.get("id", "")),
                full_time_or_contract=j.get("job_type", ""),
                salary=j.get("salary", ""),
            ))
        time.sleep(float(source.get("delay_seconds", 0.7)))
    return rows


def fetch_jobicy(source: Dict[str, Any], log: SourceLog) -> List[RawJob]:
    rows: List[RawJob] = []
    for industry in source.get("industries", ["engineering"]):
        url = f"https://jobicy.com/api/v2/remote-jobs?count={int(source.get('limit', 50))}&industry={industry}"
        response = request_url(url, log, source.get("name", "Jobicy"))
        if not response:
            continue
        data = response.json()
        for j in data.get("jobs", []):
            rows.append(make_raw_job(
                source,
                company_name=j.get("companyName", ""),
                job_title=j.get("jobTitle", ""),
                job_url=j.get("url", ""),
                location_raw=j.get("jobGeo", ""),
                description_html=j.get("jobDescription", ""),
                posted_raw=j.get("pubDate", ""),
                ats_job_id=str(j.get("id", "")),
                full_time_or_contract=j.get("jobType", ""),
                salary=str(j.get("annualSalaryMin", "")),
            ))
        time.sleep(float(source.get("delay_seconds", 0.7)))
    return rows


def fetch_remoteok(source: Dict[str, Any], log: SourceLog) -> List[RawJob]:
    rows: List[RawJob] = []
    response = request_url("https://remoteok.com/api", log, source.get("name", "RemoteOK"), timeout=30)
    if not response:
        return rows
    data = response.json()
    for j in data[1:]:
        company_name = j.get("company", "")
        rows.append(make_raw_job(
            source,
            company_name=company_name,
            company_domain=j.get("company_logo", ""),
            job_title=j.get("position", ""),
            job_url=j.get("url", ""),
            location_raw=j.get("location", ""),
            description_html=j.get("description", ""),
            posted_raw=j.get("epoch", ""),
            ats_job_id=str(j.get("id", "")),
            full_time_or_contract="Full-time",
            salary=safe_str(j.get("salary", "")),
        ))
    return rows


def fetch_arbeitnow(source: Dict[str, Any], log: SourceLog) -> List[RawJob]:
    rows: List[RawJob] = []
    max_pages = int(source.get("max_pages", 10))
    for page in range(1, max_pages + 1):
        response = request_url(f"https://arbeitnow.com/api/job-board-api?page={page}", log, source.get("name", "Arbeitnow"))
        if not response:
            break
        data = response.json()
        items = data.get("data", [])
        if not items:
            break
        for j in items:
            if not j.get("remote", False):
                continue
            rows.append(make_raw_job(
                source,
                company_name=j.get("company_name", ""),
                job_title=j.get("title", ""),
                job_url=j.get("url", ""),
                location_raw=j.get("location", ""),
                description_html=j.get("description", ""),
                posted_raw=j.get("created_at", ""),
                ats_job_id=str(j.get("slug", "")),
                full_time_or_contract=", ".join(j.get("job_types", []) or []),
            ))
        time.sleep(float(source.get("delay_seconds", 0.8)))
    return rows


def fetch_himalayas(source: Dict[str, Any], log: SourceLog) -> List[RawJob]:
    rows: List[RawJob] = []
    for query in source.get("queries", ["engineer"]):
        url = f"https://himalayas.app/jobs/api?q={query}&limit={int(source.get('limit', 100))}&remote=true"
        response = request_url(url, log, source.get("name", "Himalayas"))
        if not response:
            continue
        data = response.json()
        for j in data.get("jobs", []):
            loc = j.get("locationRestrictions", "") or ""
            if isinstance(loc, list):
                loc = ", ".join(loc)
            company_obj = j.get("company", {}) if isinstance(j.get("company"), dict) else {}
            rows.append(make_raw_job(
                source,
                company_name=j.get("companyName", "") or company_obj.get("name", ""),
                company_domain=company_obj.get("website", ""),
                job_title=j.get("title", ""),
                job_url=j.get("applicationLink", "") or j.get("jobUrl", "") or j.get("url", ""),
                location_raw=loc,
                description_html=j.get("description", ""),
                posted_raw=j.get("createdAt", ""),
                ats_job_id=str(j.get("id", "")),
                full_time_or_contract=j.get("employmentType", ""),
                salary=str(j.get("salaryRange", "")),
            ))
        time.sleep(float(source.get("delay_seconds", 0.7)))
    return rows


def fetch_rss(source: Dict[str, Any], log: SourceLog) -> List[RawJob]:
    rows: List[RawJob] = []
    response = request_url(source.get("url", ""), log, source.get("name", "RSS"))
    if not response:
        return rows
    try:
        soup = BeautifulSoup(response.content, "xml")
        for item in soup.find_all("item"):
            raw_title = clean_text(item.title.text if item.title else "")
            link = item.link.text if item.link else ""
            description = str(item.description.text if item.description else "")
            pub_date = item.pubDate.text if item.pubDate else ""
            company = ""
            title = raw_title
            if source.get("title_format") == "company_colon_title" and ":" in raw_title:
                parts = raw_title.split(":", 1)
                company = parts[0].strip()
                title = parts[1].strip()
            rows.append(make_raw_job(
                source,
                company_name=company or source.get("company_name", ""),
                job_title=title,
                job_url=link,
                location_raw=source.get("default_location", ""),
                description_html=description,
                posted_raw=pub_date,
                ats_job_id=hashlib.sha1(link.encode("utf-8")).hexdigest()[:16],
                full_time_or_contract="Full-time",
            ))
    except Exception as exc:
        log.error_count += 1
        log.last_error = f"RSS parse error: {exc}"
    return rows


def fetch_greenhouse(source: Dict[str, Any], log: SourceLog) -> List[RawJob]:
    board = source.get("board", "")
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    response = request_url(url, log, source.get("name", "Greenhouse"), timeout=20)
    rows: List[RawJob] = []
    if not response:
        return rows
    data = response.json()
    for j in data.get("jobs", []):
        location_obj = j.get("location", {}) or {}
        location = location_obj.get("name", "") if isinstance(location_obj, dict) else safe_str(location_obj)
        rows.append(make_raw_job(
            source,
            job_title=j.get("title", ""),
            job_url=j.get("absolute_url", ""),
            location_raw=location,
            description_html=j.get("content", ""),
            posted_raw=j.get("updated_at", ""),
            ats_job_id=str(j.get("id", "")),
            full_time_or_contract="Full-time",
        ))
    return rows


def fetch_lever(source: Dict[str, Any], log: SourceLog) -> List[RawJob]:
    board = source.get("board", "")
    url = f"https://api.lever.co/v0/postings/{board}?mode=json"
    response = request_url(url, log, source.get("name", "Lever"), timeout=20)
    rows: List[RawJob] = []
    if not response:
        return rows
    data = response.json()
    if not isinstance(data, list):
        return rows
    for j in data:
        categories = j.get("categories", {}) or {}
        loc = categories.get("location", "") or categories.get("allLocations", "") or ""
        if isinstance(loc, list):
            loc = ", ".join(loc)
        desc = j.get("descriptionPlain", "") or j.get("description", "")
        rows.append(make_raw_job(
            source,
            job_title=j.get("text", ""),
            job_url=j.get("hostedUrl", ""),
            location_raw=loc,
            description_html=desc,
            posted_raw=j.get("createdAt", ""),
            ats_job_id=str(j.get("id", "")),
            full_time_or_contract=categories.get("commitment", ""),
        ))
    return rows


def fetch_ashby(source: Dict[str, Any], log: SourceLog) -> List[RawJob]:
    board = source.get("board", "")
    url = f"https://jobs.ashbyhq.com/api/non-user-facing/job-board/{board}/posting-group/published"
    response = request_url(url, log, source.get("name", "Ashby"), timeout=20)
    rows: List[RawJob] = []
    if not response:
        return rows
    data = response.json()
    postings = data.get("jobPostings", []) or []
    for j in postings:
        rows.append(make_raw_job(
            source,
            job_title=j.get("title", ""),
            job_url=j.get("jobUrl", "") or j.get("applyUrl", ""),
            location_raw=j.get("locationName", "") or j.get("location", ""),
            description_html=j.get("descriptionHtml", "") or j.get("description", ""),
            posted_raw=j.get("publishedAt", ""),
            ats_job_id=str(j.get("id", "")),
            full_time_or_contract=j.get("employmentType", ""),
        ))
    return rows


def fetch_smartrecruiters(source: Dict[str, Any], log: SourceLog) -> List[RawJob]:
    company_id = source.get("company_id") or source.get("board", "")
    url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
    response = request_url(url, log, source.get("name", "SmartRecruiters"), timeout=20)
    rows: List[RawJob] = []
    if not response:
        return rows
    data = response.json()
    for j in data.get("content", []):
        ref = j.get("ref", "") or j.get("id", "")
        job_url = j.get("jobAd", {}).get("sections", {}).get("jobUrl", "") or j.get("url", "") or ref
        loc = j.get("location", {}) or {}
        location = loc.get("city", "") or loc.get("country", "") if isinstance(loc, dict) else safe_str(loc)
        rows.append(make_raw_job(
            source,
            job_title=j.get("name", ""),
            job_url=job_url,
            location_raw=location,
            description_html=json.dumps(j),
            posted_raw=j.get("releasedDate", ""),
            ats_job_id=str(j.get("id", ref)),
            full_time_or_contract=j.get("typeOfEmployment", {}).get("label", "") if isinstance(j.get("typeOfEmployment"), dict) else "",
        ))
    return rows


def fetch_generic_html(source: Dict[str, Any], log: SourceLog) -> List[RawJob]:
    """Conservative HTML parser for simple job listing pages. Only use when selectors are provided."""
    rows: List[RawJob] = []
    url = source.get("url", "")
    response = request_url(url, log, source.get("name", "HTML"), timeout=25)
    if not response:
        return rows
    soup = BeautifulSoup(response.text, "html.parser")
    item_selector = source.get("item_selector", "")
    title_selector = source.get("title_selector", "")
    link_selector = source.get("link_selector", "") or title_selector
    if not item_selector or not title_selector:
        log.error_count += 1
        log.last_error = "generic_html source missing selectors"
        return rows
    for item in soup.select(item_selector)[: int(source.get("max_items", 100))]:
        title_el = item.select_one(title_selector)
        link_el = item.select_one(link_selector)
        if not title_el:
            continue
        title = clean_text(title_el.get_text(" "))
        href = link_el.get("href") if link_el and link_el.has_attr("href") else ""
        job_url = canonical_url(href, url)
        loc = clean_text(item.select_one(source.get("location_selector", "")).get_text(" ")) if source.get("location_selector") and item.select_one(source.get("location_selector", "")) else source.get("default_location", "")
        rows.append(make_raw_job(
            source,
            company_name=source.get("company_name", ""),
            job_title=title,
            job_url=job_url,
            location_raw=loc,
            description_html=str(item),
            posted_raw="",
            ats_job_id=hashlib.sha1(job_url.encode("utf-8")).hexdigest()[:16],
        ))
    return rows

FETCHERS = {
    "remotive_api": fetch_remotive,
    "jobicy_api": fetch_jobicy,
    "remoteok_api": fetch_remoteok,
    "arbeitnow_api": fetch_arbeitnow,
    "himalayas_api": fetch_himalayas,
    "rss": fetch_rss,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "generic_html": fetch_generic_html,
}


def load_sources(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("sources.yml must contain a list under 'sources'")
    return [s for s in sources if s.get("enabled", True)]


def fetch_all_sources(sources: List[Dict[str, Any]]) -> Tuple[List[RawJob], Dict[str, SourceLog]]:
    all_jobs: List[RawJob] = []
    logs: Dict[str, SourceLog] = {}
    for idx, source in enumerate(sources, start=1):
        name = source.get("name", f"source_{idx}")
        source_type = source.get("type", "unknown")
        log = SourceLog(source_name=name, source_type=source_type)
        logs[name] = log
        fetcher = FETCHERS.get(source_type)
        print(f"[{idx}/{len(sources)}] {name} ({source_type})")
        if not fetcher:
            log.error_count += 1
            log.last_error = f"unsupported source type: {source_type}"
            print(f"  unsupported source type")
            continue
        try:
            rows = fetcher(source, log)
            log.raw_jobs_collected = len(rows)
            all_jobs.extend(rows)
            print(f"  raw jobs: {len(rows)}")
        except Exception as exc:
            log.error_count += 1
            log.last_error = f"fatal fetch error: {type(exc).__name__}: {exc}"
            print(f"  ERROR: {log.last_error}")
        time.sleep(float(source.get("delay_seconds", 0.25)))
    return all_jobs, logs


def classify_all(raw_jobs: List[RawJob], logs: Dict[str, SourceLog], verify_pages: bool) -> List[ClassifiedJob]:
    classified: List[ClassifiedJob] = []
    for i, job in enumerate(raw_jobs, start=1):
        log = logs.get(job.source_name) or SourceLog(job.source_name, job.source_type)
        try:
            result = classify_job(job, log, verify_pages=verify_pages)
            classified.append(result)
        except Exception as exc:
            reject = ClassifiedJob(
                collected_date=DATE_STAMP,
                source_name=job.source_name,
                source_type=job.source_type,
                source_trust_score=job.source_trust_score,
                company_name=job.company_name,
                company_domain=job.company_domain,
                company_website=job.company_website,
                company_headcount_estimate=job.company_headcount_estimate,
                company_headcount_bucket=job.company_headcount_bucket,
                company_hq_country=job.company_hq_country,
                target_market_fit="unknown",
                job_title=job.job_title,
                role_family="",
                seniority="",
                job_url=job.job_url,
                final_canonical_url=job.job_url,
                ats_job_id=job.ats_job_id,
                posted_date=job.posted_date,
                days_old=999,
                location_raw=job.location_raw,
                remote_classification="ERROR",
                country_restriction_status="unknown",
                restriction_evidence="",
                global_remote_evidence="",
                timezone_evidence="",
                full_time_or_contract=job.full_time_or_contract,
                tech_stack_detected="",
                description_summary="",
                dsi_icp_score=0,
                score_reasons=f"classifier error: {type(exc).__name__}: {exc}",
                duplicate_key="",
                verification_status="error",
                bucket="rejected",
                reject_reason=f"classifier error: {type(exc).__name__}: {exc}",
            )
            classified.append(reject)
            log.error_count += 1
            log.last_error = reject.reject_reason
        if i % 100 == 0:
            print(f"  classified {i}/{len(raw_jobs)}")
    return classified


def apply_company_role_boost(rows: List[ClassifiedJob]) -> None:
    counts: Dict[str, int] = {}
    for row in rows:
        if row.bucket in {"strict_icp", "secondary", "needs_verification"}:
            key = row.company_domain or normalize_company_name(row.company_name)
            counts[key] = counts.get(key, 0) + 1
    for row in rows:
        key = row.company_domain or normalize_company_name(row.company_name)
        if counts.get(key, 0) >= 2 and row.bucket != "rejected":
            if row.dsi_icp_score <= 95:
                row.dsi_icp_score += 5
                row.score_reasons += " | multiple matching roles at company +5"


def deduplicate_rows(rows: List[ClassifiedJob]) -> Tuple[List[ClassifiedJob], List[ClassifiedJob]]:
    apply_company_role_boost(rows)
    kept: List[ClassifiedJob] = []
    duplicates: List[ClassifiedJob] = []
    seen: Dict[str, ClassifiedJob] = {}

    sorted_rows = sorted(rows, key=lambda r: (r.dsi_icp_score, r.source_trust_score), reverse=True)
    for row in sorted_rows:
        if row.bucket == "rejected":
            kept.append(row)
            continue
        key = row.duplicate_key or build_fallback_duplicate_key(row)
        duplicate_of = seen.get(key)
        fuzzy_dup = None
        if not duplicate_of:
            fuzzy_dup = find_fuzzy_duplicate(row, seen.values())
        if duplicate_of or fuzzy_dup:
            row.bucket = "rejected"
            row.reject_reason = "duplicate"
            row.score_reasons += " | REJECT: duplicate"
            duplicates.append(row)
            kept.append(row)
            continue
        seen[key] = row
        kept.append(row)
    return kept, duplicates


def build_fallback_duplicate_key(row: ClassifiedJob) -> str:
    raw = f"{row.company_domain or normalize_company_name(row.company_name)}:{row.role_family}:{normalize_title(row.job_title)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def find_fuzzy_duplicate(row: ClassifiedJob, existing: Iterable[ClassifiedJob]) -> Optional[ClassifiedJob]:
    row_company = row.company_domain or normalize_company_name(row.company_name)
    row_title = normalize_title(row.job_title)
    for other in existing:
        other_company = other.company_domain or normalize_company_name(other.company_name)
        if row_company != other_company:
            continue
        if row.role_family != other.role_family:
            continue
        if fuzz.token_sort_ratio(row_title, normalize_title(other.job_title)) >= 92:
            return other
    return None


def rows_to_dataframe(rows: List[ClassifiedJob]) -> pd.DataFrame:
    data = []
    for row in rows:
        d = asdict(row)
        for extra in ["bucket", "reject_reason"]:
            d.pop(extra, None)
        data.append(d)
    df = pd.DataFrame(data)
    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[CSV_COLUMNS]


def rejected_to_dataframe(rows: List[ClassifiedJob]) -> pd.DataFrame:
    data = []
    for row in rows:
        d = asdict(row)
        data.append(d)
    df = pd.DataFrame(data)
    if df.empty:
        return pd.DataFrame(columns=CSV_COLUMNS + ["bucket", "reject_reason"])
    return df


def company_summary_dataframe(rows: List[ClassifiedJob]) -> pd.DataFrame:
    usable = [r for r in rows if r.bucket in {"strict_icp", "secondary", "needs_verification"}]
    if not usable:
        return pd.DataFrame(columns=[
            "company_name", "company_domain", "headcount_bucket", "hq_country",
            "open_matched_roles", "best_score", "strong_global_roles_count",
            "fresh_roles_count", "best_role", "sources_found", "highest_trust_url",
        ])
    buckets: Dict[str, List[ClassifiedJob]] = {}
    for row in usable:
        key = row.company_domain or normalize_company_name(row.company_name)
        buckets.setdefault(key, []).append(row)
    output = []
    for key, group in buckets.items():
        best = max(group, key=lambda r: r.dsi_icp_score)
        output.append({
            "company_name": best.company_name,
            "company_domain": best.company_domain,
            "headcount_bucket": best.company_headcount_bucket,
            "hq_country": best.company_hq_country,
            "open_matched_roles": len(group),
            "best_score": best.dsi_icp_score,
            "strong_global_roles_count": sum(1 for r in group if r.remote_classification == "STRONG_GLOBAL"),
            "fresh_roles_count": sum(1 for r in group if r.days_old <= 21),
            "best_role": best.job_title,
            "sources_found": ", ".join(sorted(set(r.source_name for r in group))[:10]),
            "highest_trust_url": best.final_canonical_url,
        })
    return pd.DataFrame(output).sort_values(["best_score", "open_matched_roles"], ascending=[False, False])


def source_report_dataframe(logs: Dict[str, SourceLog], rows: List[ClassifiedJob]) -> pd.DataFrame:
    for row in rows:
        log = logs.get(row.source_name)
        if not log:
            continue
        if row.bucket == "strict_icp":
            log.passed_strict += 1
        elif row.bucket == "secondary":
            log.passed_secondary += 1
        elif row.bucket == "needs_verification":
            log.needs_verification += 1
        elif row.bucket == "rejected":
            log.rejected += 1
    return pd.DataFrame([asdict(l) for l in logs.values()])


def save_outputs(rows: List[ClassifiedJob], logs: Dict[str, SourceLog]) -> Dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    strict_rows = sorted([r for r in rows if r.bucket == "strict_icp"], key=lambda r: r.dsi_icp_score, reverse=True)
    secondary_rows = sorted([r for r in rows if r.bucket == "secondary"], key=lambda r: r.dsi_icp_score, reverse=True)
    verify_rows = sorted([r for r in rows if r.bucket == "needs_verification"], key=lambda r: r.dsi_icp_score, reverse=True)
    rejected_rows = sorted([r for r in rows if r.bucket == "rejected"], key=lambda r: r.reject_reason)

    paths = {
        "strict": OUTPUT_DIR / f"dsi_v3_strict_icp_{DATE_STAMP}.csv",
        "secondary": OUTPUT_DIR / f"dsi_v3_secondary_{DATE_STAMP}.csv",
        "needs_verification": OUTPUT_DIR / f"dsi_v3_needs_verification_{DATE_STAMP}.csv",
        "rejected": OUTPUT_DIR / f"dsi_v3_rejected_{DATE_STAMP}.csv",
        "company_summary": OUTPUT_DIR / f"dsi_v3_company_summary_{DATE_STAMP}.csv",
        "source_report": OUTPUT_DIR / f"dsi_v3_source_report_{DATE_STAMP}.csv",
        "run_log": LOG_DIR / f"dsi_v3_run_log_{DATE_STAMP}.json",
    }

    rows_to_dataframe(strict_rows).to_csv(paths["strict"], index=False, encoding="utf-8-sig")
    rows_to_dataframe(secondary_rows).to_csv(paths["secondary"], index=False, encoding="utf-8-sig")
    rows_to_dataframe(verify_rows).to_csv(paths["needs_verification"], index=False, encoding="utf-8-sig")
    rejected_to_dataframe(rejected_rows).to_csv(paths["rejected"], index=False, encoding="utf-8-sig")
    company_summary_dataframe(rows).to_csv(paths["company_summary"], index=False, encoding="utf-8-sig")
    source_report_dataframe(logs, rows).to_csv(paths["source_report"], index=False, encoding="utf-8-sig")

    log_payload = {
        "run_time_utc": now_iso(),
        "counts": {
            "strict_icp": len(strict_rows),
            "secondary": len(secondary_rows),
            "needs_verification": len(verify_rows),
            "rejected": len(rejected_rows),
            "total_classified": len(rows),
        },
        "source_count": len(logs),
        "source_logs": [asdict(l) for l in logs.values()],
    }
    paths["run_log"].write_text(json.dumps(log_payload, indent=2), encoding="utf-8")
    return paths


def print_summary(rows: List[ClassifiedJob], logs: Dict[str, SourceLog], duplicate_count: int) -> None:
    strict = [r for r in rows if r.bucket == "strict_icp"]
    secondary = [r for r in rows if r.bucket == "secondary"]
    verify = [r for r in rows if r.bucket == "needs_verification"]
    rejected = [r for r in rows if r.bucket == "rejected"]
    raw_total = sum(l.raw_jobs_collected for l in logs.values())
    print("\n" + "=" * 72)
    print("DSI V3 RUN SUMMARY")
    print("=" * 72)
    print(f"Raw collected              : {raw_total}")
    print(f"Strict ICP                 : {len(strict)}")
    print(f"Secondary                  : {len(secondary)}")
    print(f"Needs verification          : {len(verify)}")
    print(f"Rejected                   : {len(rejected)}")
    print(f"Duplicates rejected         : {duplicate_count}")
    print(f"Sources attempted          : {len(logs)}")
    print(f"Sources with errors         : {sum(1 for l in logs.values() if l.error_count > 0)}")
    print("\nTop strict ICP companies:")
    summary = company_summary_dataframe(strict + secondary)
    if not summary.empty:
        print(summary.head(20).to_string(index=False))
    else:
        print("No strict or secondary companies found in this run.")
    if len(strict) < 100:
        print("\nReality check: strict ICP count is below 100. Do not fake volume.")
        print("Add more verified 10 to 200 headcount ATS companies in sources.yml.")
    if len(strict) + len(secondary) < 500:
        print("Strict plus secondary is below 500. This is honest output, not a code failure.")
        print("To scale, expand sources.yml with more small remote first SaaS companies.")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description="DSI Global Remote Engineering Hiring Signal Collector V3")
    parser.add_argument("--sources", default=str(DEFAULT_SOURCES), help="Path to sources.yml")
    parser.add_argument("--no-verify-pages", action="store_true", help="Skip fetching full job pages")
    parser.add_argument("--limit-sources", type=int, default=0, help="Only run first N sources for testing")
    args = parser.parse_args()

    source_path = Path(args.sources)
    if not source_path.exists():
        print(f"sources file not found: {source_path}", file=sys.stderr)
        return 2

    sources = load_sources(source_path)
    if args.limit_sources > 0:
        sources = sources[: args.limit_sources]
    print(f"Loaded {len(sources)} enabled sources from {source_path}")

    raw_jobs, logs = fetch_all_sources(sources)
    print(f"\nTotal raw jobs collected: {len(raw_jobs)}")

    classified = classify_all(raw_jobs, logs, verify_pages=not args.no_verify_pages)
    deduped_rows, duplicates = deduplicate_rows(classified)
    paths = save_outputs(deduped_rows, logs)
    print_summary(deduped_rows, logs, duplicate_count=len(duplicates))

    print("\nFiles created:")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
