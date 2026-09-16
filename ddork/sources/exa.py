"""Exa (hermes.exa.ai) search fallback. Returns list of {url,title,snippet} or []."""
import asyncio as aio
import json
import time

from ..config import log
from ..net import GlobalRateLimiter, RateLimited, get_session, get_user_agent

_limiter = GlobalRateLimiter(requests_per_second=2)
_cooldown_until = 0.0


def _resolve_ref(value, array):
    if isinstance(value, int) and 0 <= value < len(array):
        return array[value]
    if isinstance(value, list):
        return [_resolve_ref(x, array) for x in value]
    return value


def parse_exa_response(text):
    results = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
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
                title, url, highlights, domain = (
                    _resolve_ref(o.get(k), arr) for k in ("title", "url", "highlights", "domain")
                )
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
            await aio.sleep(remaining)
        try:
            await _limiter.wait_for_token()
            r = await aio.to_thread(
                get_session().get,
                "https://hermes.exa.ai/search/__data.json",
                params={"q": f'site:{domain} "bug bounty"',
                        "type": "fast", "x-sveltekit-invalidated": "01"},
                headers={"referer": "https://hermes.exa.ai/",
                         "user-agent": get_user_agent()},
                timeout=10,
            )
            if r.status_code == 429:
                _cooldown_until = time.monotonic() + wait
                raise RateLimited(f"exa 429 for {domain}", retry_after=wait)
            if r.status_code != 200:
                return []
            return parse_exa_response(r.text)
        except RateLimited:
            wait = min(wait * 2, 20)
        except Exception as e:
            log.error(f"[EXA] {domain} err: {e}")
            return []
    return []