"""Distill Intelligence 'competitor finder' lookup."""
import json

from curl_cffi import requests as rq

from ..net import RateLimited, get_user_agent, normalize_domain


def get_distill_competitors(domain):
    with rq.Session(impersonate="chrome110") as s:  # impersonation makes this harder to fingerprint/block
        headers = {
            "User-Agent": get_user_agent(),
            "Origin": "https://www.distillintelligence.com",
            "Referer": "https://www.distillintelligence.com/competitor-finder",
        }
        s.get("https://www.distillintelligence.com/competitor-finder", headers=headers, timeout=15)
        r = s.post(
            "https://www.distillintelligence.com/api/competitors/find",
            headers={**headers, "Content-Type": "text/plain;charset=UTF-8"},
            data=json.dumps({"website": domain}),
            timeout=15,
        )
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            ra = int(ra) if ra and ra.isdigit() else None
            raise RateLimited(f"distill {domain}: rate limited (429)" + (f", retry-after={ra}s" if ra else ""), retry_after=ra)
        if r.status_code != 200:
            # the old code fed this straight to .json() and reported a useless
            # "Expecting value: line 1 column 1" for what's actually a 403/5xx
            raise RuntimeError(f"distill {domain}: HTTP {r.status_code}: {r.text[:200]!r}")
        try:
            j = r.json()
        except json.JSONDecodeError as e:
            raise RuntimeError(f"distill {domain}: non-JSON 200 response: {r.text[:200]!r}") from e
        return {normalize_domain(x.get("domain")) for x in j.get("competitors", []) if x.get("domain")}