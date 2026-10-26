from scanner.normalize import RawJob, html_to_text
from scanner.sources.common import get, parse_iso, salary

QUERIES = [{}, {"geo": "apac"}, {"industry": "data-science"}, {"tag": "analyst"}, {"tag": "data"},
           {"tag": "sql"}, {"tag": "junior"}, {"tag": "intern"}, {"tag": "entry level"}]


def fetch(client) -> list[RawJob]:
    jobs = {}
    for params in QUERIES:
        r = get(client, "https://jobicy.com/api/v2/remote-jobs", {"count": 100, **params}, pause=1.0)
        for j in r.json().get("jobs", []):
            jobs[str(j["id"])] = RawJob(
                source="jobicy", source_id=str(j["id"]), url=j["url"], company=j["companyName"],
                title=html_to_text(j["jobTitle"]), description=html_to_text(j.get("jobDescription")),
                location_raw=j.get("jobGeo") or "",
                salary_raw=salary(j.get("annualSalaryMin"), j.get("annualSalaryMax"), j.get("salaryCurrency") or "", "annual"),
                posted_at=parse_iso(j.get("pubDate")), extra={"level": j.get("jobLevel")})
    return list(jobs.values())
