"""Per-domain discovery + isBounty classification, batched across domains.

v3.3 changes:
  - Replaced global AIMD limiter with per-domain token buckets (DomainRateLimiter).
    One slow/dead domain no longer throttles the entire scan (fixes "reqs 1/8" trap).
  - Candidate URLs within a domain are fetched/classified concurrently (asyncio.gather).
  - security.txt, sitemap, and path probing run concurrently per domain.
  - _classify runs in a thread via aio.to_thread so CPU-heavy isbounty doesn't stall loop.
"""
import asyncio as aio
import time

from .config import log
from .net import canonicalize_url, gated_sync, domain_limiter
from .progress import eta_str, rate_per_min
from .scrape import fetch_and_clean
from .sources.security_txt import check_security_txt
from .sources.sitemap import fetch_sitemap
from .sources.paths import probe_paths
from .sources.ddg import search_ddg
from .sources.exa import search_exa

from isbounty import Pipeline, PageContent
from isbounty.core.text_utils import split_sentences

_pipeline = Pipeline()


async def _classify(url, raw_text, domain, security_txt=None, headings=None):
    """Run the isbounty pipeline in a thread so it doesn't block the event loop."""
    if not raw_text:
        return None
    try:
        def _run():
            page = PageContent(
                url=url,
                raw_text=raw_text,
                sentences=split_sentences(raw_text),
                headings=headings or [],
                domain=domain,
                security_txt=security_txt,
            )
            return _pipeline.run_from_page(page)

        return await aio.to_thread(_run)
    except Exception as e:
        log.error(f"[CLS] {url} err: {e}")
        return None


def _finding(domain, url, source, result):
    return {
        "domain": domain,
        "url": url,
        "source": source,
        "label": getattr(result, "label", "NOT_PROGRAM"),
        "confidence": float(getattr(result, "confidence", 0.0)),
        "decision_path": getattr(result, "decision_path", "") or "",
        "reasons": list(getattr(result, "reasons", []) or []),
    }


MAX_URLS_PER_DOMAIN = 5


async def analyze_domain(domain, semaphore, limiter, tick=None):
    async with semaphore:
        log.info(f"[ANZ] start {domain}")

        # Run the three independent discovery sources concurrently.
        sec_task = aio.create_task(check_security_txt(domain, limiter))
        sm_task = aio.create_task(fetch_sitemap(domain, limiter))
        paths_task = aio.create_task(probe_paths(domain, limiter))

        sec, sm, paths = await aio.gather(sec_task, sm_task, paths_task)
        if tick:
            tick()
            tick()
            tick()

        sec_body = sec.get("body") if sec.get("found") else None

        candidates = []
        if sec.get("policy_url"):
            candidates.append((sec["policy_url"], "sec.txt"))
        candidates.extend((u, "sitemap") for u in sm)
        candidates.extend((u, "paths") for u in paths)

        if not candidates:
            ddg_blocked = False
            try:
                ddg = await search_ddg(domain)
                if ddg["status"] == "ok":
                    candidates.extend((r["url"], "ddg") for r in ddg["results"])
                elif ddg["status"] == "blocked":
                    ddg_blocked = True
            except Exception as e:
                log.warning(f"[DDG] {domain} err: {e}")
            if tick:
                tick()

            if ddg_blocked:
                try:
                    exa = await search_exa(domain)
                    candidates.extend((r["url"], "exa") for r in (exa or []))
                except Exception as e:
                    log.warning(f"[EXA] {domain} err: {e}")
                if tick:
                    tick()

        # Canonicalize for dedup
        seen_canon = set()
        unique = []
        for url, src in candidates:
            c = canonicalize_url(url)
            if c and c not in seen_canon:
                seen_canon.add(c)
                unique.append((url, src))
        unique = unique[:MAX_URLS_PER_DOMAIN]

        # Process all candidate URLs concurrently (per-domain rate limit still applies)
        async def process_url(url, source):
            text, headings = await gated_sync(limiter, fetch_and_clean, url, domain=domain)
            if tick:
                tick()
            if not text:
                return None
            res = await _classify(url, text, domain,
                                  security_txt=sec_body, headings=headings)
            if res:
                return _finding(domain, url, source, res)
            return None

        # Run all URL probes for this domain concurrently
        results = await aio.gather(*[process_url(u, s) for u, s in unique])
        findings = [r for r in results if r is not None]

        log.info(f"[ANZ] {domain} FIN: {len(findings)} findings (candidates: {len(unique)})")
        return {"domain": domain, "findings": findings}


async def run_workflow(domains, workers=8, limiter=None, ui=None):
    if limiter is None:
        limiter = domain_limiter  # Use global per-domain limiter
    sem = aio.Semaphore(max(1, workers))
    total = len(domains)
    results = []
    start = time.monotonic()
    req_count = 0

    def tick():
        nonlocal req_count
        req_count += 1
        if ui:
            n = len(results)
            ui.status(
                f"probing  {n}/{total} domains · {req_count} units "
                f"· {rate_per_min(start, n):.0f}/min "
                f"· ETA {eta_str(start, n, total)}"
            )

    tasks = [aio.create_task(analyze_domain(d, sem, limiter, tick)) for d in domains]
    if ui:
        ui.status(f"probing  0/{total} domains")
    for t in aio.as_completed(tasks):
        results.append(await t)
        tick()
    if ui:
        resolved = sum(1 for r in results if r["findings"])
        ui.checkpoint(
            f"probing  {total}/{total} domains → {resolved} with findings, "
            f"{total - resolved} empty"
        )
    return results