"""DuckDuckGo search for a domain's bug-bounty/disclosure page.

Returns {"status": "ok"|"empty"|"blocked", "results": [{url,title,snippet}]}.
For "ok", `results` has exactly one entry — the top-ranked matching URL. Returning
every match was the source of duplicate findings (multiple URLs on the same domain
all classified independently).

v3.1: the global lock is now per-backend rather than process-wide, so a slow
backend doesn't block searches that would use a different backend.
"""
import asyncio as aio
import time

from ..config import log
from ..net import GlobalRateLimiter, canonicalize_url, resolve_ddg_redirect

try:
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

try:
    from ddgs.exceptions import DDGSException, RatelimitException
except ImportError:
    class DDGSException(Exception):
        pass

    class RatelimitException(DDGSException):
        pass

BACKENDS = ["google", "bing", "brave", "yandex", "yahoo", "mojeek", "duckduckgo"]
RETRY_WAITS = (3, 6)
MAX_BACKENDS_TRIED = 3

QUERY = 'site:{domain} ("bug bounty" OR "responsible disclosure")'

_backend_limiters = {b: GlobalRateLimiter(requests_per_second=1) for b in BACKENDS}
_backend_cooldown_until = {b: 0.0 for b in BACKENDS}
# Per-backend locks instead of one global lock: a cooldown on google doesn't
# block a search that could use bing.
_backend_locks = {b: aio.Lock() for b in BACKENDS}


def _run_backend(domain, backend):
    with DDGS() as dg:
        return list(dg.text(QUERY.format(domain=domain), max_results=8, backend=backend))


async def _query_backend(domain, backend):
    remaining = _backend_cooldown_until[backend] - time.monotonic()
    if remaining > 0:
        return None

    async with _backend_locks[backend]:
        for attempt, wait in enumerate((*RETRY_WAITS, None)):
            try:
                await _backend_limiters[backend].wait_for_token()
                results = await aio.to_thread(_run_backend, domain, backend)
                return results or []
            except (RatelimitException, DDGSException) as e:
                if wait is None:
                    _backend_cooldown_until[backend] = time.monotonic() + 15
                    return None
                _backend_cooldown_until[backend] = time.monotonic() + wait
                await aio.sleep(wait)
            except Exception:
                return None
    return None


async def search_ddg(domain):
    """Returns {"status": ..., "results": [...]}. On "ok", results has 0 or 1 entries."""
    if not DDG_AVAILABLE:
        log.info(f"[DDG] {domain}: ddgs not installed")
        return {"status": "blocked", "results": []}

    backends_tried = 0
    any_clean = False

    for backend in BACKENDS:
        if backends_tried >= MAX_BACKENDS_TRIED:
            break
        results = await _query_backend(domain, backend)
        backends_tried += 1
        if results is None:
            continue
        any_clean = True
        for r in results:
            u = resolve_ddg_redirect(r.get("href", ""))
            if not u or "wikipedia.org" in u:
                continue
            c = canonicalize_url(u)
            if not c:
                continue
            log.info(f"[DDG] {domain} found: {u}")
            return {
                "status": "ok",
                "results": [{
                    "url": u,
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                }],
            }

    if any_clean:
        return {"status": "empty", "results": []}
    return {"status": "blocked", "results": []}