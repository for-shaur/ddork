"""Probe a fixed list of well-known disclosure paths. No traversal, no wordlist.

Ordered by likelihood so the most-likely path is probed first. All requests go through
the shared AIMD limiter.

v3.1: uses the shared per-thread session. Also, 404s are now clean misses (return None)
rather than AIMD failures — the limiter only reacts to real network errors.
"""
from ..config import log
from ..net import get_session, canonicalize_url, gated_sync, get_user_agent

# Ordered: most likely a real disclosure/program page first.
PATHS = (
    "/security",
    "/bug-bounty",
    "/responsible-disclosure",
    "/vulnerability-disclosure",
    "/security/report",
    "/bugbounty",
    "/legal/security",
)

_MIN_BODY = 200


def _fetch(url):
    try:
        r = get_session().get(url, headers={"User-Agent": get_user_agent()},
                              timeout=8, allow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200 or len(r.text) < _MIN_BODY:
        return None
    return url


async def probe_paths(domain, limiter):
    out, seen = [], set()
    base = f"https://{domain}"
    for path in PATHS:
        url = base + path
        got = await gated_sync(limiter, _fetch, url, domain=domain)

        if not got:
            continue
        c = canonicalize_url(got)
        if c and c not in seen:
            seen.add(c)
            out.append(got)
    return out