"""Terminal output in sqlmap's style: timestamped permanent lines plus one
in-place progress bar. Colors auto-disable on non-TTY.

Roles (see colors.Palette): brand for phases, success/error for [+]/[!],
neutral for brackets, muted for secondary text.

Verbosity:
  0  quiet     — no UI, only the final report
  1  normal    — permanent lines + bar (default)
  2  verbose   — adds per-source and per-domain checkpoints
  3  debug     — UI disabled; raw logger takes over
"""
import time

from .colors import palette
from .progress import ProgressBar


def _stamp():
    return palette.muted(time.strftime("%H:%M:%S"))


class ScanUI:
    def __init__(self, verbose=1):
        self.verbose = verbose
        self._bar = None

    # -- lifecycle -------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        if self._bar:
            self._bar.finish()
            self._bar = None

    # -- permanent lines -------------------------------------------------

    def line(self, text):
        if self._bar:
            self._bar.pause()
        print(text, flush=True)
        if self._bar:
            self._bar.resume()

    def checkpoint(self, text, ok=True):
        mark = palette.success("+") if ok else palette.error("!")
        tag = f"{palette.neutral('[')}{mark}{palette.neutral(']')}"
        self.line(f"{_stamp()} {tag} {text}")

    def error(self, text):
        self.checkpoint(text, ok=False)

    def v(self, level, text, ok=True):
        """Emit only when verbosity >= level."""
        if self.verbose >= level:
            self.checkpoint(text, ok=ok)

    # -- live bar --------------------------------------------------------

    def phase(self, label, total):
        """Start (or restart) the single live bar for a phase."""
        if self._bar:
            self._bar.finish()
        self._bar = ProgressBar(label, total)
        self._bar.start_ticker()

    def advance(self, n=1):
        if self._bar:
            self._bar.advance(n)