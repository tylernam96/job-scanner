# PRD — Job Scanner ("Thailand-Friendly Remote Job Finder")

| | |
|---|---|
| Owner | Tyler Nam |
| Status | Draft v0.1 — 2026-10-23 |
| Phase 1 | Scan → score → daily top 10 → on-demand tailoring (human applies) |
| Phase 2 | One-click approve → automated application submit |

---

## 1. Problem

Most "remote" jobs are actually remote-within-a-country. Finding roles that will accept a candidate **living in Thailand (UTC+7)** means reading dozens of postings a day just to discover the location fine print. Tailoring a resume and cover letter for each good match is a second, repetitive time sink.

## 2. Goal

Every morning (Bangkok time), present **10 new, never-before-shown jobs** that:
1. are realistically open to someone working from Thailand,
2. fit the candidate's experience, and
3. can be tailored (resume + cover letter) with one click.

### Non-goals (Phase 1)
- Automatic submission of applications (Phase 2).
- Scraping sites whose terms forbid it (LinkedIn, Indeed, Glassdoor).
- Inventing or inflating experience in tailored documents.
- Visa / tax advice. (Note: working remotely from Thailand has visa implications, e.g. the Destination Thailand Visa — out of scope for the tool, but worth checking personally.)

---

## 3. Candidate profile (inputs)

| Attribute | Value |
|---|---|
| Location & authorization | Lives in Thailand (UTC+7); holds US work authorization |
| Hire types accepted | Contractor (1099 / B2B) **and** EOR employee (Deel, Remote.com, Oyster, etc.) |
| Working hours | APAC / Australia hours and Europe hours. **Not** US-overlap-required roles. |
| Pay floor | None (learning/experience valued) — still display salary when available |
| Experience | Mid-level data analyst (a few years in analyst roles, overlapping with data engineering: warehouse tables, ETL). No formal SWE / DS / DE title. Details live in the git-ignored master profile. |
| Employment gap | Multi-year career break. Handled by an approved gap narrative (§5.9) — never hidden, never invented. |
| Core skills | SQL, Tableau, Excel, data analysis, data modeling, data engineering (pipeline-adjacent) |
| Industry exclusions | None |
| Master resume | PDF/DOCX dropped into `resume/` → converted to `profile/master_profile.json` (both git-ignored; `profile/example/` holds a fictional sample) |

### Target roles & weighting — "Analyst-first"

| Family | Example titles | Seniority accepted | Weight |
|---|---|---|---|
| **A. Data / BI Analyst (primary)** | Data Analyst, BI Analyst, Business/Product/Marketing Analyst, Reporting Analyst, Tableau Developer, Analytics Engineer | Junior → Senior (not Lead/Manager/Principal) | 1.0 |
| **B. Stretch: Data Engineering** | Junior/Associate Data Engineer, Analytics Engineer, ETL Developer | Intern → Junior/Mid | 0.8 |
| **C. Stretch: Software Engineering** | Junior/Associate SWE, SWE Intern, Apprentice, Graduate programs | Intern → Junior only | 0.6 |
| **D. Stretch: Data Science** | Junior Data Scientist, DS Intern, Associate DS | Intern → Junior only | 0.6 |

Stretch roles (B–D) only make the daily 10 when their fit score is high; they should favor postings that mention mentorship, training, or "learning opportunity".

**Daily mix target:** ~7 from Family A, up to ~3 from B–D. If B–D don't clear the threshold, fill with A.

---

## 4. User stories

1. As the user, each morning I open the dashboard and see 10 new jobs ranked by fit, each showing *why* it fits and *why* it's believed to be Thailand-friendly.
2. I never see a job I've already been shown — including the same job reposted on another board or under a new ID.
3. I click **Tailor** on a job and get, within ~1 minute, a tailored resume and cover letter I can edit, copy, and download.
4. I mark jobs **Applied**, **Saved**, or **Skip (with reason)** and the tool learns from it.
5. I can open a tracker/history view of every job I've acted on.
6. (Phase 2) I click **Approve** and the tool submits the application for me.

---

## 5. Functional requirements

### 5.1 Ingestion (daily, cloud cron)

Run once a day at **06:00 Asia/Bangkok** (23:00 UTC).

**v1 sources — free and terms-friendly:**

| Source | Access | Why |
|---|---|---|
| Himalayas | Public JSON API | Has structured location-restriction + timezone fields |
| Remotive | Public JSON API | `candidate_required_location` field |
| RemoteOK | Public JSON API | Large remote volume |
| Jobicy | Public JSON API | Geo filter (`apac`, `anywhere`) |
| We Work Remotely | RSS | High-quality remote postings |
| Working Nomads | Public JSON/RSS | Nomad-friendly by design |
| HN "Who is hiring" | Algolia API (monthly thread) | Many "REMOTE (worldwide)" startups |
| **Curated ATS boards** | Greenhouse / Lever / Ashby public board APIs | Direct from companies known to hire globally; also the path to Phase 2 auto-apply |

- **Volume tuning (2026-10-26):** Himalayas is queried with 14 role keywords (analyst, analytics, data, sql, tableau, power bi, business intelligence, data engineer/scientist, junior, intern, entry level, graduate, associate), each paged to the end with a 3 s pause + backoff (it rate-limits hard; a run takes ~11 min). RemoteOK and Jobicy are also queried by tag, We Work Remotely by category feed. Measured result: qualifying pool 26 → 73; ~4–7 new qualifying jobs/day, which meets the target of 3–5 new jobs a day.
- **Board discovery (2026-10-26):** Greenhouse/Lever/Ashby have no cross-company search, so the scanner grows its own list of boards (`ats_boards` table, `scanner/discover.py`): (1) daily, any ATS link found inside other sources' postings (mostly the HN thread); (2) weekly, companies in the open remoteintech.company directory whose hiring region is worldwide or Asia-Pacific, verified against each ATS API. Boards that start returning 404 are deactivated. Every posting is still location-classified individually.
- Curated company list lives in `config/companies.yaml` (seeded with known global-remote / EOR-friendly employers; user-editable).
- Only keep postings **≤ 14 days old** (aggregators only — curated ATS boards list currently-open roles, many evergreen, so every listed role counts as fresh the first time it's seen).
- Ingest-time title prefilter: only titles matching a role family (keywords.yaml) are stored; seniority/location are judged in M2.
- Store raw posting + normalized fields.
- Adding a source = implementing one adapter interface (`fetch() → RawJob[]`).

**Explicitly excluded in v1:** LinkedIn, Indeed, Glassdoor, Wellfound (ToS / anti-bot / paid). Revisit if volume is too low.

### 5.2 Normalization & de-duplication — "no repeats"

Each job gets:
- `canonical_key` = normalized(company) + normalized(title) (lowercase, strip punctuation, strip "remote", "(m/f/d)", location suffixes).
- `content_hash` = similarity hash of the description (catches reposts with tweaked titles).

Rules:
1. **A job that has been shown is never shown again** (`shown_jobs` table keyed by job ID *and* `canonical_key`).
2. Same `canonical_key` or near-identical `content_hash` from another source/ID within **60 days** = duplicate → merged, not re-surfaced.
3. Companies skipped with reason "company" are never shown again.
4. Cross-source duplicates found on the same day are merged into one record that lists all source links.

### 5.3 Thailand-friendliness classifier (tiered)

Each job is labeled with a tier **and the evidence quote** that drove the label.

| Tier | Meaning | Example signals |
|---|---|---|
| ✅ **Confirmed** | Explicitly open to Thailand | "Worldwide", "Anywhere", "APAC", "Asia", "Thailand", "Southeast Asia", timezone range including UTC+7, Himalayas restriction list including Thailand / empty |
| 🟡 **Likely** | Structurally open, not explicit | Contractor/B2B wording; mentions Deel/Remote/Oyster/EOR; company on curated "hires globally" list; "EMEA + APAC"; Europe-hours timezone requirement without residency restriction |
| ⚪ **Unknown** | Remote, no location info | — shown, but ranked below Confirmed/Likely |
| ❌ **Excluded (hidden)** | Residency-restricted or US-hours | "US only", "must reside in…", state lists, "EU/UK residents only", "must work PST/EST hours", required on-site travel |

Important nuance: **"Must be authorized to work in the US" alone is NOT an exclusion** (the candidate holds US work authorization). Only *residency/location* requirements exclude. Ambiguous cases → Unknown with a note.

Implementation: rules/regex first (cheap, deterministic), then the LLM resolves only the ambiguous ones. Hidden jobs are still stored (with reason) so the filter can be audited.

Rule details learned from the M2 blind-labeled sample (45 postings, `tests/fixtures/location_labels.json`):
- **Strong signals** (location field names APAC/Asia/Thailand/UTC+7) are trusted outright.
- **Broad tags** ("Worldwide", "Anywhere in the World", Himalayas' empty restriction list) are often auto-applied by boards and wrong, so a region restriction in the description ("based in Latin America", "Remote - USA", "English-speaking country") overrides them.
- A location field naming only non-Thai regions (incl. EMEA alone) = excluded.
- Jobs seen only on company ATS boards must carry an explicit remote signal — those boards also list office roles (e.g. Agoda Bangkok).
- Result: 15/16 = 94% precision on Confirmed (target ≥ 90%); test `test_precision_on_hand_labeled_sample` guards it.

### 5.4 Fit scoring (0–100)

Two stages, to stay under budget:

**Stage 1 — rules (free):** title include/exclude patterns per role family; seniority exclusions (Lead, Manager, Principal, Staff, Director, Head of; for Families C/D also exclude Senior/Mid); location tier ≠ Excluded.

**Stage 2 — LLM (cheap model, e.g. Claude Haiku):** for jobs that pass Stage 1, score against `master_profile.json` and return structured JSON:

```json
{
  "fit_score": 82,
  "role_family": "A",
  "skills_matched": ["SQL", "Tableau", "data modeling"],
  "skills_missing": ["dbt"],
  "seniority_fit": "good",
  "learning_opportunity": false,
  "location_tier": "confirmed",
  "location_evidence": "\"We hire anywhere in UTC+5 to UTC+10\"",
  "timezone_note": "Async team, APAC hours",
  "reasons": ["4/5 core skills match", "Explicitly hires in APAC"],
  "red_flags": []
}
```

Score composition (tunable in `config/scoring.yaml`):
- Skills match — 40
- Seniority / experience fit — 20
- Location tier — 20 (Confirmed 20 / Likely 12 / Unknown 5)
- Timezone compatibility — 10
- Learning opportunity (stretch families) / role-family weight — 10
- Then × role-family weight (A 1.0, B 0.8, C/D 0.6) and + learned preference adjustments (§5.7)

Master profile is sent via prompt caching so per-job cost stays low.

### 5.5 Daily selection — "10 a day"

1. Candidate pool = scored jobs, not previously shown, not hidden.
2. Rank by final score; respect mix target (~7 A / ≤3 B–D).
3. Take top 10 → record in `shown_jobs` with today's date.
4. **Minimum quality bar:** jobs below `min_score` (default 55) are not used to pad the list. If fewer than 10 qualify, show fewer and say so ("only 6 qualified today"). Unshown qualifying jobs carry over to the next day if still fresh.

### 5.6 Web dashboard

Hosted, private (behind auth — single user).

**Today view**
- 10 cards: title, company, salary (if listed), role family, fit score, location tier badge + evidence quote on hover, top 3 reasons, red flags, posted date, source link(s).
- Actions: **Tailor**, **Save**, **Applied**, **Skip ▾** (reasons: location wrong, too senior, too junior, not my field, company, pay, other).

**Job detail view**
- Full job description.
- Tailored resume (rendered, with a diff vs. master highlighting what changed).
- Cover letter (editable text area).
- Export: Copy text, **DOCX**, **PDF**.
- Regenerate button (with optional instruction, e.g. "emphasize Tableau more").

**Tracker view**
- Table of all jobs acted on: date shown, status, applied date, notes. Filter by status. CSV export.

**Settings view**
- Keyword include/exclude lists, blocked companies, min score, curated company list. (Mirrors YAML config.)

### 5.7 Learning from feedback ("simple signals")

- Skip reason **company** → add to blocklist.
- Skip reason **too senior / too junior / not my field** → adjust title/seniority weights for that pattern.
- Skip reason **location wrong** → logged as classifier error for review (classifier precision is a key metric).
- **Applied / Saved** → positive signal; boost similar titles/skills.
- A short "learned preferences" summary (auto-generated weekly from actions) is appended to the scoring prompt.
- All learned adjustments visible and reversible in Settings.

### 5.8 On-demand tailoring

Triggered only by clicking **Tailor**. Uses a stronger model (e.g. Claude Sonnet).

**Resume — "Reorder & reword only":**
- Input: `master_profile.json` + job description.
- Allowed: reorder sections/bullets, rephrase bullets using the JD's vocabulary *where truthful*, select which bullets/skills to show, rewrite summary.
- **Forbidden:** adding skills, tools, employers, titles, dates, metrics, or certifications not in the master profile. A post-generation check verifies every skill/tool/number in the output exists in the master profile; violations are flagged and blocked.
- Output: structured JSON → rendered to DOCX/PDF via a single ATS-friendly template (one column, no tables/graphics).

**Cover letter:**
- 250–350 words, 3–4 paragraphs.
- Must reference something specific from the posting/company.
- States location and working hours plainly and positively (e.g. "Based in Thailand (UTC+7), comfortable working APAC and European hours, set up to work as a contractor or via EOR").
- Same no-fabrication rule.
- Tone: configurable; default professional and direct, no clichés ("I am writing to express my interest…").

### 5.9 Employment-gap narrative

The candidate has a multi-year employment gap. Hiring managers' usual worries are: (1) are skills stale, (2) why the gap, (3) will they stay. The tool handles this consistently:

- `profile/gap_story.yaml` holds **user-written, user-approved** facts: what the time was spent on, the one-line resume entry, the 1–2 sentence cover-letter framing, and a longer interview answer. Tailoring may rephrase these but never adds to them.
- **Resume:** a dated entry for the gap (e.g. "Career break — relocation to Thailand · 2024 – present") plus a **Projects** section listing recent, verifiable work (this job-scanner project counts — Python, SQL/Postgres, ETL, LLM APIs, GitHub Actions).
- **Cover letter:** at most 1–2 sentences, positive and forward-looking. Mentions what was done and why the candidate is ready now. Never apologetic, never the opening line.
- **Scoring:** no penalty, but jobs mentioning "returnship", "career returners", or "career break welcome" get a small boost.

---

## 6. Phase 2 — Auto-apply (one-click approve)

Scoped now so Phase 1 decisions don't block it.

- User clicks **Approve** on a tailored job → system submits.
- **Start with Greenhouse, Lever, Ashby** application forms (standardized, most common among curated companies).
- Uses browser automation (headless) to fill the form with the tailored resume, cover letter, and standard fields.
- **Answer bank** (`profile/answers.yaml`): work authorization, location, salary expectations, notice period, links, how-did-you-hear, demographic (decline to answer by default).
- Any screening question not in the answer bank → **pause and ask the user** in the dashboard; the answer is saved for next time. Never guess.
- Sites with CAPTCHA / bot checks → fall back to "Apply manually" with a prefilled packet. The tool will not bypass CAPTCHAs.
- Every submission logged with a screenshot of the confirmation page.
- Daily cap (default 10) and per-company cap (1 per 30 days).

---

## 7. Architecture (proposed)

Constraints: cloud schedule, web dashboard with on-demand server actions, < $20/month.

**Recommended: Python — GitHub Actions (cron) + hosted Postgres + Streamlit dashboard**

```
┌──── GitHub Actions: scheduled workflow (23:00 UTC daily) ────┐
│  python -m scanner.run_daily                                  │
│   source adapters → normalize → dedupe → rule filter          │
│   → LLM score (Haiku, cached profile) → select top 10         │
└──────────────────────────────┬────────────────────────────────┘
                               ▼
                 Postgres (Neon or Supabase, free tier)
                               ▲
┌──────────────────────────────┴────────────────────────────────┐
│  Streamlit dashboard (Streamlit Community Cloud, private app) │
│   Today / Detail / Tracker / Settings                         │
│   "Tailor" button → scanner.tailor (Sonnet) → DOCX/PDF        │
│   generated on download from stored JSON                      │
└───────────────────────────────────────────────────────────────┘
```

**Why this shape**
- **One Python codebase** (`scanner/` package) shared by the cron job and the dashboard. The dashboard imports the same `tailor`, `scoring`, and `db` modules — no separate API layer.
- **GitHub Actions** cron is free, reliable, keeps run logs, and allows a manual "Run now" (`workflow_dispatch`). It doesn't depend on the dashboard host being awake.
- **Postgres** because two different hosts (Actions + dashboard) need the same DB; SQLite in a file can't be shared safely.
- **Streamlit** is the fastest way to build a data-heavy UI in pure Python (tables, filters, buttons, downloads) and is familiar territory for an analyst. Community Cloud supports a private app restricted to your email. Cold start after idle is a few seconds — acceptable for a once-a-day dashboard.

**Repo layout**

```
job_scanner/
├── scanner/
│   ├── sources/          # one adapter per board: himalayas.py, remotive.py, greenhouse.py …
│   ├── normalize.py      # canonical_key, content_hash
│   ├── dedupe.py
│   ├── location.py       # Thailand-tier rules + LLM fallback
│   ├── scoring.py        # Stage 1 rules + Stage 2 LLM
│   ├── select.py         # daily top 10, mix target, no-repeat
│   ├── feedback.py       # learned preferences
│   ├── tailor.py         # resume + cover letter + fabrication check
│   ├── render.py         # JSON → DOCX / PDF
│   ├── db.py             # SQLAlchemy models + session
│   └── run_daily.py      # cron entry point
├── dashboard/app.py      # Streamlit
├── config/               # scoring.yaml, companies.yaml, keywords.yaml
├── profile/              # master_profile.json, answers.yaml (gitignored if repo is public)
├── resume/               # original resume file (gitignored)
├── tests/                # pytest: classifier labeled set, dedupe, fabrication check
└── .github/workflows/daily.yml
```

**Libraries**

| Purpose | Library |
|---|---|
| Dependency management | `pyproject.toml` + `venv`/`pip` (`uv` optional) |
| HTTP / RSS | `httpx`, stdlib `xml.etree` |
| Data validation | `pydantic` (job schema, LLM JSON output) |
| DB | `SQLAlchemy` 2.x + `psycopg`, migrations with `alembic` |
| Dedupe | Normalized company+title key + SHA-1 of description words (add fuzzy matching only if misses show up) |
| LLM | `anthropic` SDK (prompt caching for the master profile) |
| Resume output | `python-docx` (DOCX), `weasyprint` from an HTML template (PDF) |
| Dashboard | `streamlit` |
| Tests | `pytest` |
| Phase 2 | `playwright` (Python) |

**Secrets:** Anthropic key + DB URL stored as GitHub Actions secrets and Streamlit secrets. Never committed. Resume and profile files are git-ignored; the scheduled workflow writes them from secrets (see README).

**Alternative (if Streamlit becomes limiting):** FastAPI + Jinja/HTMX on Fly.io or Render (~$0–5/mo), same `scanner/` package underneath. Only worth it for a custom UI, or for Phase 2 if a long-lived server is needed.

**Phase 2 runner:** "Approve" writes a row to an `apply_queue` table and triggers a GitHub Actions workflow (`workflow_dispatch` via the GitHub API). That workflow runs Playwright headless, submits, and writes the result + confirmation screenshot back to the DB. No always-on server needed.

### Data model (Postgres)

| Table | Key fields |
|---|---|
| `postings` | job_id, source, source_id (unique per source), url, title, location_raw — one row per listing; many per job |
| `jobs` | id, company, title, description, location_raw, salary_raw, posted_at, fetched_at, canonical_key, content_hash, role_family, location_tier, location_evidence, hidden_reason |
| `scores` | job_id, fit_score, breakdown_json, reasons_json, red_flags_json, model, scored_at |
| `shown_jobs` | job_id, canonical_key, shown_date, rank |
| `actions` | job_id, action (applied/saved/skipped/approved), reason, note, at |
| `tailored` | job_id, version, resume_json, cover_letter, model, instruction, created_at (DOCX/PDF rendered on download) |
| `preferences` | key, value, source (manual/learned), updated_at |
| `companies` | name, blocked, hires_globally, ats_type, ats_slug |
| `runs` | run_date, per-source counts, errors, cost_estimate |
| `apply_queue` (Phase 2) | job_id, status (queued/running/submitted/needs_input/failed), pending_question, screenshot, updated_at |

### Budget estimate (to validate in M2)

| Item | Est./month |
|---|---|
| GitHub Actions (private repo: ~10 min/day ≪ 2,000 free min/mo) | $0 |
| Postgres — Neon or Supabase free tier | $0 |
| Streamlit Community Cloud | $0 |
| Scoring — Haiku, ~100–200 jobs/day after rule filter, cached profile | a few $ |
| Tailoring — Sonnet, ≤10/day on demand | a few $ |
| **Target** | **< $20** — hard monthly cap enforced in code; scoring degrades to rules-only if the cap is hit |

---

## 8. Milestones

| # | Milestone | Done when (verifiable) |
|---|---|---|
| M0 | Setup + profile | Resume converted to `master_profile.json`; user confirms it's accurate; config files created |
| M1 | Ingestion + dedupe | ≥5 sources fetch daily into Postgres via GitHub Actions; re-running same day adds 0 duplicates; a known cross-posted job merges to one record |
| M2 | Classify + score + select | Location classifier passes a hand-labeled set of ~50 postings with ≥90% precision on Confirmed/Likely; daily top 10 produced; no repeats across 7 simulated days |
| M3 | Dashboard | Today/Detail/Tracker views live behind auth; actions persist |
| M4 | Tailoring | Tailor produces resume + letter + DOCX/PDF in < 90 s; fabrication check passes on 10 sample jobs |
| M5 | Feedback learning | Skip/apply signals change next day's ranking; adjustments visible/reversible |
| M6 | Phase 2 auto-apply | Greenhouse/Lever/Ashby submit with approval; unknown questions pause correctly |

## 9. Success metrics

- **0 repeats** shown across days (hard requirement).
- **≥ 80%** of shown jobs not skipped for "location wrong" (classifier precision).
- **≥ 50%** of shown jobs saved or applied to after the first 2 weeks of feedback.
- Daily run completes by 07:00 Bangkok on ≥ 95% of days; failures surfaced on dashboard.
- Monthly spend < $20.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Low volume of truly Thailand-friendly roles | Curated ATS company list; HN thread; add sources over time; "show fewer, not junk" rule |
| Location fine print misread | Evidence quote always shown; "location wrong" skip feeds a review queue |
| Tailoring overstates experience | Strict reorder/reword rule + automated fabrication check |
| Source API changes/breaks | Per-source error tracking in `runs`; one broken source doesn't fail the run |
| Phase 2 auto-apply errors / ToS | Approval required per job; only ATS forms; no CAPTCHA bypass; screenshots logged |

---

## 11. Open questions

1. **Contact details / links** for the resume header and answer bank: LinkedIn, GitHub, portfolio (Tableau Public?), email.
2. **Cover letter tone:** professional-direct (default) or warmer/more personal? Any sample letter you like?
3. **Run time:** is 06:00 Bangkok right?
4. **Stack:** ~~Cloudflare vs Python~~ — decided: Python (GitHub Actions + Postgres + Streamlit). Remaining: Neon or Supabase for the DB? (Default: Neon.)
5. **Salary display:** show in USD only, or also THB?
6. **Curated companies:** any specific companies you already want to track?
7. **"Analytics Engineer" titles** — treat as primary (Family A) or stretch (Family B)? Currently listed in both; proposed: A when the JD is SQL/dbt/BI-heavy, B when it's pipeline/infrastructure-heavy.
