import io
from types import SimpleNamespace

import pytest
from docx import Document

from scanner.config import load_profile
from scanner.db import Job, make_session
from scanner.render import letter_docx, letter_pdf, resume_docx, resume_pdf
from scanner.tailor import TailorOutput, fabrication_check, gap_story, resume_document, tailor

LETTER = ("Your analytics team caught my eye.\n\nAt Northwind I built a Tableau dashboard used by 120 managers. "
          "I'm based in Thailand (UTC+7).\n\nI'm also eager to learn dbt.\n\nAlex Rivera")


def faithful(**changes) -> TailorOutput:
    """A valid output built straight from the master profile."""
    p = load_profile()
    project = gap_story()["resume"]["projects"][0]
    base = dict(
        summary="Data analyst with 4+ years of experience in SQL and Tableau.",
        experience=[{"id": e.id, "bullets": [{"source_id": b.id, "text": b.text} for b in e.bullets[:3]]}
                    for e in p.experience],
        skills=["SQL", "Tableau", "Excel"],
        projects=[{"name": project["name"], "bullets": project["bullets"]}],
        cover_letter=LETTER,
        emphasis=["Led with Tableau work"],
    )
    return TailorOutput(**(base | changes))


def test_faithful_output_passes():
    assert fabrication_check(faithful()) == []


def test_catches_invented_number():
    out = faithful()
    out.experience[0].bullets[0].text = "Built a revenue dashboard used by 500 managers"
    assert any("Number '500'" in v for v in fabrication_check(out))


def test_catches_unknown_source_bullet():
    out = faithful()
    out.experience[0].bullets[0].source_id = "made-up"
    assert any("unknown source" in v for v in fabrication_check(out))


def test_catches_skill_not_in_profile():
    assert any("dbt" in v for v in fabrication_check(faithful(skills=["SQL", "dbt"])))


def test_catches_tool_claim_in_letter_but_allows_wanting_to_learn():
    claim = faithful(cover_letter="I have years of hands-on Airflow experience.\n\nAlex Rivera")
    assert any("airflow" in v for v in fabrication_check(claim))
    assert fabrication_check(faithful()) == []  # LETTER says "eager to learn dbt" -> allowed


def test_catches_unknown_project():
    assert any("Unknown project" in v for v in fabrication_check(faithful(projects=[{"name": "Moon Base", "bullets": []}])))


class FakeClient:
    """Stands in for anthropic.Anthropic: returns queued outputs from beta.messages.parse."""

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=6000, output_tokens=1500,
                                cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return SimpleNamespace(parsed_output=self.outputs.pop(0), usage=usage, stop_reason="end_turn",
                               model=kwargs["model"])


@pytest.fixture
def session_job():
    s = make_session("sqlite:///:memory:")
    job = Job(company="Acme", title="Data Analyst", canonical_key="acme|data analyst", description="SQL, Tableau")
    s.add(job)
    s.commit()
    return s, job


def test_retry_fixes_violations_and_versions_increment(session_job):
    s, job = session_job
    client = FakeClient(faithful(skills=["SQL", "dbt"]), faithful(), faithful())
    first = tailor(s, client, job)
    assert len(client.calls) == 2 and first.violations == [] and first.version == 1
    assert "dbt" in client.calls[1]["messages"][-1]["content"]  # retry was told what to fix
    second = tailor(s, client, job, instruction="emphasize Tableau")
    assert second.version == 2 and second.instruction == "emphasize Tableau"
    assert client.calls[0]["fallbacks"] == "default"


def test_unfixed_violations_are_stored_as_blocked(session_job):
    s, job = session_job
    bad = faithful(skills=["Kubernetes"])
    row = tailor(s, FakeClient(bad, bad), job)
    assert row.violations


def test_resume_facts_come_from_master_profile(session_job):
    s, job = session_job
    doc = resume_document(tailor(s, FakeClient(faithful()), job))
    assert doc["experience"][0]["title"].startswith("Career Break")
    assert [e["company"] for e in doc["experience"][1:]] == [e.company for e in load_profile().experience]


def test_render_docx_and_pdf(session_job):
    s, job = session_job
    doc = resume_document(tailor(s, FakeClient(faithful()), job))
    docx_bytes = resume_docx(doc)
    text = " ".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs)
    assert "Alex Rivera" in text and "Northwind Retail" in text
    assert resume_pdf(doc).startswith(b"%PDF")
    assert letter_docx(LETTER).startswith(b"PK") and letter_pdf(LETTER).startswith(b"%PDF")
