"""Wappalyzer tech-fingerprint 'who else runs this' competitor lookup."""
import json

from curl_cffi import requests as rq
from wappalyzer import analyze

from ..net import RateLimited, get_user_agent, normalize_domain, to_registrable_domain


def _slugify(tech_name):
    return tech_name.strip().lower().replace(" ", "-")


def _get_technologies(domain):
    url = domain if domain.startswith("http") else f"https://{domain}"
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
            raise RateLimited(f"wappalyzer {slug}: rate limited (429)",
                              retry_after=ra)
        if r.status_code != 200:
            raise RuntimeError(
                f"wappalyzer {slug}: HTTP {r.status_code}: {r.text[:200]!r}"
            )
        try:
            j = r.json()
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"wappalyzer {slug}: non-JSON 200: {r.text[:200]!r}"
            ) from e
        out, seen = [], set()
        for h in j.get("topHostnames", []):
            hostname = h.get("hostname")
            if not hostname:
                continue
            reg = to_registrable_domain(hostname)
            d = normalize_domain(reg) if reg else None
            if d and d not in seen:
                seen.add(d)
                out.append(d)
        return out


def get_wappalyzer_competitors(domain):
    out, seen = [], set()
    for tech in _get_technologies(domain):
        for d in _get_hostnames(_slugify(tech)):
            if d != normalize_domain(domain) and d not in seen:
                seen.add(d)
                out.append(d)
    return out