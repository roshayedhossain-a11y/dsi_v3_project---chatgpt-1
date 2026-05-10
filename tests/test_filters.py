import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dsi_scraper_v3 import classify_remote, role_family_for_title


def remote_class(location, text=""):
    return classify_remote(location, text)[0]


def test_rejected_locations():
    rejected = [
        "Remote, Germany",
        "Remote in Europe",
        "Remote US only",
        "United States",
        "Canada",
        "UK",
        "EMEA",
        "LATAM",
        "APAC",
        "Hybrid",
        "Onsite",
        "Must be based in Spain",
        "Must reside in Canada",
    ]
    for value in rejected:
        assert remote_class(value) == "REJECT", value


def test_rejected_restriction_text():
    rejected_texts = [
        "Work authorization required",
        "Visa sponsorship not available",
        "Must be authorized to work in the United States",
        "No visa sponsorship",
        "Office required",
    ]
    for text in rejected_texts:
        assert remote_class("Remote", text) == "REJECT", text


def test_strong_global_locations():
    accepted = [
        "Worldwide",
        "Remote Worldwide",
        "Anywhere",
        "Work from anywhere",
        "Anywhere in the world",
        "Global remote",
        "Open globally",
        "No location restriction",
        "Location independent",
        "Globally distributed",
    ]
    for value in accepted:
        assert remote_class(value) == "STRONG_GLOBAL", value


def test_weak_remote_not_strict():
    weak = ["Remote", "Fully remote", "Distributed", "Remote first", "Async"]
    for value in weak:
        assert remote_class(value) == "WEAK_REMOTE", value


def test_rejected_roles():
    roles = [
        "Customer Support Engineer",
        "Sales Engineer",
        "Engineering Manager",
        "Recruiter",
        "Intern Software Engineer",
    ]
    for title in roles:
        family, reason = role_family_for_title(title)
        assert family == "", title
        assert reason, title


def test_accepted_roles():
    roles = [
        "Backend Engineer",
        "Frontend Developer",
        "Full Stack Software Engineer",
        "DevOps Engineer",
        "QA Automation Engineer",
        "Machine Learning Engineer",
        "Mobile Engineer",
    ]
    for title in roles:
        family, reason = role_family_for_title(title)
        assert family != "", title
        assert reason == "", title
