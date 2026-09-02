"""Breadth-first expansion of a seed domain into a set of competitor domains.

To add a new competitor-discovery provider: write a `get_x_competitors(domain) -> set[str]`
function (see spyfu.py / distill.py for the shape) and add it to PROVIDERS below.
"""
import concurrent.futures as cf
import threading as th
import time

from ..config import log
from ..net import normalize_domain, retry
from ..concurrency import AdaptiveLimiter
from ..progress import eta_str, rate_per_min
from .distill import get_distill_competitors
from .spyfu import get_spyfu_competitors
from .owler import get_owler_competitors
from .wappy import get_wappalyzer_competitors
# (provider_fn, short_label_for_logs)
PROVIDERS = [
    (get_spyfu_competitors, "S"),
    (get_distill_competitors, "D"),
    (get_owler_competitors, "O"),
    #(get_wappalyzer_competitors, "W"),
]


def _limited_call(fn, domain, limiter):
    """Run fn(domain) inside the limiter so every retry attempt goes through admission control."""
    with limiter:
        return fn(domain)


def _query_provider(domain, fn, label, seen, lock, limiter, source_counts, ui=None):
    """Run a single provider against a single domain; merge genuinely-new domains
    into `seen`. This is the unit of work submitted to the executor — one future
    per (domain, provider) pair, not one future per domain, so a domain's 4
    provider calls run concurrently instead of serially inside one thread."""
    try:
        new = retry(lambda: _limited_call(fn, domain, limiter)) or set()
        with lock:
            delta = new - seen
            seen.update(new)
            source_counts[label] = source_counts.get(label, 0) + len(delta)
        log.info(f"[{label}] {domain} found {len(new)}")
        return delta
    except Exception as e:
        log.error(f"[{label}] {domain} err: {e}")
        if ui:
            ui.checkpoint(f"enumeration  {label} {domain} err: {e}", ok=False)
        return set()


def enumerate_domains(seed_domain, workers, delay, min_targets, ui=None):
    """BFS out from `seed_domain`, expanding level by level with no depth cap,
    until `min_targets` total domains are found or the frontier naturally runs
    dry. On hitting min_targets mid-level, in-flight futures finish but no new
    ones are submitted and remaining queued futures are cancelled."""
    seed = normalize_domain(seed_domain)
    if not seed:
        log.error("enumerate_domains: empty seed domain")
        return []

    seen, frontier = {seed}, {seed}
    # limiter.max_permits is the real concurrency ceiling (network-facing, AIMD-adjusted).
    # The ThreadPoolExecutor just needs enough threads that it's never the bottleneck in
    # front of the limiter -- workers domains x 4 providers each, all eligible to run at
    # once, so the limiter's permits (which can climb above `workers`) are always reachable.
    limiter = AdaptiveLimiter(max_permits=workers, min_interval=delay)
    pool_size = workers * len(PROVIDERS)
    level = 0
    start = time.monotonic()  # ponytail: one clock for the whole run, ETA is coarse on purpose
    while frontier:
        level += 1
        log.info(f"Depth {level}: Scanning {len(frontier)} domains (permits: {limiter.permits}/{limiter.max_permits})")
        next_frontier, lock = set(), th.Lock()
        source_counts = {}
        req_count = 0
        hit_cap = False

        def report_progress():
            nonlocal req_count
            req_count += 1  # ponytail: display-only counter, minor races under GIL are fine
            if ui:
                n = len(seen)
                ui.status(
                    f"enumerating  depth {level} · {n}/{min_targets} targets · {req_count} requests "
                    f"· {rate_per_min(start, n):.0f}/min · ETA {eta_str(start, n, min_targets)}"
                )

        with cf.ThreadPoolExecutor(max_workers=pool_size) as ex:
            futures = {
                ex.submit(_query_provider, d, fn, label, seen, lock, limiter, source_counts, ui): (d, label)
                for d in frontier for fn, label in PROVIDERS
            }
            for f in cf.as_completed(futures):
                next_frontier.update(f.result())
                report_progress()
                if len(seen) >= min_targets:
                    hit_cap = True
                    ex.shutdown(cancel_futures=True)
                    break
        frontier = next_frontier
        seen.update(frontier)
        log.info(f"Depth {level}: found {len(next_frontier)} new targets (total unique so far: {len(seen)})")
        if ui:
            breakdown = " ".join(f"{label}:{n}" for label, n in sorted(source_counts.items()) if n)
            ui.checkpoint(
                f"enumeration  depth {level} → {len(next_frontier)} new targets"
                f"{' (' + breakdown + ')' if breakdown else ''} ({len(seen)} total)"
            )
        if hit_cap:
            log.info(f"Hit min_targets={min_targets}, stopping enumeration early at depth {level}")
            if ui:
                ui.checkpoint(f"enumeration  stopped early: reached max targets ({min_targets})")
            break

    log.info(f"Enumeration complete: {len(seen)} total targets across {level} depth(s)")
    return sorted(seen)