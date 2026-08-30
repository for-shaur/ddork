"""Fetch and strip a web page down to plain text for classification."""
from bs4 import BeautifulSoup as bs
from curl_cffi import requests as rq

from .config import IMPERSONATE, log
from .net import get_user_agent


def fetch_text(url):
    if not url:
        return None
    try:
        log.info(f"[SCRAPE] {url}")
        r = rq.get(url, headers={"User-Agent": get_user_agent()}, impersonate=IMPERSONATE, timeout=10)
        log.info(f"[SCRAPE] {url} stat: {r.status_code} len: {len(r.text)}")
        if r.status_code != 200:
            log.warning(f"[SCRAPE] {url} non-200, skipping classification")
            return None
        s = bs(r.text, "html.parser")
        for t in s(["script", "style", "nav", "footer", "header"]):
            t.decompose()
        tx = " ".join(s.stripped_strings)
        log.info(f"[SCRAPE] {url} extracted len: {len(tx)}")
        return tx
    except Exception as e:
        log.error(f"[SCRAPE] {url} err: {e}")
        return None
