"""SearXNG (mectov instance) search — primary search source; ddg/exa are fallbacks."""
import asyncio as aio
from urllib.parse import urlparse

from bs4 import BeautifulSoup as bs

from ..config import log
from ..net import GlobalRateLimiter, get_session, is_relevant_result

BLOCK_WAITS = (5, 10, 18)

_limiter = GlobalRateLimiter(requests_per_second=2)

SEARX_URL = "https://search.mectov.my.id/search"
SEARX_HOST = urlparse(SEARX_URL).netloc
HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
}

_SKIP_HREF_SUBSTRINGS = ("/search?", "/preferences", "/about", "/stats", "github.com/searxng")


def _parse(html, host=SEARX_HOST):
    results, seen_urls = [], set()
    for a in bs(html, "html.parser").find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if host in href or any(s in href for s in _SKIP_HREF_SUBSTRINGS):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        title = a.get_text(strip=True)
        snippet = ""
        for ancestor in filter(None, (a.find_parent(), a.find_parent().find_parent() if a.find_parent() else None)):
            text = ancestor.get_text(" ", strip=True)
            if len(text) > len(title) + 20:
                snippet = text
                break

        results.append({"url": href, "title": title, "snippet": snippet[:400]})
    return results


async def search_searx(domain, timeout=10, block_waits=BLOCK_WAITS):
    params = {
        "q": f'site:{domain} ("bug bounty" OR "responsible disclosure")',
        "category_general": 1,
        "language": "en-US",
        "time_range": "",
        "safesearch": 0,
        "theme": "simple",
    }
    try:
        log.info(f"[SEARX] {domain} search")

        r, last_err = None, None
        for attempt, wait in enumerate((0,) + block_waits):
            if wait:
                log.info(f"[SEARX] {domain} blocked ({last_err}), waiting {wait}s before retry {attempt}/{len(block_waits)}")
                await aio.sleep(wait)
            try:
                await _limiter.wait_for_token()
                r = await aio.to_thread(get_session().get, SEARX_URL, params=params, headers=HEADERS, timeout=timeout)
                if r.status_code != 200:
                    raise RuntimeError(f"searx status {r.status_code}")
            except Exception as e:
                last_err, r = e, None
                continue
            break

        if r is None:
            log.warning(f"[SEARX] {domain} still failing ({last_err}) after {len(block_waits)} retries, giving up on searx")
            return None

        log.info(f"[SEARX] {domain} stat: {r.status_code} len: {len(r.text)}")

        parsed = _parse(r.text)
        log.info(f"[SEARX] {domain} candidates: {len(parsed)}")
        if not parsed:
            log.warning(f"[SEARX] {domain} 0 links extracted, possible parser mismatch. body[:300]: {r.text[:300]!r}")

        for res in parsed:
            if is_relevant_result(domain, res["url"], f"{res['title']} {res['snippet']}"):
                log.info(f"[SEARX] {domain} found: {res['url']}")
                return {"url": res["url"], "source": "searx", "snippet": res["snippet"], "title": res["title"]}

        log.info(f"[SEARX] {domain} {len(parsed)} candidates, none matched host")
        return None
    except Exception as e:
        log.error(f"[SEARX] {domain} err: {e}")
        return None