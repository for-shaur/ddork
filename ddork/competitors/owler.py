# competitors/owler.py
"""Owler 'basic search' competitor lookup."""
import json

from curl_cffi import requests as rq

from ..net import RateLimited, get_user_agent, normalize_domain


def get_owler_competitors(domain):
    # ponytail: owler indexes by company name, not domain, so we search on the
    # bare label (e.g. "hive" from "hive.com") and treat all results as competitors
    term = domain.split(".")[0]
    with rq.Session(impersonate="chrome110") as s:  # impersonation + warmup GET to pick up Akamai cookies (ak_bmsc/bm_sv)
        headers = {
            "User-Agent": get_user_agent(),
            "Accept": "*/*",
            "Referer": "https://www.owler.com/search",
        }
        s.get("https://www.owler.com/search", headers=headers, timeout=15)
        r = s.get(
            "https://www.owler.com/a/v1/pb/basicSearchInternal",
            params={"searchTerm": term},
            headers=headers,
            timeout=15,
        )
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            ra = int(ra) if ra and ra.isdigit() else None
            raise RateLimited(f"owler {domain}: rate limited (429)" + (f", retry-after={ra}s" if ra else ""), retry_after=ra)
        if r.status_code != 200:
            raise RuntimeError(f"owler {domain}: HTTP {r.status_code}: {r.text[:200]!r}")
        try:
            j = r.json()
        except json.JSONDecodeError as e:
            raise RuntimeError(f"owler {domain}: non-JSON 200 response: {r.text[:200]!r}") from e
        return {normalize_domain(x["primaryDomain"]) for x in j.get("results", []) if x.get("primaryDomain")}