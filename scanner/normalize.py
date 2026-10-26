"""Normalization: plain text, canonical keys, content hashes, relevance prefilter."""
import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache

from scanner.config import load_yaml


@dataclass
class RawJob:
    source: str
    source_id: str
    url: str
    company: str
    title: str
    description: str = ""
    location_raw: str = ""
    salary_raw: str = ""
    posted_at: datetime | None = None  # naive UTC
    extra: dict = field(default_factory=dict)


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(html.unescape(s))  # Greenhouse double-escapes
    s = re.sub(r"<(br|/p|/li|/h\d|/div)\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


_COMPANY_SUFFIX = re.compile(r"\b(inc|llc|ltd|limited|gmbh|corp|corporation|co|plc|sa|bv|pty)\b\.?", re.I)
_TITLE_NOISE = re.compile(r"\b(remote|hybrid|full[- ]?time|part[- ]?time|m/f/d|f/m/d|w/m/d|all genders)\b", re.I)


def _squash(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", s.lower()).split())


def norm_company(company: str) -> str:
    return _squash(_COMPANY_SUFFIX.sub(" ", company))


def norm_title(title: str) -> str:
    title = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", title)
    return _squash(_TITLE_NOISE.sub(" ", title))


def canonical_key(company: str, title: str) -> str:
    return f"{norm_company(company)}|{norm_title(title)}"


def content_hash(description: str) -> str | None:
    """Hash of the description's words; identical text across boards/reposts -> same hash."""
    words = re.findall(r"[a-z0-9]+", description.lower())
    if len(words) < 50:  # too short to identify a job
        return None
    return hashlib.sha1(" ".join(words[:2000]).encode()).hexdigest()


FAMILIES = ("A", "B", "C", "D")


@cache
def _families():
    kw = load_yaml("keywords.yaml")
    c = lambda pats: [re.compile(p, re.I) for p in pats]
    return {f: {k: c(v) for k, v in kw["role_families"][f].items()} for f in FAMILIES}, c(kw["title_exclude"])


def role_family(title: str) -> str | None:
    """First family whose title patterns match and whose seniority rules allow it; None if excluded."""
    fams, exclude = _families()
    if any(p.search(title) for p in exclude):
        return None
    for f in FAMILIES:
        rules = fams[f]
        if not any(p.search(title) for p in rules["include"]):
            continue
        if rules.get("require_any") and not any(p.search(title) for p in rules["require_any"]):
            continue
        if any(p.search(title) for p in rules["exclude_seniority"]):
            continue
        return f
    return None


def is_relevant_title(title: str) -> bool:
    """Ingest-time filter: only titles in a target role family (incl. seniority) are stored."""
    return role_family(title) is not None
