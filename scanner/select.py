"""Daily top-N selection with the analyst-first mix and no repeats (PRD §5.5)."""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.config import load_yaml
from scanner.db import Job, Score, ShownJob, utcnow

STILL_LISTED_DAYS = 3  # a job counts as open if a source listed it this recently


def select_daily(session: Session, day: date) -> list[ShownJob]:
    """Pick today's jobs. Idempotent: re-running for the same day returns the same list."""
    existing = session.scalars(select(ShownJob).where(ShownJob.shown_date == day).order_by(ShownJob.rank)).all()
    if existing:
        return list(existing)

    sel = load_yaml("scoring.yaml")["selection"]
    now = utcnow()
    shown_keys = set(session.scalars(
        select(ShownJob.canonical_key).where(ShownJob.shown_date >= day - timedelta(days=sel["repeat_window_days"]))
    ))
    candidates = session.execute(
        select(Job, Score)
        .join(Score, Score.job_id == Job.id)
        .outerjoin(ShownJob, ShownJob.job_id == Job.id)
        .where(ShownJob.job_id.is_(None), Job.hidden_reason.is_(None),
               Job.location_tier.in_(["confirmed", "likely", "unknown"]),
               Score.fit_score >= sel["min_score"])
        .order_by(Score.fit_score.desc(), Job.id)
    ).all()

    fresh_cutoff = now - timedelta(days=sel["max_posting_age_days"])
    listed_cutoff = now - timedelta(days=STILL_LISTED_DAYS)
    primary, stretch = [], []
    for job, _ in candidates:
        if job.canonical_key in shown_keys:
            continue
        if job.last_seen_at < listed_cutoff and not (job.posted_at and job.posted_at >= fresh_cutoff):
            continue
        shown_keys.add(job.canonical_key)  # one per canonical key per day too
        (primary if job.role_family == "A" else stretch).append(job)

    n, stretch_max = sel["daily_count"], sel["mix"]["stretch_max"]
    picked_stretch = stretch[:stretch_max]
    picked = primary[: n - len(picked_stretch)]
    # Not enough primary jobs: let more stretch roles in rather than show fewer.
    picked += picked_stretch + stretch[stretch_max: stretch_max + n - len(picked) - len(picked_stretch)]
    picked.sort(key=lambda j: -j.score.fit_score)

    rows = [ShownJob(job=j, canonical_key=j.canonical_key, shown_date=day, rank=i + 1) for i, j in enumerate(picked)]
    session.add_all(rows)
    session.commit()
    return rows
