"""Fetch sitemap.xml (one level of sitemap index), return top-N security/bounty URLs.

Ranking matters: a domain's sitemap routinely contains a dozen URLs containing
"security" or "report". Dumping all of them into the classifier produces pages of
NOT_PROGRAM noise. Instead rank by path-keyword specificity and cap to TOP_N.

v3.1: uses the shared per-thread session from net.get_session().
"""
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse as up

from ..config import log
from ..net import get_session, gated_sync, get_user_agent

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# Path fragments that strongly indicate a program page, weighted.
_KEYWORD_WEIGHTS = (
    ("bug-bounty", 10), ("bugbounty", 10), ("bug_bounty", 10),
    ("responsible-disclosure", 9), ("vulnerability-disclosure", 9),
    ("vulnerability", 6), ("disclosure", 6),
    ("security.txt", 5), ("security-policy", 5), ("security/report", 5),
    ("security", 3), ("bounty", 7), ("report", 1),
)

# How many sitemap URLs to return per domain. Top-ranked only.
TOP_N = 2


def _score(url):
    path = (up(url).path or "").lower()
    return sum(w for kw, w in _KEYWORD_WEIGHTS if kw in path)


def _fetch(u):
    try:
        r = get_session().get(u, headers={"User-Agent": get_user_agent()}, timeout=10)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None


def _parse(text):
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return [], []
    sitemaps = [e.text for e in root.findall(".//sm:sitemap/sm:loc", _NS) if e.text]
    urls = [e.text for e in root.findall(".//sm:url/sm:loc", _NS) if e.text]
    return sitemaps, urls


async def fetch_sitemap(domain, limiter, max_children=2):
    base = f"https://{domain}"
    urls, children = [], []

    body = await gated_sync(limiter, _fetch, urljoin(base, "/sitemap.xml"), domain=domain)
    if body:
        sitemaps, page_urls = _parse(body)
        urls.extend(page_urls)
        children.extend(sitemaps)

    for child in children[:max_children]:
        body = await gated_sync(limiter, _fetch, child, domain=domain)
        if body:
            _, page_urls = _parse(body)
            urls.extend(page_urls)

    seen = set()
    scored = []
    for u in urls:
        s = _score(u)
        if s > 0 and u not in seen:
            seen.add(u)
            scored.append((s, u))
    scored.sort(key=lambda x: -x[0])
    return [u for _, u in scored[:TOP_N]]