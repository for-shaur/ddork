"""Small, dependency-free networking/domain helpers shared across the package."""
import asyncio as aio
import random
import time
from functools import lru_cache
from threading import local
from urllib.parse import parse_qsl, urlencode, urlparse as up, urlunparse
import tldextract
from curl_cffi import requests as rq

from .config import USER_AGENTS, IMPERSONATE


@lru_cache(maxsize=1)
def _remote_user_agents():
    try:
        r = rq.get("https://headers.scrapeops.io/v1/user-agents", timeout=3)
        return r.json().get("result", []) if r.status_code == 200 else []
    except Exception:
        return []


def get_user_agent():
    return random.choice(_remote_user_agents() or USER_AGENTS)


# --- Shared per-thread session -------------------------------------------
_thread_local = local()


def get_session():
    """Return a curl_cffi session for the current thread (cached per-thread)."""
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = rq.Session(impersonate=IMPERSONATE)
        _thread_local.session = s
    return s


def normalize_domain(d):
    if not d:
        return None
    p = up(d if "://" in d else "https://" + d).netloc.lower().strip()
    return p[4:] if p.startswith("www.") else p


def resolve_ddg_redirect(u):
    if not u:
        return None
    u = f"https:{u}" if u.startswith("//") else u
    p = up(u)
    if "duckduckgo.com" in p.netloc and "uddg" in p.query:
        for pt in p.query.split("&"):
            if pt.startswith("uddg="):
                from urllib.parse import unquote as uq
                return uq(pt[5:])
    return u


def to_registrable_domain(hostname):
    if not hostname:
        return None
    ext = tldextract.extract(hostname)
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"


# --- URL canonicalization ------------------------------------------------

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "_ga",
}


def canonicalize_url(url):
    """Normalize a URL for dedup."""
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    try:
        p = up(url)
    except Exception:
        return None
    if not p.netloc:
        return None
    scheme = (p.scheme or "https").lower()
    host = p.netloc.lower()
    if scheme == "http" and host.endswith(":80"):
        host = host[:-3]
    elif scheme == "https" and host.endswith(":443"):
        host = host[:-4]
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    pairs = sorted(
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    )
    query = urlencode(pairs)
    return urlunparse((scheme, host, path, "", query, ""))


# --- Per-Domain Token Bucket Rate Limiter ---------------------------------

class DomainRateLimiter:
    """Fair per-domain rate limiting using token buckets.
    
    Replaces global AIMD: one slow/dead domain no longer throttles all others.
    Each domain gets its own bucket (default: 2 req/s, burst 5).
    """
    def __init__(self, rate=2.0, capacity=5):
        self.rate = rate
        self.capacity = capacity
        self._buckets = {}
        self._lock = aio.Lock()
    
    async def acquire(self, domain: str):
        """Acquire a token for the given domain, waiting if necessary."""
        async with self._lock:
            if domain not in self._buckets:
                self._buckets[domain] = {
                    "tokens": self.capacity,
                    "last": time.monotonic()
                }
            bucket = self._buckets[domain]
        
        now = time.monotonic()
        elapsed = now - bucket["last"]
        bucket["tokens"] = min(self.capacity, bucket["tokens"] + elapsed * self.rate)
        bucket["last"] = now
        
        if bucket["tokens"] < 1:
            sleep_time = (1 - bucket["tokens"]) / self.rate
            await aio.sleep(sleep_time)
            bucket["tokens"] = 0
        else:
            bucket["tokens"] -= 1


# Global instance used by analyzer
domain_limiter = DomainRateLimiter(rate=2.0, capacity=5)


async def gated_sync(limiter, fn, *args, **kwargs):
    """Run sync fn(*args) in a thread under the per-domain limiter.
    
    Args:
        limiter: DomainRateLimiter instance (or None to skip limiting)
        fn: Sync function to run
        *args: Positional args for fn
        **kwargs: Must include 'domain' key for rate limiting
    
    Returns the fn result (or None on failure). Never raises.
    """
    domain = kwargs.pop('domain', None)
    if limiter and domain:
        await limiter.acquire(domain)
    try:
        result = await aio.to_thread(fn, *args)
        return result
    except Exception:
        return None


class RateLimited(RuntimeError):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def retry(fn, tries=3, base=1.5):
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
    for a in range(tries):
        try:
            return await fn()
        except RateLimited:
            raise
        except Exception:
            if a == tries - 1:
                raise
            await aio.sleep(base * (2 ** a))


class GlobalRateLimiter:
    """Kept for ddg.py / exa.py which need per-backend pacing."""
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