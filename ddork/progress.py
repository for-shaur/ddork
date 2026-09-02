"""Tiny rate/ETA helpers for ui.py status lines. No dependencies beyond stdlib."""
import time


def fmt_secs(secs):
    secs = max(0, int(secs))
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def rate_per_min(start, done):
    elapsed = time.monotonic() - start
    return done / elapsed * 60 if elapsed > 0 else 0.0


def eta_str(start, done, total):
    """Linear extrapolation from current rate. `total` may be None (unbounded run)."""
    if not total or done <= 0:
        return "?"
    elapsed = time.monotonic() - start
    rate = done / elapsed if elapsed > 0 else 0
    if rate <= 0:
        return "?"
    return fmt_secs((total - done) / rate)