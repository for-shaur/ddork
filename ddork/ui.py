"""Terminal output in sqlmap's style: timestamped permanent lines plus one
in-place progress bar. Colors auto-disable on non-TTY.

Verbosity:
  0  quiet     — no UI, only the final report
  1  normal    — permanent lines + bar (default)
  2  verbose   — adds per-source and per-domain checkpoints
  3  debug     — UI disabled; raw logger takes over (see config.configure_logging)
"""
import time

from .colors import palette
from .progress import ProgressBar

# Phase label -> palette method name. Feeds both the bar and any [phase] tag.
PHASE_COLORS = {
    "enumerating": "bright_cyan",
    "probing":     "bright_magenta",
    "security":    "bright_blue",
    "exa":         "bright_yellow",
    "searx":       "bright_yellow",
    "ddg":         "bright_yellow",
}


def _stamp():
    return palette.grey(time.strftime("%H:%M:%S"))


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
        mark = palette.bright_green("+") if ok else palette.bright_red("!")
        tag = f"{palette.white('[')}{mark}{palette.white(']')}"
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
        color = PHASE_COLORS.get(label, "bright_magenta")
        self._bar = ProgressBar(label, total, color=color)
        self._bar.start_ticker()

    def advance(self, n=1):
        if self._bar:
            self._bar.advance(n)