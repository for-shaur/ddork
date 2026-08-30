"""DuckDuckGo search fallback for finding a domain's bug-bounty/disclosure page."""
import asyncio as aio
import json
import time

from ..config import log
from ..net import GlobalRateLimiter, is_relevant_result, resolve_ddg_redirect

try:
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

try:
    from ddgs.exceptions import DDGSException, RatelimitException
except ImportError:
    # no bare `except Exception` fallback — define our own so a real bug
    # (TypeError, etc.) doesn't get silently treated as "just a rate limit, retry"
    class DDGSException(Exception):
        pass

    class RatelimitException(DDGSException):
        pass

# backend="auto" (the ddgs default) fans out to whatever it picks, wikipedia first — not
# useful here. An ORDERED LIST, not a comma-joined string: passing a joined string to
# dg.text(backend=...) makes ddgs itself fan out to all of them back-to-back with no delay,
# so one block (brave 429) instantly burns through the rest too and surfaces as a generic
# DDGSException("No results found") with zero backoff. We drive the iteration ourselves
# instead, one backend at a time, so a block on engine N only costs engine N.
BACKENDS = ["google", "bing", "brave", "yandex", "yahoo", "mojeek", "duckduckgo"]

# Fixed backoff on a block: 5s, then 10s, then 18s. Exhaust this on one backend -> move to
# the next backend rather than sit there forever.
RETRY_WAITS = (5, 10, 18)

# -w runs many domains concurrently; a plain Semaphore(2) per backend only bounds how many
# requests are IN FLIGHT at once, not how fast they fire — 2 concurrent slots at 0.1s
# latency is still ~20 req/s, which still trips e.g. brave's rate limit. Pace each backend
# to a fixed rate instead, shared across every domain's search in this process.
_backend_limiters = {b: GlobalRateLimiter(requests_per_second=1) for b in BACKENDS}

# Rate limiting alone doesn't stop this: domain A gets blocked on mojeek and starts its own
# 18s backoff; domain B, running concurrently, has no idea and walks into the same block a
# few requests later, wasting a request and reinforcing the block. This dict is that missing
# shared memory — the first domain to get blocked on a backend marks it hot, so every other
# concurrent domain's _query_backend skips it outright instead of rediscovering the block
# itself. Plain dict, no lock: asyncio is single-threaded/cooperative, and a float assignment
# never straddles an `await`, so there's no torn read between coroutines.
_backend_cooldown_until = {b: 0.0 for b in BACKENDS}


def _run_backend(domain, backend):
    with DDGS() as dg:
        return list(dg.text(f'site:{domain} ("bug bounty" OR "responsible disclosure")', max_results=5, backend=backend))


async def _query_backend(domain, backend):
    """Try one backend, retrying on block per RETRY_WAITS. None = give up on this backend."""
    remaining = _backend_cooldown_until[backend] - time.monotonic()
    if remaining > 0:
        log.info(f"[DDG] {domain} {backend} in shared cooldown ({remaining:.0f}s left, set by another domain), skipping")
        return None

    for attempt, wait in enumerate((*RETRY_WAITS, None)):
        try:
            await _backend_limiters[backend].wait_for_token()
            log.info(f"[DDG] {domain} {backend} (try {attempt + 1})")
            # run in a thread — a bare sync call inside `async def` would block the
            # whole event loop and stall every other in-flight domain
            results = await aio.to_thread(_run_backend, domain, backend)
            log.info(f"[DDG] {domain} {backend} raw: {json.dumps(results)[:1000]}")
            return results
        except (RatelimitException, DDGSException) as e:
            if wait is None:  # backoff schedule exhausted on this backend, move on
                log.info(f"[DDG] {domain} {backend} still blocked after {len(RETRY_WAITS)} retries, skipping it: {e}")
                return None
            _backend_cooldown_until[backend] = time.monotonic() + wait
            log.info(f"[DDG] {domain} {backend} blocked ({e}), shared cooldown {wait}s")
            await aio.sleep(wait)
        except Exception as e:
            log.error(f"[DDG] {domain} {backend} err: {e}")
            return None
    return None


async def search_ddg(domain):
    if not DDG_AVAILABLE:
        log.info(f"[DDG] {domain} uninstalled")
        return None

    for backend in BACKENDS:
        results = await _query_backend(domain, backend)
        for r in results or []:
            u = r.get("href", "")
            if u and "wikipedia.org" not in u and is_relevant_result(domain, u, f"{r.get('title', '')} {r.get('body', '')}"):
                ru = resolve_ddg_redirect(u)
                log.info(f"[DDG] {domain} found: {ru}")
                return {"url": ru, "source": "ddgs", "snippet": r.get("body", ""), "title": r.get("title", "")}

    log.info(f"[DDG] {domain} no match on any backend")
    return None