"""Himalayas search API, restricted to jobs open to candidates in Thailand.

Every query is paged to the end. Himalayas rate-limits hard, so requests are spaced out.
"""
from scanner.normalize import RawJob, html_to_text
from scanner.sources.common import get, parse_epoch, salary

QUERIES = ["analyst", "analytics", "data", "sql", "tableau", "power bi", "business intelligence",
           "data engineer", "data scientist", "junior", "intern", "entry level", "graduate", "associate"]
PAGE_SIZE = 20
MAX_PAGES = 60   # safety cap per query (1,200 jobs)
PAUSE = 3.0      # seconds between requests


def fetch(client) -> list[RawJob]:
    jobs, failed = {}, []
    for q in QUERIES:
        for page in range(1, MAX_PAGES + 1):
            try:
                r = get(client, "https://himalayas.app/jobs/api/search",
                        {"q": q, "country": "Thailand", "page": page}, pause=PAUSE)
            except Exception as e:  # keep what other queries found; one failure shouldn't drop the source
                failed.append(e)
                break
            batch = r.json().get("jobs", [])
            for j in batch:
                jobs[j["guid"]] = RawJob(
                    source="himalayas", source_id=j["guid"], url=j["applicationLink"] or j["guid"],
                    company=j["companyName"], title=j["title"], description=html_to_text(j["description"]),
                    location_raw=", ".join(j.get("locationRestrictions") or []) or "Worldwide",
                    salary_raw=salary(j.get("minSalary"), j.get("maxSalary"), j.get("currency") or "", j.get("salaryPeriod") or ""),
                    posted_at=parse_epoch(j.get("pubDate")),
                    extra={"location_restrictions": j.get("locationRestrictions") or [],
                           "timezone_restrictions": j.get("timezoneRestrictions") or [],
                           "seniority": j.get("seniority") or [],
                           "thailand_eligible": True},  # queried with country=Thailand
                )
            if len(batch) < PAGE_SIZE:
                break
    if failed and len(failed) == len(QUERIES):
        raise failed[-1]
    return list(jobs.values())
