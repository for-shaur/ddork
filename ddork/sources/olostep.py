"""Olostep search source. Returns list of {url,title,snippet} or [].

Runs concurrently with Exa to share the search load. Both engines query
in parallel in analyzer.py and their results are merged + deduped before
classification.

Results are filtered through the same is_relevant_result predicate used by
all other search sources, so an Olostep hit means the same thing as an Exa hit.
"""
import asyncio as aio
import time

from ..config import log
from ..net import (
    GlobalRateLimiter,
    RateLimited,
    get_session,
    get_user_agent,
    is_relevant_result,
)

_URL = "https://api.olostep.com/playground/v1/searches"
_limiter = GlobalRateLimiter(requests_per_second=2)
_cooldown_until = 0.0


def _parse_response(data):
    """Extract a flat list of {url, title, snippet} from the decoded JSON.

    Expected shape: {"result": {"links": [{url, title, description, ...}, ...]}}
    """
    results = []
    if not isinstance(data, dict):
        return results
    links = (data.get("result") or {}).get("links") or []
    if not isinstance(links, list):
        return results
    for item in links:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("link") or ""
        if not url or not isinstance(url, str):
            continue
        title = item.get("title") or item.get("name") or ""
        snippet = item.get("description") or item.get("snippet") or ""
        results.append({
            "url": url,
            "title": str(title),
            "snippet": str(snippet),
        })
    return results


async def search_olostep(domain, max_retries=3, initial_wait=2):
    """Search Olostep for `site:{domain} "bug bounty"`.

    Runs in parallel with search_exa() inside analyze_domain(). Returns a
    (possibly empty) list of relevant {url, title, snippet} dicts.
    Never raises — callers can treat a [] return as "no results".
    """
    global _cooldown_until
    wait = initial_wait
    query = f'site:{domain} "bug bounty"'

    for attempt in range(max_retries):
        remaining = _cooldown_until - time.monotonic()
        if remaining > 0:
            await aio.sleep(remaining)
        try:
            await _limiter.wait_for_token()
            r = await aio.to_thread(
                get_session().post,
                _URL,
                json={"query": query},
                headers={
                    "content-type": "application/json",
                    "origin": "https://www.olostep.com",
                    "referer": "https://www.olostep.com/",
                    "user-agent": get_user_agent(),
                },
                timeout=30,
            )
            if r.status_code == 429:
                _cooldown_until = time.monotonic() + wait
                raise RateLimited(f"olostep 429 for {domain}", retry_after=wait)
            if r.status_code != 200:
                log.warning(f"[OLOSTEP] {domain}: HTTP {r.status_code}")
                return []
            try:
                data = r.json()
            except Exception:
                log.error(f"[OLOSTEP] {domain}: invalid JSON response")
                return []
            hits = _parse_response(data)
            relevant = [
                h for h in hits
                if is_relevant_result(
                    domain, h["url"], f"{h['title']} {h['snippet']}"
                )
            ]
            if hits and not relevant:
                log.info(
                    f"[OLOSTEP] {domain}: {len(hits)} raw hits, "
                    f"0 passed relevance filter"
                )
            return relevant
        except RateLimited:
            wait = min(wait * 2, 20)
        except Exception as e:
            log.error(f"[OLOSTEP] {domain} err: {e}")
            return []
    return []