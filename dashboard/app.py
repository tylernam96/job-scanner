"""Job Scanner dashboard (PRD §5.6).

    streamlit run dashboard/app.py
"""
import csv
import io
import os
from datetime import date

import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import scanner.tailor as tailoring
from scanner import render
from scanner.config import load_companies, load_yaml
from scanner.db import Action, Job, Run, ShownJob, make_session

FAMILY = {"A": "Analyst", "B": "Data eng (stretch)", "C": "Junior SWE (stretch)", "D": "Junior DS (stretch)"}
TIER = {"confirmed": ("✅", "Confirmed"), "likely": ("🟡", "Likely"), "unknown": ("⚪", "Unknown")}
SKIP_REASONS = ["location wrong", "too senior", "too junior", "not my field", "company", "pay", "other"]
STATUS_ICON = {"saved": "⭐ Saved", "applied": "📨 Applied", "skipped": "⏭️ Skipped"}

st.set_page_config(page_title="Job Scanner", page_icon="🔎", layout="wide")


@st.cache_resource
def _engine(url: str | None):
    return make_session(url).get_bind()


# Streamlit reruns this script on every interaction, so this is a fresh session each time
# and rows written by the scanner show up on refresh.
db = Session(_engine(os.environ.get("DATABASE_URL")))


def latest_actions(db, job_ids) -> dict[int, Action]:
    """Latest action per job; 'reset' clears the status."""
    out = {}
    for a in db.scalars(select(Action).where(Action.job_id.in_(job_ids)).order_by(Action.at, Action.id)):
        out[a.job_id] = a
    return {k: v for k, v in out.items() if v.action != "reset"}


def record(job_id: int, action: str, reason: str | None = None):
    db.add(Action(job_id=job_id, action=action, reason=reason))
    db.commit()


# ---------------------------------------------------------------- Today

def job_card(job: Job, rank: int, status: Action | None):
    score = job.score
    icon, tier = TIER.get(job.location_tier, ("", job.location_tier))
    with st.container(border=True):
        left, right = st.columns([5, 2])
        with left:
            st.markdown(f"**{rank}. {job.title}** · {job.company}")
            meta = [FAMILY.get(job.role_family, ""), f"{icon} {tier} Thailand fit"]
            if job.salary_raw:
                meta.append(f"💰 {job.salary_raw}")
            if job.posted_at:
                meta.append(f"Posted {job.posted_at:%b %d}")
            st.caption(" · ".join(meta))
            if job.location_evidence:
                st.caption(f"📍 {job.location_evidence}")
            for r in (score.reasons or [])[:3]:
                st.markdown(f"- {r}")
            for f in score.red_flags or []:
                st.markdown(f"- ⚠️ {f}")
        with right:
            st.metric("Fit", f"{score.fit_score:.0f}", help=f"Scored by: {score.model}")
            if status:
                label = STATUS_ICON.get(status.action, status.action)
                st.markdown(f"{label}" + (f" — {status.reason}" if status.reason else ""))

        links = " · ".join(f"[{p.source}]({p.url})" for p in job.postings)
        st.markdown(f"Apply / view: {links}")

        b1, b2, b3, b4 = st.columns([1, 1, 2, 1])
        if b1.button("⭐ Save", key=f"save{job.id}"):
            record(job.id, "saved")
            st.rerun()
        if b2.button("📨 Applied", key=f"applied{job.id}"):
            record(job.id, "applied")
            st.rerun()
        reason = b3.selectbox("Skip reason", SKIP_REASONS, key=f"reason{job.id}", label_visibility="collapsed")
        if b4.button("⏭️ Skip", key=f"skip{job.id}"):
            record(job.id, "skipped", reason)
            st.rerun()
        if status and st.button("↩️ Undo status", key=f"reset{job.id}"):
            record(job.id, "reset")
            st.rerun()

        with st.expander("Job description"):
            st.text(job.description[:20000] or "No description.")
        tailored_section(job)


def run_tailor(job: Job, instruction: str = ""):
    with st.spinner("Tailoring resume and cover letter… (usually under a minute)"):
        try:
            tailoring.tailor(db, tailoring.make_client(), job, instruction)
        except Exception as e:  # no API key, network, refusal…
            st.session_state[f"tailor_error{job.id}"] = str(e)
        else:
            st.session_state.pop(f"tailor_error{job.id}", None)
    st.rerun()


def tailored_section(job: Job):
    t = tailoring.latest(db, job.id)
    if err := st.session_state.get(f"tailor_error{job.id}"):
        st.error(f"Tailoring failed: {err}\n\nIs `ANTHROPIC_API_KEY` set in the shell that runs the dashboard?")
    if t is None:
        if st.button("✍️ Tailor resume + cover letter", key=f"tailor{job.id}", type="primary"):
            run_tailor(job)
        return

    with st.expander(f"✍️ Tailored resume + cover letter (v{t.version})", expanded=True):
        if t.violations:
            st.error("**Blocked — the fabrication check failed.** Regenerate, or fix by hand before using:\n\n"
                     + "\n".join(f"- {v}" for v in t.violations))
        st.caption(" · ".join(t.resume.get("emphasis", [])) + f"  ·  {t.model} · ${t.cost_usd:.3f}")
        tab_resume, tab_letter = st.tabs(["Resume", "Cover letter"])
        doc = tailoring.resume_document(t)
        slug = f"{job.company}_{job.title}".replace(" ", "_")[:60]

        with tab_resume:
            st.markdown(f"**Summary.** {doc['summary']}")
            for e in doc["experience"]:
                st.markdown(f"**{e['title']}** — {e['company']} · {e['dates']}")
                for b in e["bullets"]:
                    st.markdown(f"- {b['text']}")
                    if b["source"] and b["source"] != b["text"]:
                        st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ original: {b['source']}")
            for p in doc["projects"]:
                st.markdown(f"**{p['name']}**" + "\n" + "\n".join(f"- {b}" for b in p["bullets"]))
            st.markdown(f"**Skills:** {', '.join(doc['skills'])}")
            if not t.violations:
                d1, d2 = st.columns(2)
                d1.download_button("⬇️ Resume (DOCX)", render.resume_docx(doc), f"Resume_{slug}.docx",
                                   key=f"rdocx{t.id}")
                d2.download_button("⬇️ Resume (PDF)", render.resume_pdf(doc), f"Resume_{slug}.pdf",
                                   key=f"rpdf{t.id}")

        with tab_letter:
            text = st.text_area("Cover letter (editable)", t.cover_letter, height=380, key=f"letter{t.id}")
            if text != t.cover_letter and st.button("💾 Save edits", key=f"saveletter{t.id}"):
                t.cover_letter = text
                db.commit()
                st.rerun()
            if not t.violations:
                d1, d2 = st.columns(2)
                d1.download_button("⬇️ Letter (DOCX)", render.letter_docx(text), f"Cover_letter_{slug}.docx",
                                   key=f"ldocx{t.id}")
                d2.download_button("⬇️ Letter (PDF)", render.letter_pdf(text), f"Cover_letter_{slug}.pdf",
                                   key=f"lpdf{t.id}")

        instruction = st.text_input("Regenerate with an instruction (optional)", key=f"instr{job.id}",
                                    placeholder="e.g. emphasize Tableau and stakeholder work")
        if st.button("🔁 Regenerate", key=f"regen{job.id}"):
            run_tailor(job, instruction)


def today_page():
    days = db.scalars(select(ShownJob.shown_date).distinct().order_by(ShownJob.shown_date.desc())).all()
    st.title("Today's jobs")
    if not days:
        st.info("No daily lists yet. Run the scanner: `python -m scanner.run_daily`")
        return
    day = st.selectbox("List for", days, format_func=lambda d: f"{d:%a %b %d, %Y}")
    rows = db.scalars(select(ShownJob).where(ShownJob.shown_date == day).order_by(ShownJob.rank)).all()
    statuses = latest_actions(db, [r.job_id for r in rows])

    hide_done = st.toggle("Hide skipped", value=False)
    done = sum(1 for r in rows if r.job_id in statuses)
    st.caption(f"{len(rows)} jobs · {done} acted on" + (" · fewer than 10 qualified today" if len(rows) < 10 else ""))
    for r in rows:
        status = statuses.get(r.job_id)
        if hide_done and status and status.action == "skipped":
            continue
        job_card(r.job, r.rank, status)


# ---------------------------------------------------------------- Tracker

def tracker_page():
    st.title("Tracker")
    shown = {s.job_id: s.shown_date for s in db.scalars(select(ShownJob))}
    statuses = latest_actions(db, list(shown))
    choice = st.segmented_control("Status", ["All", "Saved", "Applied", "Skipped", "No action"], default="All")

    table = []
    for job_id, shown_date in sorted(shown.items(), key=lambda x: x[1], reverse=True):
        job = db.get(Job, job_id)
        a = statuses.get(job_id)
        status = a.action if a else "no action"
        if choice and choice != "All" and status != choice.lower():
            continue
        table.append({"Shown": shown_date, "Title": job.title, "Company": job.company,
                      "Fit": job.score.fit_score if job.score else None,
                      "Thailand fit": job.location_tier, "Status": status,
                      "Skip reason": a.reason if a and a.action == "skipped" else "",
                      "Updated": a.at.strftime("%Y-%m-%d") if a else "",
                      "Link": job.postings[0].url if job.postings else ""})
    st.caption(f"{len(table)} jobs")
    st.dataframe(table, width="stretch", hide_index=True,
                 column_config={"Link": st.column_config.LinkColumn("Link", display_text="open")})
    if table:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=table[0].keys())
        writer.writeheader()
        writer.writerows(table)
        st.download_button("Download CSV", buf.getvalue(), file_name=f"job_tracker_{date.today()}.csv")


# ---------------------------------------------------------------- Settings

def settings_page():
    st.title("Settings")
    st.caption("Read-only for now: edit the YAML files in `config/` to change these.")
    sel = load_yaml("scoring.yaml")["selection"]
    c = st.columns(4)
    c[0].metric("Jobs per day", sel["daily_count"])
    c[1].metric("Minimum fit score", sel["min_score"])
    c[2].metric("Stretch roles max", sel["mix"]["stretch_max"])
    c[3].metric("No-repeat window", f"{sel['repeat_window_days']} days")

    companies = load_companies()
    blocked = [x.name for x in companies if x.blocked]
    st.subheader(f"Curated companies ({len(companies)})")
    st.write(", ".join(sorted(x.name for x in companies if not x.blocked)))
    st.write(f"Blocked: {', '.join(blocked) if blocked else 'none'}")

    st.subheader("Last scan")
    run = db.scalars(select(Run).order_by(Run.id.desc()).limit(1)).first()
    if run:
        st.caption(f"Started {run.started_at:%Y-%m-%d %H:%M} UTC")
        st.json(run.counts, expanded=False)
        if run.errors:
            st.error(run.errors)
    total = db.scalar(select(func.count()).select_from(Job))
    visible = db.scalar(select(func.count()).select_from(Job).where(Job.hidden_reason.is_(None),
                                                                     Job.location_tier.is_not(None)))
    st.caption(f"{total} jobs stored · {visible} pass all filters")


st.navigation([
    st.Page(today_page, title="Today", icon="📋", default=True),
    st.Page(tracker_page, title="Tracker", icon="📊", url_path="tracker"),
    st.Page(settings_page, title="Settings", icon="⚙️", url_path="settings"),
]).run()
