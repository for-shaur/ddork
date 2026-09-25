"""Lazy pool-based competitor expansion.

Each provider returns an ordered list. Take up to PER_PROVIDER from each into `domains`;
everything else goes into `extra`. To reach the target count, draw from `extra` first; when
`extra` runs dry, expand the next unexpanded domain. Expansion is roughly
O(target / (PER_PROVIDER * num_providers)) rounds, not exponential in depth.

The three providers are called in parallel per domain via a thread pool, all
sharing one AdaptiveLimiter so global request concurrency stays bounded.
"""
import threading as th
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import log
from ..net import normalize_domain, retry
from ..concurrency import AdaptiveLimiter
from ..progress import fmt_secs
from .distill import get_distill_competitors
from .spyfu import get_spyfu_competitors
from .owler import get_owler_competitors

PROVIDERS = [
    (get_spyfu_competitors, "S"),
    (get_distill_competitors, "D"),
    (get_owler_competitors, "O"),
]

PER_PROVIDER = 5


class Enumerator:
    def __init__(self, seed, target, workers=8, delay=0.5, ui=None):
        self.seed = normalize_domain(seed)
        self.target = target
        self.ui = ui
        self.workers = workers
        self.domains = []
        self.extra = []
        self.unexpanded = []
        self.seen = {self.seed} if self.seed else set()
        # min_interval=0: rely on AIMD alone for pacing. The old delay-based gate
        # held permits during sleep and hard-capped throughput at 1/delay req/s.
        self.limiter = AdaptiveLimiter(max_permits=workers, min_interval=0)
        self._lock = th.Lock()

    def _call(self, fn, domain):
        with self.limiter:
            return fn(domain)

    def _expand(self, domain):
        log.info(f"[ENUM] expand {domain}")

        # Fan out all providers in parallel; each still goes through the shared
        # AdaptiveLimiter so global request concurrency stays bounded.
        futures = {}
        with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
            for fn, label in PROVIDERS:
                fut = pool.submit(
                    lambda f=fn, l=label: (l, retry(lambda: self._call(f, domain)) or [])
                )
                futures[fut] = label

            for fut in as_completed(futures):
                label = futures[fut]
                try:
                    _, results = fut.result()
                except Exception as e:
                    log.error(f"[{label}] {domain} err: {e}")
                    if self.ui:
                        self.ui.v(2, f"{label} lookup failed for {domain}: {e}", ok=False)
                    continue

                fresh = []
                for r in results:
                    d = normalize_domain(r)
                    if d and d not in self.seen:
                        self.seen.add(d)
                        fresh.append(d)

                log.info(f"[{label}] {domain} -> {len(fresh)} fresh")
                if self.ui:
                    self.ui.v(2, f"{label} {domain}: {len(fresh)} fresh")
                with self._lock:
                    top, rest = fresh[:PER_PROVIDER], fresh[PER_PROVIDER:]
                    for d in top:
                        if len(self.domains) < self.target:
                            self.domains.append(d)
                        else:
                            self.extra.append(d)
                    self.extra.extend(rest)
                    self.unexpanded.extend(fresh)

        with self._lock:
            self.unexpanded = [d for d in self.unexpanded if d != domain]

    def run(self):
        if not self.seed:
            log.error("enumerate_domains: empty seed domain")
            return []

        if self.ui:
            self.ui.phase("enumerating", self.target)
        start = time.monotonic()

        self._expand(self.seed)
        while len(self.domains) < self.target:
            before = len(self.domains)
            if self.extra:
                self.domains.append(self.extra.pop(0))
            elif self.unexpanded:
                self._expand(self.unexpanded.pop(0))
            else:
                break
            if self.ui and len(self.domains) > before:
                self.ui.advance(len(self.domains) - before)

        log.info(
            f"[ENUM] complete: {len(self.domains)}/{self.target} domains "
            f"(pool leftover: {len(self.extra)})"
        )
        if self.ui:
            self.ui.checkpoint(
                f"enumerated {len(self.domains)}/{self.target} target(s) in "
                f"{fmt_secs(time.monotonic() - start)} — "
                f"{len(self.extra)} still in pool"
            )
        return self.domains


def enumerate_domains(seed, target, workers=8, delay=0.5, ui=None):
    return Enumerator(seed, target, workers, delay, ui).run()