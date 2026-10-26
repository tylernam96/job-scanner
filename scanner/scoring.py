"""Stage 1 (rules: role family, seniority, location) and Stage 2 (fit score) — PRD §5.4.

Rule scoring always runs and is the fallback. LLM scoring (Haiku) runs when Anthropic
credentials are available and the monthly budget allows; it replaces the rule score.
"""
import json
import re
from datetime import datetime
from functools import cache
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scanner.config import PROFILE_DIR, load_companies, load_profile, load_yaml
from scanner.db import Job, Score, utcnow
from scanner.location import classify, timezone_fit
from scanner.normalize import role_family

ATS_SOURCES = {"greenhouse", "lever", "ashby"}


@cache
def _boosts():
    kw = load_yaml("keywords.yaml")
    return {k: [re.compile(p, re.I) for p in v] for k, v in kw["boosts"].items()}


@cache
def _skill_patterns() -> dict[str, re.Pattern]:
    p = load_profile()
    skills = p.skills.tools + p.skills.methods
    return {s: re.compile(rf"(?<![a-z]){re.escape(s)}(?![a-z])", re.I) for s in skills}


_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d+\s*)?years?", re.I)


def rule_score(job: Job) -> tuple[float, dict, list[str]]:
    sc = load_yaml("scoring.yaml")
    comp, _, boosts = sc["components"], None, sc["boosts"]
    text = f"{job.title}\n{job.description}"
    reasons = []

    matched = [s for s, p in _skill_patterns().items() if p.search(text)]
    skills = comp["skills_match"] * min(1.0, len(matched) / 5)
    if matched:
        reasons.append(f"Skills match: {', '.join(matched[:6])}")

    years = [int(y) for y in _YEARS.findall(job.description or "") if 0 < int(y) < 20]
    senior_title = re.search(r"\b(senior|sr\.?)\b", job.title, re.I)
    if job.role_family == "A":
        seniority = comp["seniority_fit"] * (0.75 if senior_title else 1.0)
    else:  # stretch families are already restricted to junior/intern titles
        seniority = comp["seniority_fit"]
    if years and min(years) > 6:
        seniority = comp["seniority_fit"] * 0.25
        reasons.append(f"Asks for {min(years)}+ years")

    location = sc["location_tier_points"].get(job.location_tier, 0)

    tz = timezone_fit(job.description)
    timezone = {"good": 1.0, "unknown": 0.5, "us_overlap": 0.0}[tz] * comp["timezone"]
    if tz == "us_overlap":
        reasons.append("Mentions US working hours")

    boost_rx = _boosts()
    learning = any(p.search(text) for p in boost_rx["learning_opportunity"])
    family_pts = comp["learning_or_family"] * (1.0 if job.role_family == "A" or learning else 0.3)
    bonus = boosts["learning_opportunity"] * (learning and job.role_family != "A")
    if any(p.search(text) for p in boost_rx["returnship"]):
        bonus += boosts["returnship"]
        reasons.append("Welcomes career returners")

    raw = skills + seniority + location + timezone + family_pts + bonus
    final = round(min(100.0, raw) * sc["role_family_weight"][job.role_family], 1)
    breakdown = {"skills": round(skills, 1), "seniority": round(seniority, 1), "location": location,
                 "timezone": round(timezone, 1), "family": round(family_pts, 1), "bonus": bonus}
    return final, breakdown, reasons


# ---------------------------------------------------------------- LLM (Stage 2)

class LLMScore(BaseModel):
    fit_score: int
    role_family: Literal["A", "B", "C", "D"]
    skills_matched: list[str]
    skills_missing: list[str]
    seniority_fit: Literal["good", "stretch", "too_senior", "too_junior"]
    learning_opportunity: bool
    location_tier: Literal["confirmed", "likely", "unknown", "excluded"]
    location_evidence: str
    timezone_note: str
    reasons: list[str]
    red_flags: list[str]


# $ per million tokens (input, output) — keep in sync with the model in scoring.yaml
PRICES = {"claude-haiku-5-5": (0.10, 0.50)}

SYSTEM = """You score job postings for one candidate. Be strict and factual; quote the posting for evidence.

Candidate facts:
{profile}

Career break (approved wording facts):
{gap}

Role families: A = data/BI/business analyst or analytics engineer (primary target).
B = junior data engineering. C = junior/intern software engineering. D = junior/intern data science.

Location tiers — the candidate lives in Thailand (UTC+7); see contact.location and work_setup for citizenship,
accepted hire types and working hours:
- confirmed: posting explicitly allows Thailand, APAC/Asia, worldwide/anywhere, or a timezone range containing UTC+7.
- likely: no explicit statement, but structurally open (contractor/B2B, EOR such as Deel, Europe-hours requirement without residency limits).
- unknown: remote with no location information.
- excluded: residency limited to regions excluding Thailand, on-site/hybrid, or required working hours outside work_setup.
A work-authorization requirement for the candidate's own citizenship country is NOT an exclusion on its own.

fit_score (0-100): skills match 40, seniority/experience fit 20, location 20, timezone fit with work_setup 10, learning opportunity / primary-family fit 10.
Judge years of experience from the experience dates; roles needing far more experience or lead/manager scope are a poor fit."""


@cache
def _system_prompt() -> str:
    profile = load_profile().model_dump(exclude={"status"})
    gap = (PROFILE_DIR / "gap_story.yaml").read_text()
    return SYSTEM.format(profile=json.dumps(profile, indent=1), gap=gap)


def month_spend(session: Session) -> float:
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return session.scalar(select(func.coalesce(func.sum(Score.cost_usd), 0.0)).where(Score.scored_at >= start))


def llm_score(client, model: str, job: Job) -> tuple[LLMScore | None, float]:
    """Returns (score or None on refusal/failure, cost in USD)."""
    posting = (f"Title: {job.title}\nCompany: {job.company}\nLocation field: {job.location_raw}\n"
               f"Salary: {job.salary_raw or 'not listed'}\n\n{job.description[:12000]}")
    response = client.messages.parse(
        model=model,
        max_tokens=2000,
        system=[{"type": "text", "text": _system_prompt(), "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": posting}],
        output_format=LLMScore,
    )
    u = response.usage
    pin, pout = PRICES.get(model, (0, 0))
    cost = ((u.input_tokens + (u.cache_creation_input_tokens or 0) * 1.25 + (u.cache_read_input_tokens or 0) * 0.1)
            * pin + u.output_tokens * pout) / 1e6
    if response.stop_reason == "refusal":
        return None, cost
    return response.parsed_output, cost


def make_llm_client():
    """Anthropic client if credentials resolve, else None (rules-only mode)."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        client.models.retrieve(load_yaml("scoring.yaml")["models"]["scoring"])  # cheap auth check
        return client
    except Exception:
        return None


# ---------------------------------------------------------------- orchestration

def classify_and_score(session: Session, client=None, rescore: bool = False) -> dict:
    """Classify every unclassified job; score candidates. Returns counts."""
    sc = load_yaml("scoring.yaml")
    model = sc["models"]["scoring"]
    blocked = {c.name.lower() for c in load_companies() if c.blocked}
    counts = {"classified": 0, "hidden": 0, "scored_rules": 0, "scored_llm": 0, "llm_failed": 0}

    query = select(Job) if rescore else select(Job).where(Job.location_tier.is_(None))
    for job in session.scalars(query):
        job.role_family = role_family(job.title)
        remote_implied = any(p.source not in ATS_SOURCES for p in job.postings)
        job.location_tier, job.location_evidence = classify(job.location_raw, job.description, job.extra, remote_implied)
        job.hidden_reason = (
            "company blocked" if job.company.lower() in blocked
            else "title/seniority not a target" if job.role_family is None
            else f"location: {job.location_evidence}" if job.location_tier == "excluded"
            else None
        )
        counts["classified"] += 1
        if job.hidden_reason:
            counts["hidden"] += 1
            continue

        fit, breakdown, reasons = rule_score(job)
        score = job.score or Score(job=job)
        score.fit_score, score.breakdown, score.reasons, score.red_flags = fit, breakdown, reasons, []
        score.model, score.cost_usd, score.scored_at = "rules", 0.0, utcnow()
        session.add(score)
        counts["scored_rules"] += 1

        if client and month_spend(session) < sc["budget"]["monthly_usd_cap"]:
            try:
                result, cost = llm_score(client, model, job)
            except Exception:
                result, cost = None, 0.0
            score.cost_usd = cost
            if result is None:
                counts["llm_failed"] += 1
                continue
            if job.location_tier == "unknown":  # LLM resolves only the ambiguous ones
                job.location_tier, job.location_evidence = result.location_tier, result.location_evidence
                if result.location_tier == "excluded":
                    job.hidden_reason = f"location (LLM): {result.location_evidence}"
            job.role_family = result.role_family
            score.fit_score = round(result.fit_score * sc["role_family_weight"][result.role_family], 1)
            score.breakdown = result.model_dump(include={"skills_matched", "skills_missing", "seniority_fit",
                                                         "learning_opportunity", "timezone_note"})
            score.reasons, score.red_flags, score.model = result.reasons, result.red_flags, model
            counts["scored_llm"] += 1
        session.flush()

    session.commit()
    return counts
