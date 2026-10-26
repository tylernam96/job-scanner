"""Store raw jobs, merging cross-posts and reposts into one Job (PRD §5.2)."""
from collections import Counter
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from scanner.db import Job, Posting, utcnow
from scanner.normalize import RawJob, canonical_key, content_hash


def ingest(session: Session, raws: list[RawJob], repeat_window_days: int = 60) -> Counter:
    """Returns counts: new (new Job), merged (new Posting on an existing Job), seen (Posting already stored)."""
    counts = Counter()
    now = utcnow()
    window_start = now - timedelta(days=repeat_window_days)

    for raw in raws:
        posting = session.scalar(
            select(Posting).where(Posting.source == raw.source, Posting.source_id == raw.source_id)
        )
        if posting:
            posting.job.last_seen_at = now
            counts["seen"] += 1
            continue

        key = canonical_key(raw.company, raw.title)
        chash = content_hash(raw.description)
        match = [Job.canonical_key == key]
        if chash:
            match.append(Job.content_hash == chash)
        job = session.scalar(
            select(Job).where(or_(*match), Job.first_seen_at >= window_start).order_by(Job.id).limit(1)
        )
        if job:
            job.last_seen_at = now
            # Same job posted per region: keep every location so an APAC variant isn't lost.
            locations = job.location_raw.split(" ; ") if job.location_raw else []
            for loc in (raw.location_raw, raw.title if raw.title != job.title else ""):
                if loc and loc not in locations:
                    locations.append(loc)
            job.location_raw = " ; ".join(locations)
            counts["merged"] += 1
        else:
            job = Job(
                company=raw.company, title=raw.title, description=raw.description,
                location_raw=raw.location_raw, salary_raw=raw.salary_raw, posted_at=raw.posted_at,
                canonical_key=key, content_hash=chash, extra=raw.extra,
                first_seen_at=now, last_seen_at=now,
            )
            session.add(job)
            counts["new"] += 1
        session.add(Posting(job=job, source=raw.source, source_id=raw.source_id, url=raw.url,
                            title=raw.title, location_raw=raw.location_raw, first_seen_at=now))
        session.flush()  # later raws in this batch must see this one

    session.commit()
    return counts
