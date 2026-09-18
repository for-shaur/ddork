"""Per-domain discovery + isBounty classification, batched across domains.

Pipeline per domain:
  1. security.txt — deterministic, runs first; if it advertises a fetchable
     policy URL, that URL is the only candidate.
  2. Otherwise Exa.  "ok"/"empty" → candidates (or none) from the search.
  3. Otherwise Exa fallback is the only remaining search source.

Candidates are canonicalized (www-stripped) and deduped before fetching.
"""
import asyncio as aio
import time

from .config import log
from .net import canonicalize_url, gated_sync, domain_limiter
from .progress import fmt_secs
from .scrape import fetch_and_clean
from .sources.security_txt import check_security_txt
#from .sources.ddg import search_ddg
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


async def analyze_domain(domain, semaphore, limiter, ui=None):
    async with semaphore:
        log.info(f"[ANZ] start {domain}")

        sec = await check_security_txt(domain, limiter)
        sec_body = sec.get("body") if sec.get("found") else None

        candidates = []
        if sec.get("policy_url"):
            candidates.append((sec["policy_url"], "sec.txt"))
        else:
            try:
                exa = await search_exa(domain)
                candidates.extend((r["url"], "exa") for r in (exa or []))
            except Exception as e:
                log.warning(f"[EXA] {domain} err: {e}")

        seen_canon = set()
        unique = []
        for url, src in candidates:
            c = canonicalize_url(url)
            if c and c not in seen_canon:
                seen_canon.add(c)
                unique.append((url, src))
        unique = unique[:MAX_URLS_PER_DOMAIN]

        async def process_url(url, source):
            text, headings = await gated_sync(
                limiter, fetch_and_clean, url, domain=domain
            )
            if not text:
                return None
            res = await _classify(url, text, domain,
                                  security_txt=sec_body, headings=headings)
            return _finding(domain, url, source, res) if res else None

        results = await aio.gather(*[process_url(u, s) for u, s in unique])
        findings = [r for r in results if r is not None]

        log.info(f"[ANZ] {domain} FIN: {len(findings)} findings "
                 f"(candidates: {len(unique)})")
        if ui:
            ui.v(2, f"{domain}: {len(findings)} findings "
                    f"from {len(unique)} candidate(s)")
        return {"domain": domain, "findings": findings}


async def run_workflow(domains, workers=8, limiter=None, ui=None):
    if limiter is None:
        limiter = domain_limiter
    sem = aio.Semaphore(max(1, workers))
    total = len(domains)
    results = []
    start = time.monotonic()

    if ui:
        ui.phase("probing", total)

    tasks = [aio.create_task(analyze_domain(d, sem, limiter, ui)) for d in domains]
    for t in aio.as_completed(tasks):
        results.append(await t)
        if ui:
            ui.advance()

    if ui:
        resolved = sum(1 for r in results if r["findings"])
        ui.checkpoint(
            f"probing complete: {total} domain(s) in "
            f"{fmt_secs(time.monotonic() - start)} — "
            f"{resolved} with findings, {total - resolved} empty"
        )
    return results