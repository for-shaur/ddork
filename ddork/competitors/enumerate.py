"""Breadth-first expansion of a seed domain into a set of competitor domains.

To add a new competitor-discovery provider: write a `get_x_competitors(domain) -> set[str]`
function (see spyfu.py / distill.py for the shape) and add it to PROVIDERS below.
"""
import concurrent.futures as cf
import threading as th

from ..config import log
from ..net import normalize_domain, retry
from ..concurrency import AdaptiveLimiter
from .distill import get_distill_competitors
from .spyfu import get_spyfu_competitors
from .owler import get_owler_competitors
from .wappy import get_wappalyzer_competitors
# (provider_fn, short_label_for_logs)
PROVIDERS = [
    (get_spyfu_competitors, "S"),
    (get_distill_competitors, "D"),
    (get_owler_competitors, "O"),
    (get_wappalyzer_competitors, "W"),
]


def _limited_call(fn, domain, limiter):
    """Run fn(domain) inside the limiter so every retry attempt goes through admission control."""
    with limiter:
        return fn(domain)


def find_competitors(domain, seen, lock, limiter, ui=None):
    """Query every provider for `domain`, merging newly-seen domains into `seen`."""
    found = set()
    for fn, label in PROVIDERS:
        try:
            new = retry(lambda fn=fn: _limited_call(fn, domain, limiter)) or set()
            with lock:
                delta = new - seen
                seen.update(new)
                found.update(delta)
            log.info(f"[{label}] {domain} found {len(new)}")
        except Exception as e:
            log.error(f"[{label}] {domain} err: {e}")
            if ui:
                ui.checkpoint(f"enumeration  {label} {domain} err: {e}", ok=False)
    return found

# --- competitors/enumerate.py ---
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
    limiter = AdaptiveLimiter(max_permits=workers, min_interval=delay)
    level = 0
    while frontier:
        level += 1
        log.info(f"Depth {level}: Scanning {len(frontier)} domains (permits: {limiter.permits}/{limiter.max_permits})")
        next_frontier, lock = set(), th.Lock()
        hit_cap = False
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(find_competitors, d, seen, lock, limiter, ui): d for d in frontier}
            for f in cf.as_completed(futures):
                next_frontier.update(f.result())
                if ui:
                    ui.status(f"enumerating  depth {level} · {len(seen)}/{min_targets} targets found")
                if len(seen) >= min_targets:
                    hit_cap = True
                    ex.shutdown(cancel_futures=True)
                    break
        frontier = next_frontier
        seen.update(frontier)
        log.info(f"Depth {level}: found {len(next_frontier)} new targets (total unique so far: {len(seen)})")
        if ui:
            ui.checkpoint(f"enumeration  depth {level} → {len(next_frontier)} new targets ({len(seen)} total)")
        if hit_cap:
            log.info(f"Hit min_targets={min_targets}, stopping enumeration early at depth {level}")
            if ui:
                ui.checkpoint(f"enumeration  stopped early: reached max targets ({min_targets})")
            break

    log.info(f"Enumeration complete: {len(seen)} total targets across {level} depth(s)")
    return sorted(seen)
    
    """BFS out from `seed_domain` for `depth` levels, or until `min_targets` total
    domains are found, whichever comes first. On hitting min_targets mid-level,
    in-flight futures are left to finish but no new ones are submitted, and the
    executor cancels whatever's still queued."""
    seed = normalize_domain(seed_domain)
    if not seed:
        log.error("enumerate_domains: empty seed domain")
        return []

    seen, frontier = {seed}, {seed}
    limiter = AdaptiveLimiter(max_permits=workers, min_interval=delay)
    for level in range(1, depth + 1):
        if not frontier:
            break
        log.info(f"Depth {level}: Scanning {len(frontier)} domains (permits: {limiter.permits}/{limiter.max_permits})")
        next_frontier, lock = set(), th.Lock()
        hit_cap = False
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(find_competitors, d, seen, lock, limiter, ui): d for d in frontier}
            for f in cf.as_completed(futures):
                next_frontier.update(f.result())
                if ui:
                    ui.status(f"enumerating  depth {level}/{depth} · {len(seen)} targets found")
                if min_targets and len(seen) >= min_targets:
                    hit_cap = True
                    ex.shutdown(cancel_futures=True)  # ponytail: drop unstarted work, let in-flight finish
                    break
        frontier = next_frontier
        seen.update(frontier)
        log.info(f"Depth {level}: found {len(next_frontier)} new targets (total unique so far: {len(seen)})")
        if ui:
            ui.checkpoint(f"enumeration  depth {level}/{depth} → {len(next_frontier)} new targets ({len(seen)} total)")
        if hit_cap:
            log.info(f"Hit min_targets={min_targets}, stopping enumeration early at depth {level}")
            if ui:
                ui.checkpoint(f"enumeration  stopped early: reached max targets ({min_targets})")
            break

    log.info(f"Enumeration complete: {len(seen)} total targets across {depth} depth(s)")
    return sorted(seen)