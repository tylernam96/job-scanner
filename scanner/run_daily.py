"""Daily entry point: fetch -> prefilter -> dedupe/store -> classify/score -> pick today's jobs.

    python -m scanner.run_daily
"""
from collections import Counter
from datetime import timedelta

from sqlalchemy import select

from scanner import discover
from scanner.config import load_yaml
from scanner.db import AtsBoard, Run, make_session, utcnow
from scanner.dedupe import ingest
from scanner.normalize import RawJob, is_relevant_title
from scanner.scoring import classify_and_score, make_llm_client
from scanner.select import select_daily
from scanner.sources import AGGREGATORS, ats
from scanner.sources.common import make_client


def keep(raw: RawJob, max_age_days: int | None) -> bool:
    if not is_relevant_title(raw.title):
        return False
    if max_age_days is not None and raw.posted_at and raw.posted_at < utcnow() - timedelta(days=max_age_days):
        return False
    return True


def remoteintech_due(session) -> bool:
    """The directory changes slowly: refresh at most weekly (tracked in run counts)."""
    week_ago = utcnow() - timedelta(days=7)
    recent = session.scalars(select(Run).where(Run.started_at >= week_ago, Run.finished_at.is_not(None)))
    return not any("remoteintech" in (r.counts.get("discovery") or {}) for r in recent)


def main(session=None) -> Run:
    selection = load_yaml("scoring.yaml")["selection"]
    session = session or make_session()
    run = Run()
    session.add(run)
    counts, errors = {}, {}

    with make_client() as client:
        batches = []
        for name, fetch in AGGREGATORS.items():
            try:
                batches.append((name, fetch(client), selection["max_posting_age_days"]))
            except Exception as e:  # one broken source must not fail the run
                errors[name] = repr(e)[:300]

        # Grow the list of company boards before polling them (PRD §5.1).
        found = {"from_postings": discover.from_postings(session, [r for _, raws, _ in batches for r in raws])}
        if remoteintech_due(session):
            try:
                found["remoteintech"] = discover.from_remoteintech(session, client)
            except Exception as e:
                errors["remoteintech"] = repr(e)[:300]
        counts["discovery"] = found

        boards = session.scalars(select(AtsBoard).where(AtsBoard.active)).all()
        ats_jobs, gone = ats.fetch(client, errors, boards)
        for b in boards:
            if (b.ats, b.slug) in gone:
                b.active = False
        batches.append(("ats", ats_jobs, None))

    for name, raws, max_age in batches:
        relevant = [r for r in raws if keep(r, max_age)]
        c = Counter(fetched=len(raws), relevant=len(relevant))
        c.update(ingest(session, relevant, selection["repeat_window_days"]))
        counts[name] = dict(c)

    client = make_llm_client()
    counts["scoring"] = classify_and_score(session, client)
    today = (utcnow() + timedelta(hours=7)).date()  # Bangkok date
    picked = select_daily(session, today)
    counts["selected"] = {"date": today.isoformat(), "count": len(picked)}

    run.counts, run.errors, run.finished_at = counts, errors, utcnow()
    session.commit()

    for name, c in counts.items():
        if isinstance(c, dict) and "fetched" in c:
            print(f"{name:15} " + "  ".join(f"{k}={c.get(k, 0)}" for k in ("fetched", "relevant", "new", "merged", "seen")))
    print("discovery      ", counts["discovery"])
    print("scoring        ", counts["scoring"], "(LLM on)" if client else "(rules only: no Anthropic credentials)")
    print(f"\nToday's jobs ({today}):")
    for s in picked:
        j = s.job
        print(f"{s.rank:>2}. [{j.score.fit_score:>5}] {j.role_family} {j.location_tier:9} {j.title[:60]} — {j.company}")
    for name, e in errors.items():
        print(f"ERROR {name}: {e}")
    return run


if __name__ == "__main__":
    main()
