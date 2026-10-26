from scanner.normalize import RawJob, html_to_text
from scanner.sources.common import parse_iso


def fetch(client) -> list[RawJob]:
    r = client.get("https://remotive.com/api/remote-jobs")
    r.raise_for_status()
    return [
        RawJob(source="remotive", source_id=str(j["id"]), url=j["url"], company=j["company_name"],
               title=j["title"], description=html_to_text(j["description"]),
               location_raw=j.get("candidate_required_location") or "", salary_raw=j.get("salary") or "",
               posted_at=parse_iso(j.get("publication_date")), extra={"job_type": j.get("job_type")})
        for j in r.json()["jobs"]
    ]
