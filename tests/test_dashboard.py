from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

from scanner.db import Action, Job, Posting, Score, ShownJob, make_session

APP = str(Path(__file__).parent.parent / "dashboard/app.py")


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    s = make_session(url)
    for i in (1, 2):
        job = Job(company=f"Co{i}", title=f"Data Analyst {i}", canonical_key=f"co{i}|da", description="SQL",
                  role_family="A", location_tier="confirmed", location_evidence="Worldwide")
        s.add_all([job, Posting(job=job, source="himalayas", source_id=str(i), url=f"https://x/{i}"),
                   Score(job=job, fit_score=90 - i, model="rules", reasons=["SQL match"]),
                   ShownJob(job=job, canonical_key=job.canonical_key, shown_date=date(2026, 10, 26), rank=i)])
    s.commit()
    return url


def app():
    return AppTest.from_file(APP, default_timeout=30)


def actions(url):
    return [(a.job_id, a.action, a.reason) for a in make_session(url).scalars(select(Action).order_by(Action.id))]


def test_today_renders_jobs(db_url):
    at = app().run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Data Analyst 1" in text and "Data Analyst 2" in text


def test_save_and_skip_persist(db_url):
    at = app().run()
    at.button(key="save1").click().run()
    at.selectbox(key="reason2").select("too senior").run()
    at.button(key="skip2").click().run()
    assert not at.exception
    assert actions(db_url) == [(1, "saved", None), (2, "skipped", "too senior")]


def test_empty_database_shows_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    at = app().run()
    assert not at.exception
    assert any("No daily lists yet" in i.value for i in at.info)


def test_tailor_button_generates_and_shows_result(db_url, monkeypatch):
    from scanner.db import Tailored
    from tests.test_tailor import FakeClient, faithful
    monkeypatch.setattr("scanner.tailor.make_client", lambda: FakeClient(faithful()))
    at = app().run()
    at.button(key="tailor1").click().run()
    assert not at.exception
    rows = make_session(db_url).scalars(select(Tailored)).all()
    assert [(r.job_id, r.version, r.violations) for r in rows] == [(1, 1, [])]
    assert any("Data analyst with 4+ years" in m.value for m in at.markdown)
    assert at.text_area(key=f"letter{rows[0].id}").value.startswith("Your analytics team")


def test_tailor_without_credentials_shows_error(db_url, monkeypatch):
    def no_key():
        raise RuntimeError("Could not resolve authentication method")
    monkeypatch.setattr("scanner.tailor.make_client", no_key)
    at = app().run()
    at.button(key="tailor1").click().run()
    assert not at.exception
    assert any("ANTHROPIC_API_KEY" in e.value for e in at.error)
