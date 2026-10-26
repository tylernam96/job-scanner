"""Find more Greenhouse / Lever / Ashby company boards automatically.

- Daily: any ATS link inside postings from other sources (e.g. the HN "Who is hiring" thread).
- Weekly: companies in the remoteintech.company directory whose hiring region is worldwide or Asia-Pacific.
"""
import io
import re
import tarfile
from concurrent.futures import ThreadPoolExecutor

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.config import load_companies
from scanner.db import AtsBoard
from scanner.normalize import RawJob
from scanner.sources.common import get

ATS_LINK = re.compile(
    r"(?:boards(?:-api)?\.(?:eu\.)?greenhouse\.io/(?:v1/boards/|embed/job_board\?for=)?|job-boards\.(?:eu\.)?greenhouse\.io/)"
    r"(?P<greenhouse>[a-z0-9_-]+)"
    r"|jobs\.(?:eu\.)?lever\.co/(?P<lever>[a-z0-9_.-]+)"
    r"|jobs\.ashbyhq\.com/(?P<ashby>[a-z0-9_.%-]+)", re.I)
NOT_SLUGS = {"embed", "v1", "jobs", "api"}
BOARD_API = {"greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs",
             "lever": "https://api.lever.co/v0/postings/{}?mode=json",
             "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}"}
REMOTEINTECH = "https://codeload.github.com/remoteintech/remote-jobs/tar.gz/refs/heads/main"
REMOTEINTECH_REGIONS = re.compile(r"worldwide|asia|apac|global", re.I)


def links_in(text: str) -> set[tuple[str, str]]:
    found = set()
    for m in ATS_LINK.finditer(text or ""):
        ats, slug = next((k, v) for k, v in m.groupdict().items() if v)
        slug = slug.lower().rstrip(".")
        if slug not in NOT_SLUGS:
            found.add((ats, slug))
    return found


def _known(session: Session) -> set[tuple[str, str]]:
    curated = {(c.ats, c.slug) for c in load_companies()}
    return curated | {(a, s) for a, s in session.execute(select(AtsBoard.ats, AtsBoard.slug))}


def from_postings(session: Session, raws: list[RawJob]) -> int:
    """Record ATS boards linked from other sources' postings. Validity is checked when polled."""
    known, added = _known(session), 0
    for raw in raws:
        for ats, slug in links_in(f"{raw.url}\n{raw.description}") - known:
            session.add(AtsBoard(ats=ats, slug=slug, name=raw.company or slug, origin=f"link:{raw.source}"))
            known.add((ats, slug))
            added += 1
    session.commit()
    return added


def remoteintech_candidates(tar_bytes: bytes) -> list[tuple[str, list[tuple[str, str]]]]:
    """(company name, [(ats, slug) guesses]) for worldwide/APAC companies in the directory archive."""
    out = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        for member in tar:
            if not (member.isfile() and "/src/companies/" in member.name and member.name.endswith(".md")):
                continue
            text = tar.extractfile(member).read().decode("utf-8", "ignore")  # read in memory, never written
            try:
                fm = yaml.safe_load(text.split("---")[1]) if text.startswith("---") else None
            except yaml.YAMLError:
                continue
            if not isinstance(fm, dict) or not REMOTEINTECH_REGIONS.search(str(fm.get("region", ""))):
                continue
            name = str(fm.get("title", "")).strip(" >")
            guesses = sorted(links_in(str(fm.get("careers_url") or "")))  # explicit ATS link first
            bases = {str(fm.get("slug") or "").lower(), re.sub(r"[^a-z0-9]", "", name.lower())} - {""}
            guesses += [(ats, b) for b in sorted(bases) for ats in BOARD_API if (ats, b) not in guesses]
            out.append((name, guesses))
    return out


def _first_live_board(client, guesses) -> tuple[str, str] | None:
    for ats, slug in guesses:
        try:
            r = client.get(BOARD_API[ats].format(slug))
            if r.status_code == 200:
                data = r.json()
                if data.get("jobs") if isinstance(data, dict) else data:
                    return ats, slug
        except Exception:
            continue
    return None


def from_remoteintech(session: Session, client) -> int:
    """Verify and store boards for worldwide/APAC companies in the remoteintech directory."""
    known = _known(session)
    candidates = [(name, guesses) for name, guesses in remoteintech_candidates(get(client, REMOTEINTECH).content)
                  if guesses and not any(g in known for g in guesses)]
    with ThreadPoolExecutor(8) as pool:
        live = list(pool.map(lambda c: (c[0], _first_live_board(client, c[1])), candidates))
    added = 0
    for name, board in live:
        if board and board not in known:
            session.add(AtsBoard(ats=board[0], slug=board[1], name=name, origin="remoteintech"))
            known.add(board)
            added += 1
    session.commit()
    return added
