import xml.etree.ElementTree as ET

from scanner.normalize import RawJob, html_to_text
from scanner.sources.common import get, parse_rfc822

FEEDS = ["remote-jobs"] + [f"categories/{c}" for c in (
    "remote-full-stack-programming-jobs", "remote-back-end-programming-jobs", "remote-front-end-programming-jobs",
    "remote-management-and-finance-jobs", "remote-product-jobs", "remote-sales-and-marketing-jobs",
    "all-other-remote-jobs")]


def fetch(client) -> list[RawJob]:
    jobs = {}
    for feed in FEEDS:
        r = get(client, f"https://weworkremotely.com/{feed}.rss", pause=1.0)
        for item in ET.fromstring(r.content).iter("item"):
            text = lambda tag: (item.findtext(tag) or "").strip()
            company, _, title = text("title").partition(": ")
            key = text("guid") or text("link")
            jobs[key] = RawJob(
                source="weworkremotely", source_id=key, url=text("link"),
                company=company, title=title or company, description=html_to_text(text("description")),
                location_raw=", ".join(x for x in (text("region"), text("country")) if x),
                posted_at=parse_rfc822(text("pubDate")), extra={"category": text("category"), "type": text("type")})
    return list(jobs.values())
