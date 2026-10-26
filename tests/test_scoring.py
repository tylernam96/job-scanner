from types import SimpleNamespace

import pytest

from scanner.db import Job, Posting, Score, make_session
from scanner.scoring import LLMScore, classify_and_score

DESC = "We need SQL, Tableau and Excel skills for dashboards. Open to candidates anywhere in the world."


class FakeClient:
    """Stands in for anthropic.Anthropic; records calls."""

    def __init__(self, result: LLMScore | None, stop_reason="end_turn"):
        self.calls = 0
        self.messages = SimpleNamespace(parse=self._parse)
        self.result, self.stop_reason = result, stop_reason

    def _parse(self, **kwargs):
        self.calls += 1
        usage = SimpleNamespace(input_tokens=3000, output_tokens=300,
                                cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return SimpleNamespace(parsed_output=self.result, usage=usage, stop_reason=self.stop_reason)


def llm_result(**kw):
    base = dict(fit_score=72, role_family="A", skills_matched=["SQL"], skills_missing=["dbt"],
                seniority_fit="good", learning_opportunity=False, location_tier="confirmed",
                location_evidence="anywhere in the world", timezone_note="async",
                reasons=["SQL + Tableau"], red_flags=[])
    return LLMScore(**(base | kw))


@pytest.fixture
def session():
    s = make_session("sqlite:///:memory:")
    job = Job(company="Acme", title="Data Analyst", canonical_key="acme|data analyst",
              description=DESC, location_raw="Remote")
    s.add(job)
    s.add(Posting(job=job, source="remotive", source_id="1", url="u"))
    s.commit()
    return s


def test_rules_only_without_client(session):
    counts = classify_and_score(session, client=None)
    score = session.get(Score, 1)
    assert counts["scored_rules"] == 1 and score.model == "rules" and score.fit_score > 0


def test_llm_score_replaces_rule_score(session):
    client = FakeClient(llm_result())
    classify_and_score(session, client)
    score = session.get(Score, 1)
    assert client.calls == 1 and score.model == "claude-haiku-5-5" and score.fit_score == 72
    assert score.cost_usd > 0


def test_llm_resolves_unknown_location_to_excluded(session):
    session.get(Job, 1).description = "SQL and Tableau dashboards."  # no location info -> unknown
    session.commit()
    classify_and_score(session, FakeClient(llm_result(location_tier="excluded", location_evidence="US only")))
    job = session.get(Job, 1)
    assert job.location_tier == "excluded" and job.hidden_reason.startswith("location (LLM)")


def test_refusal_falls_back_to_rules(session):
    classify_and_score(session, FakeClient(None, stop_reason="refusal"))
    assert session.get(Score, 1).model == "rules"


def test_budget_cap_stops_llm(session, monkeypatch):
    monkeypatch.setattr("scanner.scoring.month_spend", lambda s: 999.0)
    client = FakeClient(llm_result())
    classify_and_score(session, client)
    assert client.calls == 0
