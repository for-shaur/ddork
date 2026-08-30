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

# (provider_fn, short_label_for_logs)
PROVIDERS = [
    (get_spyfu_competitors, "S"),
    (get_distill_competitors, "D"),
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


def enumerate_domains(seed_domain, depth, workers, delay, ui=None):
    """BFS out from `seed_domain` for `depth` levels. `workers` is a hard ceiling; actual
    concurrency is self-tuned within it by the shared AdaptiveLimiter, which persists
    across depth levels so it keeps adapting as the frontier grows."""
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
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(find_competitors, d, seen, lock, limiter, ui): d for d in frontier}
            for f in cf.as_completed(futures):
                next_frontier.update(f.result())
                if ui:
                    ui.status(f"enumerating  depth {level}/{depth} · {len(seen)} targets found")
        frontier = next_frontier
        seen.update(frontier)
        log.info(f"Depth {level}: found {len(next_frontier)} new targets (total unique so far: {len(seen)})")
        if ui:
            ui.checkpoint(f"enumeration  depth {level}/{depth} → {len(next_frontier)} new targets ({len(seen)} total)")

    log.info(f"Enumeration complete: {len(seen)} total targets across {depth} depth(s)")
    return sorted(seen)