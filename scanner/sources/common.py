"""Shared helpers for source adapters."""
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

USER_AGENT = "job-scanner/0.1 (personal job search)"  # Ashby/RemoteOK reject default client UAs


def make_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)


def get(client: httpx.Client, url: str, params: dict | None = None, pause: float = 0.0,
        retries: int = 4) -> httpx.Response:
    """GET with a polite pause before each request and backoff on 429/5xx and dropped connections."""
    for attempt in range(retries + 1):
        time.sleep(pause)
        try:
            r = client.get(url, params=params)
        except httpx.TransportError:  # connection reset, timeout…
            if attempt == retries:
                raise
        else:
            if r.status_code != 429 and r.status_code < 500:
                break
        if attempt < retries:
            time.sleep(10 * 2 ** attempt)
    r.raise_for_status()
    return r


def to_utc(dt: datetime) -> datetime:
    """Naive UTC (see db.utcnow)."""
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_iso(s: str | None) -> datetime | None:
    return to_utc(datetime.fromisoformat(s.replace("Z", "+00:00"))) if s else None


def parse_epoch(v: str | int | float | None, ms: bool = False) -> datetime | None:
    if not v:
        return None
    return datetime.fromtimestamp(float(v) / (1000 if ms else 1), timezone.utc).replace(tzinfo=None)


def parse_rfc822(s: str | None) -> datetime | None:
    return to_utc(parsedate_to_datetime(s)) if s else None


def salary(lo, hi, currency="", period="") -> str:
    lo, hi = (int(float(x)) if x not in (None, "", 0, "0") else None for x in (lo, hi))
    if not (lo or hi):
        return ""
    rng = f"{lo:,}–{hi:,}" if lo and hi else f"{lo or hi:,}"
    return " ".join(x for x in (currency, rng, period) if x)
