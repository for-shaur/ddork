"""Exa (hermes.exa.ai) search fallback for finding a domain's bug-bounty page.

Exa's frontend endpoint returns a SvelteKit `__data.json` stream where objects
often reference other array slots by index instead of embedding values inline.
`_resolve_ref` / `parse_exa_response` reconstruct plain dicts out of that.
"""
import asyncio as aio
import json
import time

from curl_cffi import requests as rq

from ..config import IMPERSONATE, log
from ..net import GlobalRateLimiter, RateLimited, get_user_agent, is_relevant_result

# reuse the same paced-queue limiter ddg.py uses — a per-domain retry loop alone
# doesn't stop N concurrent domains from all firing at Exa in the same instant.
_limiter = GlobalRateLimiter(requests_per_second=2)

# Single shared clock (there's only one Exa endpoint, unlike ddg's per-backend dict): once
# any domain gets a 429, every other concurrent domain waits out the same cooldown instead
# of each independently re-triggering its own 429 in the same window.
_cooldown_until = 0.0


def _resolve_ref(value, array):
    """Resolve a SvelteKit data-stream reference: an int index into `array`, recursively."""
    if isinstance(value, int) and 0 <= value < len(array):
        return array[value]
    if isinstance(value, list):
        return [_resolve_ref(x, array) for x in value]
    return value


def parse_exa_response(text):
    """Parse every line of the streamed response, not just the first `type: data` line."""
    results = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception as e:
            log.debug(f"Exa parse err: {e}")
            continue
        if payload.get("type") != "data":
            continue
        for node in payload.get("nodes", []):
            if node.get("type") != "data":
                continue
            arr = node.get("data", [])
            if not arr or not isinstance(arr, list):
                continue
            meta = arr[0] if arr else {}
            if not isinstance(meta, dict):
                continue
            results_idx = meta.get("results")
            if results_idx is None or results_idx >= len(arr):
                continue
            result_indices = arr[results_idx]
            if not isinstance(result_indices, list):
                continue
            for r_ix in result_indices:
                if not isinstance(r_ix, int) or r_ix >= len(arr):
                    continue
                o = arr[r_ix]
                if not isinstance(o, dict):
                    continue
                title, url, highlights, domain = (_resolve_ref(o.get(k), arr) for k in ("title", "url", "highlights", "domain"))
                if isinstance(highlights, list):
                    highlights = " ".join(str(h) for h in highlights if h)
                if url and isinstance(url, str):
                    results.append({
                        "title": str(title) if title else "",
                        "url": url,
                        "snippet": str(highlights) if highlights else "",
                        "domain": str(domain) if domain else "",
                    })
    return results


async def search_exa(domain, max_retries=3, initial_wait=2):
    global _cooldown_until
    wait = initial_wait
    for attempt in range(max_retries):
        remaining = _cooldown_until - time.monotonic()
        if remaining > 0:
            log.info(f"[EXA] {domain} shared cooldown ({remaining:.0f}s left, set by another domain), waiting")
            await aio.sleep(remaining)
        try:
            await _limiter.wait_for_token()
            log.info(f"[EXA] {domain} search (try {attempt + 1})")
            r = await aio.to_thread(
                rq.get,
                "https://hermes.exa.ai/search/__data.json",
                params={"q": f'{domain} "bug bounty"', "type": "fast", "x-sveltekit-invalidated": "01"},
                impersonate=IMPERSONATE,
                headers={"referer": "https://hermes.exa.ai/", "user-agent": get_user_agent()},
                timeout=10,
            )
            log.info(f"[EXA] {domain} stat: {r.status_code}")
            if r.status_code == 429:
                _cooldown_until = time.monotonic() + wait
                raise RateLimited(f"exa 429 for {domain}", retry_after=wait)
            if r.status_code != 200:
                log.warning(f"[EXA] {domain} non-200: {r.status_code}")
                return None
            log.info(f"[EXA] {domain} raw length: {len(r.text)}")
            parsed = parse_exa_response(r.text)
            log.info(f"[EXA] {domain} parsed {len(parsed)}")
            for p in parsed:
                if is_relevant_result(domain, p.get("url", ""), f"{p.get('title', '')} {p.get('snippet', '')}"):
                    log.info(f"[EXA] {domain} found: {p['url']}")
                    return {"url": p["url"], "source": "exa", "snippet": p.get("snippet", ""), "title": p.get("title", "")}
            log.info(f"[EXA] {domain} no match")
            return None
        except RateLimited as e:
            log.warning(f"[EXA] {e}")
            wait = min(wait * 2, 20)
        except Exception as e:
            log.error(f"[EXA] {domain} err: {e}")
            return None
    return None