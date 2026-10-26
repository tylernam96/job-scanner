from scanner.normalize import RawJob, html_to_text
from scanner.sources.common import parse_iso


def fetch(client) -> list[RawJob]:
    r = client.get("https://www.workingnomads.com/api/exposed_jobs/")
    r.raise_for_status()
    return [
        RawJob(source="workingnomads", source_id=j["url"], url=j["url"], company=j["company_name"],
               title=j["title"], description=html_to_text(j.get("description")),
               location_raw=j.get("location") or "", posted_at=parse_iso(j.get("pub_date")),
               extra={"category": j.get("category_name"), "tags": j.get("tags")})
        for j in r.json()
    ]
