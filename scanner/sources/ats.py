"""Curated company boards (config/companies.yaml): Greenhouse, Lever, Ashby.
Everything listed is currently open, so no posting-age filter applies to these."""
from scanner.config import load_companies
from scanner.normalize import RawJob, html_to_text
from scanner.sources.common import parse_epoch, parse_iso


def greenhouse(client, name, slug) -> list[RawJob]:
    r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"})
    r.raise_for_status()
    return [
        RawJob(source="greenhouse", source_id=f"{slug}:{j['id']}", url=j["absolute_url"], company=name,
               title=j["title"], description=html_to_text(j.get("content")),
               location_raw=(j.get("location") or {}).get("name") or "",
               posted_at=parse_iso(j.get("first_published") or j.get("updated_at")))
        for j in r.json()["jobs"]
    ]


def lever(client, name, slug) -> list[RawJob]:
    r = client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    r.raise_for_status()
    jobs = []
    for j in r.json():
        lists = "\n\n".join(f"{l['text']}\n{html_to_text(l['content'])}" for l in j.get("lists", []))
        cats = j.get("categories") or {}
        sal = j.get("salaryRange") or {}
        jobs.append(RawJob(
            source="lever", source_id=f"{slug}:{j['id']}", url=j["hostedUrl"], company=name, title=j["text"],
            description="\n\n".join(x for x in (j.get("descriptionPlain"), lists, j.get("additionalPlain")) if x),
            location_raw=", ".join(x for x in (cats.get("location"), *(cats.get("allLocations") or []), j.get("workplaceType")) if x),
            salary_raw=f"{sal.get('currency', '')} {sal.get('min', '')}–{sal.get('max', '')} {sal.get('interval', '')}".strip() if sal else "",
            posted_at=parse_epoch(j.get("createdAt"), ms=True), extra={"commitment": cats.get("commitment")}))
    return jobs


def ashby(client, name, slug) -> list[RawJob]:
    r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"})
    r.raise_for_status()
    jobs = []
    for j in r.json()["jobs"]:
        locs = [j.get("location") or ""] + [l.get("location", "") for l in j.get("secondaryLocations") or []]
        jobs.append(RawJob(
            source="ashby", source_id=f"{slug}:{j['id']}", url=j["jobUrl"], company=name, title=j["title"],
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            location_raw=", ".join(x for x in locs + [j.get("workplaceType") or ""] if x),
            salary_raw=(j.get("compensation") or {}).get("compensationTierSummary") or "",
            posted_at=parse_iso(j.get("publishedAt")), extra={"is_remote": j.get("isRemote")}))
    return jobs


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def fetch(client, errors: dict, discovered=()) -> tuple[list[RawJob], set[tuple[str, str]]]:
    """Poll curated boards (config/companies.yaml) plus discovered ones (AtsBoard rows).

    One failing board is logged in `errors` and skipped. Returns (jobs, boards that no longer exist).
    """
    curated = load_companies()
    blocked = {c.name.lower() for c in curated if c.blocked}
    boards = [(c.ats, c.slug, c.name, "curated") for c in curated if not c.blocked]
    seen = {(ats, slug) for ats, slug, *_ in boards}
    boards += [(b.ats, b.slug, b.name, "discovered") for b in discovered
               if (b.ats, b.slug) not in seen and b.name.lower() not in blocked]

    jobs, gone = [], set()
    for ats, slug, name, kind in boards:
        try:
            jobs += FETCHERS[ats](client, name, slug)
        except Exception as e:
            if kind == "discovered" and getattr(getattr(e, "response", None), "status_code", None) == 404:
                gone.add((ats, slug))
            else:
                errors[f"{ats}:{slug}"] = repr(e)[:300]
    return jobs, gone
