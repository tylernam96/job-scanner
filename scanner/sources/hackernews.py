"""Latest HN "Ask HN: Who is hiring?" thread; one RawJob per top-level comment."""
from scanner.normalize import RawJob, html_to_text
from scanner.sources.common import parse_iso


def fetch(client) -> list[RawJob]:
    r = client.get("https://hn.algolia.com/api/v1/search_by_date",
                   params={"tags": "story,author_whoishiring", "query": "who is hiring"})
    r.raise_for_status()
    story = next(h for h in r.json()["hits"] if h["title"].startswith("Ask HN: Who is hiring?"))
    r = client.get(f"https://hn.algolia.com/api/v1/items/{story['objectID']}", timeout=90)
    r.raise_for_status()
    jobs = []
    for c in r.json()["children"]:
        if not c.get("text"):
            continue
        header = html_to_text(c["text"].split("<p>", 1)[0])[:300]  # "Company | Role | Location | ..."
        company = header.replace("—", "|").split("|", 1)[0].strip()
        jobs.append(RawJob(
            source="hackernews", source_id=str(c["id"]), url=f"https://news.ycombinator.com/item?id={c['id']}",
            company=company, title=header, description=html_to_text(c["text"]), location_raw=header,
            posted_at=parse_iso(c.get("created_at")), extra={"thread": story["title"]}))
    return jobs
