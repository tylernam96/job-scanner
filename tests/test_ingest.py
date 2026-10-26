from datetime import timedelta

import pytest
from sqlalchemy import func, select

from scanner.db import Job, Posting, make_session, utcnow
from scanner.dedupe import ingest
from scanner.normalize import RawJob, canonical_key, is_relevant_title

DESC = "We are hiring a data analyst to own SQL reporting and Tableau dashboards. " * 10


def raw(source="remotive", source_id="1", company="Acme, Inc.", title="Data Analyst", description=DESC):
    return RawJob(source=source, source_id=source_id, url=f"https://{source}/{source_id}",
                  company=company, title=title, description=description)


@pytest.fixture
def session():
    return make_session("sqlite:///:memory:")


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_canonical_key_ignores_noise():
    assert canonical_key("Acme, Inc.", "Data Analyst (Remote)") == canonical_key("ACME", "Data Analyst - Remote")
    assert canonical_key("Acme", "Data Analyst") != canonical_key("Acme", "Data Engineer")


def test_rerun_same_day_adds_nothing(session):
    batch = [raw(source_id="1"), raw(source_id="2", title="BI Analyst", description="other " * 60)]
    assert ingest(session, batch)["new"] == 2
    second = ingest(session, batch)
    assert second["new"] == 0 and second["seen"] == 2
    assert count(session, Job) == 2


def test_cross_post_merges_into_one_job(session):
    c = ingest(session, [
        raw(source="remotive", source_id="r1", title="Data Analyst (Remote)"),
        raw(source="remoteok", source_id="k9", company="ACME", title="Data Analyst - Remote"),
    ])
    assert (c["new"], c["merged"]) == (1, 1)
    job = session.scalar(select(Job))
    assert sorted(p.source for p in job.postings) == ["remoteok", "remotive"]


def test_regional_variants_keep_all_locations(session):
    a = raw(source="ashby", source_id="1", title="Engineer (US Remote)")
    a.location_raw = "United States"
    b = raw(source="ashby", source_id="2", title="Engineer (APJ-Remote)")
    b.location_raw = "Singapore"
    ingest(session, [a, b])
    job = session.scalar(select(Job))
    assert "Singapore" in job.location_raw and "APJ-Remote" in job.location_raw


def test_repost_with_new_title_merges_by_description(session):
    ingest(session, [raw(source_id="1", title="Data Analyst")])
    c = ingest(session, [raw(source_id="2", title="Data Analyst II")])
    assert c["merged"] == 1 and count(session, Job) == 1


def test_old_job_outside_window_is_not_merged(session):
    ingest(session, [raw(source_id="1")])
    session.scalar(select(Job)).first_seen_at = utcnow() - timedelta(days=61)
    session.commit()
    assert ingest(session, [raw(source_id="2")])["new"] == 1
    assert count(session, Posting) == 2


@pytest.mark.parametrize("title,ok", [
    ("Senior Data Analyst", True),
    ("Business Intelligence Analyst", True),
    ("Junior Software Engineer", True),
    ("Data Engineer", True),
    ("Account Executive", False),
    ("Product Designer", False),
    ("Fraud Analyst", True),
    ("Senior FP&A Analyst", True),
    ("Data Modeler", True),
    ("Analytics Engineering Intern", True),
    ("Software Engineer I", True),
    ("Junior DevOps Engineer", True),
    ("Senior Software Engineer", False),
    ("Cyber Security Analyst", False),
    ("QA Analyst", False),
    ("Video Data Annotator", False),
    ("Lead Data Analyst", False),
])
def test_title_prefilter(title, ok):
    assert is_relevant_title(title) is ok
