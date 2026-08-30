"""Small, dependency-free networking/domain helpers shared across the package."""
import asyncio as aio
import random
import re
import time
from functools import lru_cache
from urllib.parse import unquote as uq
from urllib.parse import urlparse as up

from curl_cffi import requests as rq

from .config import USER_AGENTS

# Third-party disclosure/bounty platforms: a company's real program often lives here, not
# on their own domain, so a plain host_matches() would always miss e.g. hackerone.com/interos.
# But accepting ANY hit on these hosts would just as easily match an unrelated program or a
# news article mentioning the platform -- so a hit here only counts if the target's own name
# also shows up in the URL/title/snippet, not just the platform domain.
BOUNTY_PLATFORMS = {
    "hackerone.com", "bugcrowd.com", "intigriti.com", "yeswehack.com",
    "hackenproof.com", "huntr.com", "cobalt.io", "synack.com", "safehats.com",
    "openbugbounty.org",
}


@lru_cache(maxsize=1)  # fetches once per process, replaces hand-rolled flag+global caching
def _remote_user_agents():
    try:
        # scrapeops' free endpoint 401s without ?api_key=...; this silently returns [] and
        # get_user_agent() falls back to USER_AGENTS every run unless you add a key.
        r = rq.get("https://headers.scrapeops.io/v1/user-agents", timeout=3)
        return r.json().get("result", []) if r.status_code == 200 else []
    except Exception:
        return []


def get_user_agent():
    return random.choice(_remote_user_agents() or USER_AGENTS)


def normalize_domain(d):
    """Strip scheme/path/www. from a URL or bare domain, return lowercase host or None."""
    if not d:
        return None
    p = up(d if "://" in d else "https://" + d).netloc.lower().strip()
    return p[4:] if p.startswith("www.") else p


def host_matches(domain, url):
    """True if url's host is exactly `domain` or a subdomain of it (not a substring match)."""
    if not domain or not url:
        return False
    h = normalize_domain(url)
    return bool(h) and (h == domain or h.endswith("." + domain))


def is_relevant_result(domain, url, text=""):
    """True if `url` is the target's own site, OR a known bounty-platform page that actually
    names the target company. `text` should be the result's title+snippet -- used only for the
    platform case, so a random news mention of "bug bounty" on an unrelated host never matches
    (that host isn't in BOUNTY_PLATFORMS at all), and a platform hit for a *different* company's
    program doesn't either (the slug check below requires the target's own name to be present)."""
    if host_matches(domain, url):
        return True
    host = normalize_domain(url)
    slug = domain.split(".")[0] if domain else None
    if not host or host not in BOUNTY_PLATFORMS or not slug:
        return False
    return bool(re.search(rf'\b{re.escape(slug)}\b', f"{url} {text}".lower()))


def resolve_ddg_redirect(u):
    """Unwrap DuckDuckGo's `uddg=` redirect param to the real target URL, if present."""
    if not u:
        return None
    u = f"https:{u}" if u.startswith("//") else u
    p = up(u)
    if "duckduckgo.com" in p.netloc and "uddg" in p.query:
        for pt in p.query.split("&"):
            if pt.startswith("uddg="):
                return uq(pt[5:])
    return u


class GlobalRateLimiter:
    """Token-bucket-style pacing: schedules a fixed interval between requests instead of just
    capping concurrency (a Semaphore bounds how many requests run at once, not how fast they
    fire — 2 concurrent slots at 0.1s latency is still ~20 req/s). Shared across every caller
    that holds a reference, so N concurrent domains still funnel through one paced queue.

    Lock is only held to reserve a time SLOT, not for the sleep itself — so tasks release the
    lock immediately and sleep in parallel toward their own target time, rather than serializing
    on the lock and drifting later than necessary."""
    def __init__(self, requests_per_second):
        self.interval = 1.0 / requests_per_second
        self._last_scheduled_time = 0.0
        self._lock = aio.Lock()

    async def wait_for_token(self):
        async with self._lock:
            now = time.monotonic()
            if self._last_scheduled_time < now:
                self._last_scheduled_time = now
            target_time = self._last_scheduled_time
            self._last_scheduled_time = target_time + self.interval

        sleep_time = target_time - time.monotonic()
        if sleep_time > 0:
            await aio.sleep(sleep_time)


class RateLimited(RuntimeError):
    """Raised by a provider on HTTP 429. Distinct from other errors: retrying the same
    request immediately against an endpoint that just rate-limited you is pointless — the
    fix is to slow down future requests, which the shared AdaptiveLimiter already does."""
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def retry(fn, tries=3, base=1.5):
    """Run fn() with exponential backoff. Used for the scraped/undocumented competitor APIs.
    RateLimited is not retried locally — see RateLimited docstring."""
    for a in range(tries):
        try:
            return fn()
        except RateLimited:
            raise
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(base * (2 ** a))


async def retry_async(fn, tries=3, base=1.0):
    """Async counterpart to retry(): run async fn() with exponential backoff."""
    for a in range(tries):
        try:
            return await fn()
        except RateLimited:
            raise
        except Exception:
            if a == tries - 1:
                raise
            await aio.sleep(base * (2 ** a))