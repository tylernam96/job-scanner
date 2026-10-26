"""Thailand-friendliness tiers (PRD §5.3): confirmed / likely / unknown / excluded, with evidence."""
import re
from functools import cache

from scanner.config import load_yaml


@cache
def _rules():
    loc = load_yaml("keywords.yaml")["location"]
    compile_all = lambda pats: [re.compile(p, re.I) for p in pats]
    rules = {f"{part}.{k}": compile_all(v) for part in ("field", "description", "timezone")
             for k, v in loc[part].items()}
    # "based in Europe", "anywhere in Poland", "Remote - USA": a region restriction stated in prose.
    regions = "(?:" + "|".join(p.removeprefix(r"\b").removesuffix(r"\b") for p in loc["field"]["restricted_regions"]) + ")"
    rules["description.regional"] = compile_all([
        rf"\b(based|located|living|residing|reside|live|anywhere|candidates?|hiring|hire|remote|analyst|engineer|"
        rf"developer|scientist|buscamos)(\s+\w+){{0,3}}?\s+(with)?(in|en)\s+(an?\s+|the\s+)?{regions}\b",
        rf"\bremote\s*[-–(:,]\s*{regions}\b",
        rf"\bremote\s+{regions}\b",
    ])
    return rules


def _first(patterns, text):
    for p in patterns:
        if m := p.search(text):
            return m
    return None


def snippet(text: str, m: re.Match, width: int = 80) -> str:
    start, end = max(0, m.start() - width), min(len(text), m.end() + width)
    return ("…" if start else "") + " ".join(text[start:end].split()) + ("…" if end < len(text) else "")


_REMOTE = re.compile(r"\b(remote|anywhere|worldwide|home[- ]based|distributed|work from home|wfh)\b", re.I)


def classify(location_raw: str, description: str, extra: dict | None = None,
             remote_implied: bool = True) -> tuple[str, str]:
    """Returns (tier, evidence). The location field is explicit, so it wins over the description.

    remote_implied: False for jobs seen only on company ATS boards, which also list office roles;
    those need an explicit remote signal in the location field.
    """
    r = _rules()
    extra = extra or {}
    field = location_raw or ""
    if not remote_implied and not extra.get("is_remote") and not _REMOTE.search(field):
        return "excluded", f"Not listed as remote: {field[:80]}"
    if m := _first(r["field.onsite"], field):
        return "excluded", f"On-site/hybrid: {snippet(field, m, 60)}"
    if m := _first(r["field.confirmed"], field):
        return "confirmed", f"Location: {snippet(field, m, 60)}"
    worldwide = _first(r["field.worldwide"], field)
    if not worldwide and (m := _first(r["field.restricted_regions"], field)):
        return "excluded", f"Location limited to: {snippet(field, m, 60)}"

    desc = description or ""
    if m := _first(r["description.confirmed"], desc):
        return "confirmed", snippet(desc, m)
    if m := _first(r["description.excluded"] + r["description.regional"], desc):
        return "excluded", snippet(desc, m)
    if extra.get("thailand_eligible"):
        return "confirmed", "Himalayas lists the job as open to candidates in Thailand"
    if worldwide:
        return "confirmed", f"Location: {snippet(field, worldwide, 60)}"
    if m := _first(r["description.likely"], desc):
        return "likely", snippet(desc, m)
    return "unknown", ""


def timezone_fit(description: str) -> str:
    """'good' (APAC/Europe hours), 'us_overlap', or 'unknown'."""
    r = _rules()
    if _first(r["timezone.us_overlap"], description or ""):
        return "us_overlap"
    if _first(r["timezone.good"], description or ""):
        return "good"
    return "unknown"
