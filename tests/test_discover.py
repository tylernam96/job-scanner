import io
import tarfile
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from scanner import discover
from scanner.db import AtsBoard, make_session
from scanner.normalize import RawJob
from scanner.sources import ats


@pytest.fixture
def session():
    return make_session("sqlite:///:memory:")


def test_links_in_finds_all_three_ats():
    text = ("Apply: https://boards.greenhouse.io/acme/jobs/123 or https://job-boards.eu.greenhouse.io/beta "
            "https://jobs.lever.co/gamma-co/abc and https://jobs.ashbyhq.com/delta.io/xyz "
            "https://boards.greenhouse.io/embed/job_board?for=epsilon")
    assert discover.links_in(text) == {("greenhouse", "acme"), ("greenhouse", "beta"), ("lever", "gamma-co"),
                                       ("ashby", "delta.io"), ("greenhouse", "epsilon")}


def test_from_postings_adds_new_boards_once(session):
    raws = [RawJob(source="hackernews", source_id=str(i), url="u", company="Newco",
                   title="t", description="jobs at https://jobs.ashbyhq.com/newco and https://jobs.ashbyhq.com/posthog")
            for i in range(2)]
    assert discover.from_postings(session, raws) == 1  # posthog is already curated; second raw is a duplicate
    board = session.scalars(select(AtsBoard)).one()
    assert (board.ats, board.slug, board.name, board.origin) == ("ashby", "newco", "Newco", "link:hackernews")


def _tarball(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"remote-jobs-main/src/companies/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_remoteintech_keeps_worldwide_and_apac_only():
    tar = _tarball({
        "a.md": "---\ntitle: Alpha\nslug: alpha\nregion: worldwide\ncareers_url: https://jobs.lever.co/alpha-hq\n---\nbody",
        "b.md": "---\ntitle: Beta Corp\nslug: beta\nregion: asia-pacific\n---\nbody",
        "c.md": "---\ntitle: Gamma\nslug: gamma\nregion: europe\n---\nbody",
    })
    got = dict(discover.remoteintech_candidates(tar))
    assert set(got) == {"Alpha", "Beta Corp"}
    assert got["Alpha"][0] == ("lever", "alpha-hq")  # explicit careers link is tried first
    assert ("greenhouse", "betacorp") in got["Beta Corp"]


def test_ats_fetch_polls_discovered_and_reports_dead_boards(monkeypatch):
    calls = []

    def fake(client, name, slug):
        calls.append(slug)
        if slug == "dead":
            raise httpx.HTTPStatusError("404", request=None, response=SimpleNamespace(status_code=404))
        return [RawJob(source="greenhouse", source_id=f"{slug}:1", url="u", company=name, title="Data Analyst")]

    monkeypatch.setattr(ats, "FETCHERS", {"greenhouse": fake, "lever": fake, "ashby": fake})
    discovered = [SimpleNamespace(ats="greenhouse", slug="newco", name="Newco"),
                  SimpleNamespace(ats="greenhouse", slug="dead", name="Dead"),
                  SimpleNamespace(ats="greenhouse", slug="gitlab", name="GitLab")]  # already curated
    errors = {}
    jobs, gone = ats.fetch(None, errors, discovered)
    assert gone == {("greenhouse", "dead")} and not errors
    assert calls.count("gitlab") == 1 and "newco" in calls


def test_get_retries_dropped_connections(monkeypatch):
    from scanner.sources import common
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ReadError("Connection reset by peer")
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert common.get(client, "https://example.test").json() == {"ok": True}
    assert len(attempts) == 3
