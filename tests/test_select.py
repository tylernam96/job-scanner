from datetime import date, timedelta

import pytest
from sqlalchemy import select

from scanner.db import Job, Score, ShownJob, make_session, utcnow
from scanner.scoring import role_family
from scanner.select import select_daily


@pytest.fixture
def session():
    return make_session("sqlite:///:memory:")


def add_job(session, i, family="A", score=80.0, tier="confirmed", key=None):
    job = Job(company=f"Co{i}", title=f"Job {i}", canonical_key=key or f"co{i}|job {i}",
              role_family=family, location_tier=tier, last_seen_at=utcnow(), posted_at=utcnow())
    session.add(job)
    session.add(Score(job=job, fit_score=score, model="rules"))
    return job


@pytest.mark.parametrize("title,family", [
    ("Data Analyst", "A"),
    ("Senior Data Analyst", "A"),
    ("Lead Data Analyst", None),
    ("Data Engineer", "B"),
    ("Senior Data Engineer", None),
    ("Junior Software Engineer", "C"),
    ("Software Engineer", None),
    ("Data Science Intern", "D"),
])
def test_role_family(title, family):
    assert role_family(title) == family


def test_seven_days_never_repeat(session):
    for i in range(100):
        add_job(session, i, family="A" if i % 3 else "B", score=60 + i % 40)
    session.commit()
    seen_ids, seen_keys = set(), set()
    start = date(2026, 11, 1)
    for d in range(7):
        rows = select_daily(session, start + timedelta(days=d))
        assert len(rows) == 10
        ids = {r.job_id for r in rows}
        keys = {r.canonical_key for r in rows}
        assert not ids & seen_ids and not keys & seen_keys
        seen_ids |= ids
        seen_keys |= keys


def test_same_day_is_idempotent(session):
    for i in range(20):
        add_job(session, i)
    session.commit()
    first = [r.job_id for r in select_daily(session, date(2026, 11, 1))]
    again = [r.job_id for r in select_daily(session, date(2026, 11, 1))]
    assert first == again


def test_mix_caps_stretch_roles(session):
    for i in range(10):
        add_job(session, i, family="A", score=70)
        add_job(session, 100 + i, family="C", score=90)
    session.commit()
    families = [r.job.role_family for r in select_daily(session, date(2026, 11, 1))]
    assert families.count("C") == 3 and families.count("A") == 7


def test_fewer_than_ten_rather_than_padding(session):
    add_job(session, 1, score=80)
    add_job(session, 2, score=30)  # below min_score
    add_job(session, 3, tier="excluded")
    session.commit()
    assert [r.job_id for r in select_daily(session, date(2026, 11, 1))] == [1]


def test_reposted_job_with_same_key_not_reshown(session):
    add_job(session, 1, key="acme|data analyst")
    session.commit()
    select_daily(session, date(2026, 11, 1))
    add_job(session, 2, key="acme|data analyst")
    session.commit()
    assert select_daily(session, date(2026, 11, 2)) == []
