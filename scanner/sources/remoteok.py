from scanner.normalize import RawJob, html_to_text
from scanner.sources.common import get, parse_epoch, salary

TAGS = [None, "analyst", "data", "data-science", "sql", "junior"]


def fetch(client) -> list[RawJob]:
    jobs = {}
    for tag in TAGS:
        r = get(client, "https://remoteok.com/api", {"tag": tag} if tag else None, pause=2.0)
        for j in r.json():
            if "id" not in j:  # first element is a legal notice
                continue
            jobs[str(j["id"])] = RawJob(
                source="remoteok", source_id=str(j["id"]), url=j["url"], company=j["company"],
                title=j["position"], description=html_to_text(j.get("description")),
                location_raw=j.get("location") or "", salary_raw=salary(j.get("salary_min"), j.get("salary_max"), "USD"),
                posted_at=parse_epoch(j.get("epoch")), extra={"tags": j.get("tags") or []})
    return list(jobs.values())
