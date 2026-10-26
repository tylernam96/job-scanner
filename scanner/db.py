"""Database models and session. Postgres in production (DATABASE_URL), SQLite locally."""
import os
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from scanner.config import ROOT


def utcnow() -> datetime:
    """Naive UTC; all datetimes are stored naive-UTC so SQLite and Postgres behave the same."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Job(Base):
    """One real-world job. Cross-posts and reposts are merged into a single row (PRD §5.2)."""
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    location_raw: Mapped[str] = mapped_column(Text, default="")
    salary_raw: Mapped[str] = mapped_column(String(300), default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    canonical_key: Mapped[str] = mapped_column(String(800), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(40), index=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    # Set by classification (M2)
    role_family: Mapped[str | None] = mapped_column(String(1))
    location_tier: Mapped[str | None] = mapped_column(String(20))  # confirmed/likely/unknown/excluded
    location_evidence: Mapped[str] = mapped_column(Text, default="")
    hidden_reason: Mapped[str | None] = mapped_column(String(300))

    postings: Mapped[list["Posting"]] = relationship(back_populates="job")
    score: Mapped["Score | None"] = relationship(back_populates="job")


class Posting(Base):
    """A job as listed on one source. Many postings can point to one Job."""
    __tablename__ = "postings"
    __table_args__ = (UniqueConstraint("source", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    source: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(500), default="")
    location_raw: Mapped[str] = mapped_column(Text, default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped[Job] = relationship(back_populates="postings")


class Score(Base):
    """Latest fit score for a job (PRD §5.4). `model` is "rules" or the LLM id."""
    __tablename__ = "scores"

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    fit_score: Mapped[float]
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    red_flags: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(50))
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    scored_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped[Job] = relationship(back_populates="score")


class ShownJob(Base):
    """Every job ever put in a daily list. Never shown twice (PRD §5.2, §5.5)."""
    __tablename__ = "shown_jobs"

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(800), index=True)
    shown_date: Mapped[date] = mapped_column(Date, index=True)
    rank: Mapped[int]

    job: Mapped[Job] = relationship()


class Tailored(Base):
    """A tailored resume + cover letter for one job (PRD §5.8). New version per generation."""
    __tablename__ = "tailored"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    version: Mapped[int]
    resume: Mapped[dict] = mapped_column(JSON)          # TailoredResume, validated
    cover_letter: Mapped[str] = mapped_column(Text)     # user may edit this
    violations: Mapped[list] = mapped_column(JSON, default=list)  # fabrication check; non-empty = blocked
    instruction: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(50))
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Action(Base):
    """User actions from the dashboard (PRD §5.6). A job's status is its latest action."""
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))  # saved / applied / skipped / reset
    reason: Mapped[str | None] = mapped_column(String(50))  # skip reason
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped[Job] = relationship()


class AtsBoard(Base):
    """A company job board found automatically (PRD §5.1). Polled alongside config/companies.yaml."""
    __tablename__ = "ats_boards"
    __table_args__ = (UniqueConstraint("ats", "slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ats: Mapped[str] = mapped_column(String(20))   # greenhouse / lever / ashby
    slug: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300))
    origin: Mapped[str] = mapped_column(String(50))  # "remoteintech" or "link:<source>"
    active: Mapped[bool] = mapped_column(default=True)  # False once the board 404s
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)  # {source: {fetched, relevant, new, merged, seen}}
    errors: Mapped[dict] = mapped_column(JSON, default=dict)  # {source: message}


def make_session(url: str | None = None):
    url = url or os.environ.get("DATABASE_URL") or f"sqlite:///{ROOT / 'data/jobs.db'}"
    if url.startswith("sqlite:///") and ":memory:" not in url:
        (ROOT / "data").mkdir(exist_ok=True)
    if url.startswith("postgres://"):  # Neon/Supabase style URL -> psycopg driver
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine)()
