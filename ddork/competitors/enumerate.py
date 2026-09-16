"""Lazy pool-based competitor expansion.

Each provider returns an ordered list. Take up to PER_PROVIDER from each into `domains`;
everything else goes into `extra`. To reach the target count, draw from `extra` first; when
`extra` runs dry, expand the next unexpanded domain. Expansion is roughly
O(target / (PER_PROVIDER * num_providers)) rounds, not exponential in depth.

v3.1: the three providers are now called in parallel per domain via a thread pool,
and the AdaptiveLimiter no longer holds permits during min_interval sleeps — both
changes together roughly triple enumeration throughput.
"""
import threading as th
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import log
from ..net import normalize_domain, retry
from ..concurrency import AdaptiveLimiter
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
                        self.ui.checkpoint(
                            f"enumeration  {label} {domain} err: {e}", ok=False
                        )
                    continue

                fresh = []
                for r in results:
                    d = normalize_domain(r)
                    if d and d not in self.seen:
                        self.seen.add(d)
                        fresh.append(d)

                log.info(f"[{label}] {domain} -> {len(fresh)} fresh")
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
        self._expand(self.seed)
        while len(self.domains) < self.target:
            if self.extra:
                self.domains.append(self.extra.pop(0))
            elif self.unexpanded:
                self._expand(self.unexpanded.pop(0))
            else:
                break
            if self.ui:
                self.ui.status(
                    f"enumerating  {len(self.domains)}/{self.target} targets "
                    f"· extra pool: {len(self.extra)}"
                )
        log.info(
            f"[ENUM] complete: {len(self.domains)}/{self.target} domains "
            f"(pool leftover: {len(self.extra)})"
        )
        return self.domains


def enumerate_domains(seed, target, workers=8, delay=0.5, ui=None):
    return Enumerator(seed, target, workers, delay, ui).run()