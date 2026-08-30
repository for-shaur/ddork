"""Orchestrates the discovery sources + classifier into a per-domain result, and runs a batch."""
import asyncio as aio

from .classify import classify_bounty
from .config import log
from .scrape import fetch_text
from .sources.ddg import search_ddg
from .sources.exa import search_exa
from .sources.security_txt import check_security_txt


async def analyze_domain(domain, semaphore):
    async with semaphore:
        log.info(f"[ANZ] Start {domain}")

        sec, hit = await aio.gather(check_security_txt(domain), search_ddg(domain))
        if not hit:
            hit = await search_exa(domain)

        url, source = sec["policy_url"], "sec.txt"
        # security.txt body is also a classification fallback — a site with a security.txt
        # but no explicit Policy: URL used to get zero signal at all
        snippet, title = sec.get("snippet"), None
        if not url and hit:
            url, source, snippet, title = hit["url"], hit["source"], hit.get("snippet", ""), hit.get("title", "")

        log.info(f"[ANZ] {domain} src: {source} url: {url}")

        page_text = await aio.to_thread(fetch_text, url) if url else None
        bounty = classify_bounty(page_text) if page_text else None

        if not bounty or bounty["confidence"] == "low":
            snippet_bounty = classify_bounty(snippet) if snippet else None
            if snippet_bounty and snippet_bounty["confidence"] != "low":
                bounty = snippet_bounty
                bounty["source_note"] = "snippet_fallback"

        if not bounty:
            bounty = {"offers_money": None, "confidence": "low", "evidence": "none", "evidence_quote": None}

        log.info(f"[ANZ] {domain} FIN: money={bounty.get('offers_money')} conf={bounty.get('confidence')}")
        return {
            "domain": domain,
            "sec_txt": sec,
            "source": source,
            "url": url,
            "title": title,
            "page": page_text[:600] if page_text else None,
            "bounty": bounty,
        }


async def run_workflow(domains, workers=8, ui=None):
    semaphore = aio.Semaphore(max(1, workers))  # actually driven by -w now
    tasks = [aio.create_task(analyze_domain(d, semaphore)) for d in domains]
    total = len(domains)
    results = []
    if ui:
        ui.status(f"probing  0/{total} pages")
    for t in aio.as_completed(tasks):
        results.append(await t)
        if ui:
            ui.status(f"probing  {len(results)}/{total} pages")
    if ui:
        resolved = sum(1 for r in results if r["url"])
        ui.checkpoint(f"probing  {total}/{total} pages → {resolved} resolved, {total - resolved} unresolved")
    return results