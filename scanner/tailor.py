"""On-demand resume + cover letter tailoring (PRD §5.8).

Rule: reorder and reword only. Every bullet must point at a master bullet, skills must come from
the master profile, and a deterministic fabrication check blocks invented numbers or tools.
"""
import json
import re
from datetime import datetime
from functools import cache

import yaml
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scanner.config import PROFILE_DIR, load_profile, load_yaml
from scanner.db import Job, Tailored

PRICES = {"claude-sonnet-5-5": (2.00, 10.00)}  # $ per million tokens (input, output)

# Tools/tech a tailored document might be tempted to borrow from a job description.
# Any of these appearing in output but not in the candidate's own material is a violation.
# Words that are also common English ("go", "mode", "segment", "swift") are left out on purpose.
TECH_TERMS = [
    "sql", "python", "r", "excel", "tableau", "power bi", "looker", "looker studio", "metabase",
    "qlik", "superset", "snowflake", "bigquery", "redshift", "databricks", "postgres", "postgresql", "mysql",
    "dbt", "airflow", "dagster", "prefect", "fivetran", "airbyte", "spark", "pyspark", "kafka", "hadoop",
    "aws", "gcp", "azure", "docker", "kubernetes", "terraform", "java", "javascript", "typescript",
    "golang", "rust", "c++", "c#", "scala", "ruby", "php", "kotlin", "react", "node.js", "django",
    "flask", "fastapi", "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch", "stata", "sas", "spss",
    "google analytics", "ga4", "amplitude", "mixpanel", "salesforce", "hubspot", "jira", "git",
    "github", "streamlit", "langchain", "llm", "machine learning", "statistics", "a/b testing", "etl", "elt",
]
LEARNING = re.compile(r"\b(learn|learning|pick up|ramp up|grow into|excited to explore)\b", re.I)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


class TBullet(BaseModel):
    source_id: str  # id of the master bullet this rewords
    text: str


class TExperience(BaseModel):
    id: str  # master experience id
    bullets: list[TBullet]


class TProject(BaseModel):
    name: str  # must match a project in gap_story.yaml
    bullets: list[str]


class TailorOutput(BaseModel):
    summary: str
    experience: list[TExperience]
    skills: list[str]
    projects: list[TProject]
    cover_letter: str
    emphasis: list[str]  # short notes for the candidate: what was emphasized and why


@cache
def gap_story() -> dict:
    return yaml.safe_load((PROFILE_DIR / "gap_story.yaml").read_text())


def _corpus() -> str:
    """Everything the candidate has said about themselves, lowercased."""
    return (json.dumps(load_profile().model_dump()) + "\n" + (PROFILE_DIR / "gap_story.yaml").read_text()).lower()


def _has_term(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9+#])", text.lower()) is not None


def _numbers(text: str) -> set[str]:
    return {n.rstrip(".,").replace(",", "") for n in NUMBER.findall(text)}


def fabrication_check(out: TailorOutput) -> list[str]:
    """Deterministic 'no invented facts' check. Returns violations (empty = OK)."""
    profile, gap = load_profile(), gap_story()
    corpus = _corpus()
    corpus_numbers = _numbers(corpus)
    violations = []

    bullets = {e.id: {b.id: b.text for b in e.bullets} for e in profile.experience}
    for exp in out.experience:
        if exp.id not in bullets:
            violations.append(f"Unknown experience '{exp.id}'")
            continue
        for b in exp.bullets:
            source = bullets[exp.id].get(b.source_id)
            if source is None:
                violations.append(f"Bullet cites unknown source '{b.source_id}'")
                continue
            for n in _numbers(b.text) - _numbers(source):
                violations.append(f"Number '{n}' not in source bullet {b.source_id}: {b.text!r}")

    master_skills = {s.lower() for s in profile.skills.tools + profile.skills.methods + profile.skills.practices}
    violations += [f"Skill not in master profile: {s}" for s in out.skills if s.lower() not in master_skills]

    projects = {p["name"]: p for p in gap["resume"]["projects"]}
    for p in out.projects:
        if p.name not in projects:
            violations.append(f"Unknown project '{p.name}'")
            continue
        source_numbers = _numbers(" ".join(projects[p.name]["bullets"]))
        for text in p.bullets:
            for n in _numbers(text) - source_numbers:
                violations.append(f"Number '{n}' not in project '{p.name}' source: {text!r}")

    for n in _numbers(out.summary + " " + out.cover_letter) - corpus_numbers:
        violations.append(f"Number '{n}' in summary/cover letter not found in profile")

    resume_text = " ".join([out.summary] + [b.text for e in out.experience for b in e.bullets]
                           + [t for p in out.projects for t in p.bullets])
    letter_claims = " ".join(s for s in re.split(r"(?<=[.!?])\s+", out.cover_letter) if not LEARNING.search(s))
    for term in TECH_TERMS:
        if not _has_term(corpus, term) and (_has_term(resume_text, term) or _has_term(letter_claims, term)):
            violations.append(f"Tool/skill '{term}' is not in the candidate's profile")
    return violations


SYSTEM = """You tailor a resume and write a cover letter for one candidate and one job.

HARD RULES (a program checks these; violations are rejected):
- Reorder and reword only. Every experience bullet must reword exactly one master bullet and cite its id in source_id.
  Keep the meaning; you may use the job posting's vocabulary only where it is truthful for that bullet.
- Never add skills, tools, employers, titles, dates, metrics, numbers, or certifications that are not in the candidate material.
  Every number you write must appear in the cited source bullet (or, in the summary/letter, in the candidate material).
- skills: choose and order from the master profile's skills only, most relevant first.
- Include every experience id from the master profile, in the same order; pick the 3-6 most relevant bullets for each (2-3 for the internship).
- projects: pick the 1-3 most relevant projects from the career-break section, by exact name; you may reword their bullets.
- Do not mention the job's required tools unless the candidate has them. In the cover letter you may say you are eager to learn a tool, nothing more.

SUMMARY: 2-3 sentences aimed at this role.

COVER LETTER: 250-350 words, 3-4 paragraphs, plain text with blank lines between paragraphs, no placeholders, signed with the candidate's name.
- Open with something specific about this company or role (not "I am writing to express my interest").
- Mid-letter, explain the career break using ONLY the approved framing in the candidate material (cover_letter.default or .short); never apologize for it.
- State plainly where the candidate is based (contact.location) and their working setup from work_setup
  (working hours; contractor / employer-of-record hiring).
- Professional and direct; no clichés.

emphasis: 2-4 short notes telling the candidate what you emphasized and why.

CANDIDATE MASTER PROFILE (JSON):
{profile}

CAREER BREAK, PROJECTS AND APPROVED GAP WORDING (YAML):
{gap}"""


@cache
def _system_prompt() -> str:
    profile = load_profile().model_dump(exclude={"status"})
    return SYSTEM.format(profile=json.dumps(profile, indent=1),
                         gap=(PROFILE_DIR / "gap_story.yaml").read_text())


def _posting(job: Job) -> str:
    return (f"JOB POSTING\nTitle: {job.title}\nCompany: {job.company}\nLocation: {job.location_raw}\n"
            f"Salary: {job.salary_raw or 'not listed'}\n\n{job.description[:15000]}")


def _call(client, model: str, messages: list[dict]) -> tuple[TailorOutput | None, float, str]:
    response = client.beta.messages.parse(
        model=model,
        max_tokens=8000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # on a policy decline, the API retries on a fallback model
        system=[{"type": "text", "text": _system_prompt(), "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": "medium"},
        messages=messages,
        output_format=TailorOutput,
    )
    u = response.usage
    pin, pout = PRICES.get(model, (0, 0))
    cost = ((u.input_tokens + (u.cache_creation_input_tokens or 0) * 1.25 + (u.cache_read_input_tokens or 0) * 0.1)
            * pin + u.output_tokens * pout) / 1e6
    if response.stop_reason == "refusal":
        return None, cost, response.model
    return response.parsed_output, cost, response.model


def make_client():
    import anthropic
    return anthropic.Anthropic()


def latest(session: Session, job_id: int) -> Tailored | None:
    return session.scalars(select(Tailored).where(Tailored.job_id == job_id)
                           .order_by(Tailored.version.desc()).limit(1)).first()


def tailor(session: Session, client, job: Job, instruction: str = "") -> Tailored:
    """Generate, check (one automatic retry on violations), and store a new version."""
    model = load_yaml("scoring.yaml")["models"]["tailoring"]
    user = _posting(job) + (f"\n\nEXTRA INSTRUCTION FROM THE CANDIDATE: {instruction}" if instruction else "")
    messages = [{"role": "user", "content": user}]
    out, cost, served_by = _call(client, model, messages)
    if out is None:
        raise RuntimeError("The model declined to tailor this posting.")

    violations = fabrication_check(out)
    if violations:
        messages += [{"role": "assistant", "content": out.model_dump_json()},
                     {"role": "user", "content": "Your output broke the hard rules:\n- " + "\n- ".join(violations)
                      + "\nReturn a corrected version that fixes every item and changes nothing else."}]
        retry, retry_cost, served_by = _call(client, model, messages)
        cost += retry_cost
        if retry is not None:
            out, violations = retry, fabrication_check(retry)

    version = (session.scalar(select(func.max(Tailored.version)).where(Tailored.job_id == job.id)) or 0) + 1
    row = Tailored(job_id=job.id, version=version, resume=out.model_dump(), cover_letter=out.cover_letter,
                   violations=violations, instruction=instruction, model=served_by, cost_usd=round(cost, 4))
    session.add(row)
    session.commit()
    return row


def _month(ym: str) -> str:
    """'2022-08' -> 'Aug 2022'."""
    return datetime.strptime(ym, "%Y-%m").strftime("%b %Y")


def _url(url) -> str:
    return url if isinstance(url, str) and url.startswith("http") else ""


def resume_document(t: Tailored) -> dict:
    """Assemble the final resume: facts (names, titles, dates) always come from the master profile."""
    profile, gap = load_profile(), gap_story()
    out = TailorOutput(**t.resume)
    exp_by_id = {e.id: e for e in profile.experience}
    source = {b.id: b.text for e in profile.experience for b in e.bullets}
    brk = gap["resume"]["entry"]
    projects = {p["name"]: p for p in gap["resume"]["projects"]}
    return {
        "name": profile.name,
        "contact": [v for k, v in profile.contact.items() if v],
        "summary": out.summary,
        "experience": [{"company": brk["location"], "title": brk["title"], "dates": brk["dates"],
                        "bullets": [{"text": b, "source": b} for b in brk["bullets"]]}] + [
            {"company": exp_by_id[e.id].company, "title": exp_by_id[e.id].title,
             "location": exp_by_id[e.id].location,
             "dates": f"{_month(exp_by_id[e.id].start)} – {_month(exp_by_id[e.id].end) if exp_by_id[e.id].end else 'Present'}",
             "bullets": [{"text": b.text, "source": source.get(b.source_id, "")} for b in e.bullets]}
            for e in out.experience if e.id in exp_by_id],
        "projects": [{"name": p.name, "url": _url(projects.get(p.name, {}).get("url")), "bullets": p.bullets}
                     for p in out.projects],
        "education": [f"{e.school} — {e.degree} ({e.start}–{e.end})" for e in profile.education],
        "skills": out.skills,
        "interests": gap["resume"].get("interests", ""),
    }
