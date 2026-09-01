# competitors/wappalyzer.py
"""Wappalyzer tech-fingerprint 'who else runs this' competitor lookup.

Two-step: detect `domain`'s tech stack (local scan), then for each technology
ask api.wappalyzer.com for other hostnames running it.
"""
import json

from curl_cffi import requests as rq
from wappalyzer import analyze

from ..net import RateLimited, get_user_agent, normalize_domain, to_registrable_domain


def _slugify(tech_name):
    # wappalyzer.com's URL slugs are just lowercased, space-dashed tech names
    return tech_name.strip().lower().replace(" ", "-")


def _get_technologies(domain):
    url = domain if domain.startswith("http") else f"https://{domain}"
    # 'balanced' avoids the playwright/chromium dependency the 'full' scan_type needs
    results = analyze(url=url, scan_type="fast", timeout=30)
    return list(results.get(url, {}).keys())


def _get_hostnames(slug):
    with rq.Session(impersonate="chrome110") as s:
        r = s.get(
            f"https://api.wappalyzer.com/v2/technologies/{slug}",
            params={"view": "page"},
            headers={
                "User-Agent": get_user_agent(),
                "Origin": "https://www.wappalyzer.com",
                "Accept": "application/json",
            },
            timeout=15,
        )
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            ra = int(ra) if ra and ra.isdigit() else None
            raise RateLimited(f"wappalyzer {slug}: rate limited (429)" + (f", retry-after={ra}s" if ra else ""), retry_after=ra)
        if r.status_code != 200:
            raise RuntimeError(f"wappalyzer {slug}: HTTP {r.status_code}: {r.text[:200]!r}")
        try:
            j = r.json()
        except json.JSONDecodeError as e:
            raise RuntimeError(f"wappalyzer {slug}: non-JSON 200 response: {r.text[:200]!r}") from e
        found = set()
        for h in j.get("topHostnames", []):
            hostname = h.get("hostname")
            if not hostname:
                continue
            reg = to_registrable_domain(hostname)
            if reg:
                found.add(normalize_domain(reg))
        return found

def get_wappalyzer_competitors(domain):
    found = set()
    for tech in _get_technologies(domain):
        found |= _get_hostnames(_slugify(tech))
    found.discard(normalize_domain(domain))
    return found