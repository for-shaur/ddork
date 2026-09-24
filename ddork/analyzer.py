"""Per-domain discovery + isBounty classification, batched across domains.

Pipeline per domain:
  1. security.txt — deterministic, runs first; if it advertises a fetchable
     policy URL, that URL is the only candidate.
  2. Otherwise Exa.  "ok"/"empty" → candidates (or none) from the search.
  3. Otherwise Exa fallback is the only remaining search source.

Candidates are canonicalized (www-stripped) and deduped before fetching.

Classification is loaded lazily through classifier.py, so a missing or
broken isbounty install no longer prevents the module from importing.
"""
import asyncio as aio
import itertools
import time

from .classifier import ClassifierUnavailable, classifier
from .config import log
from .net import canonicalize_url, gated_sync, domain_limiter
from .progress import fmt_secs
from .scrape import fetch_and_clean
from .sources.security_txt import check_security_txt
#from .sources.ddg import search_ddg
from .sources.exa import search_exa
from .sources.olostep import search_olostep

# Alternates which engine is *primary* for each domain so the two engines
# share the load roughly equally across a run.  asyncio is single-threaded,
# so advancing a bare cycle between coroutines is safe without a lock.
_engine_cycle = itertools.cycle([(search_exa, "exa", search_olostep, "olostep"),
                                  (search_olostep, "olostep", search_exa, "exa")])


async def _classify(url, raw_text, domain, security_txt=None, headings=None):
    """Classify one page. Returns the pipeline result or None.

    Returns None (rather than raising) if the classifier is unavailable or
    classification fails for any reason — a single unclassifiable page must
    never abort the run.
    """
    if not raw_text:
        return None
    if not classifier.available:
        return None
    try:
        def _run():
            return classifier.classify_page(
                url=url,
                raw_text=raw_text,
                headings=headings,
                domain=domain,
                security_txt=security_txt,
            )

        return await aio.to_thread(_run)
    except ClassifierUnavailable:
        # Classifier disappeared between the availability check and the
        # call. Log once; the CLI preflight normally prevents this.
        log.error("[CLS] classifier unavailable mid-run; skipping page")
        return None
    except Exception as e:
        log.error(f"[CLS] {url} err: {e.__class__.__name__}: {e}")
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
            primary_fn, primary_src, fallback_fn, fallback_src = next(_engine_cycle)
            log.debug(f"[SEARCH] {domain}: assigned primary={primary_src.upper()}, "
                      f"fallback={fallback_src.upper()}")
            try:
                hits = await primary_fn(domain)
                if hits:
                    log.info(f"[SEARCH] {domain}: {primary_src} succeeded "
                             f"({len(hits)} hits), skipping {fallback_src}")
                    for h in hits:
                        log.debug(f"[{primary_src.upper()}] {domain} → {h['url']}")
                    candidates.extend((r["url"], primary_src) for r in hits)
                else:
                    log.info(f"[SEARCH] {domain}: {primary_src} empty, "
                             f"trying {fallback_src}")
                    hits = await fallback_fn(domain)
                    if hits:
                        log.debug(f"[SEARCH] {domain}: {fallback_src} fallback "
                                  f"returned {len(hits)} hit(s)")
                        for h in hits:
                            log.debug(f"[{fallback_src.upper()}] {domain} → {h['url']}")
                    else:
                        log.debug(f"[SEARCH] {domain}: both engines returned empty")
                    candidates.extend((r["url"], fallback_src) for r in (hits or []))
            except Exception as e:
                log.warning(f"[SEARCH] {domain} {primary_src} err: {e}; "
                            f"trying {fallback_src}")
                try:
                    hits = await fallback_fn(domain)
                    if hits:
                        log.debug(f"[SEARCH] {domain}: {fallback_src} fallback "
                                  f"returned {len(hits)} hit(s) after {primary_src} error")
                        for h in hits:
                            log.debug(f"[{fallback_src.upper()}] {domain} → {h['url']}")
                    candidates.extend((r["url"], fallback_src) for r in (hits or []))
                except Exception as e2:
                    log.warning(f"[SEARCH] {domain} {fallback_src} err: {e2}")

        seen_canon = set()
        unique = []
        for url, src in candidates:
            c = canonicalize_url(url)
            if c and c not in seen_canon:
                seen_canon.add(c)
                unique.append((url, src))
        unique = unique[:MAX_URLS_PER_DOMAIN]
        log.debug(f"[ANZ] {domain}: {len(unique)} unique candidate(s) after dedup: "
                  + ", ".join(f"{src}:{url}" for url, src in unique))

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
        if findings:
            for f in findings:
                log.debug(f"[ANZ] {domain} finding via [{f['source'].upper()}]: "
                          f"{f['url']} → {f['label']} ({f['confidence']:.2f})")
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