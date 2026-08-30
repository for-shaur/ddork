"""Minimalistic, responsive terminal UI.

Two regions, "divided like the equator" — nothing drawn for the line itself, just
convention: checkpoints (finished phases/depths, and errors) stack above a rule;
exactly one live status line sits below it and gets redrawn in place. Keeps the
user seeing *something move* during long enumeration/probing runs without
scrolling the terminal into a wall of log lines.

Disabled entirely by --debug, which keeps the original raw logger instead
(see config.configure_logging).
"""
import threading

from rich.console import Group
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

RULE = "─" * 56


class ScanUI:
    def __init__(self):
        self._lock = threading.Lock()
        self._checkpoints = []
        self._spinner = Spinner("dots", text="")  # dots = the requested ⠋⠙⠹⠸⠼ set, stdlib-adjacent (rich builtin)
        self._live = Live(self._render(), refresh_per_second=12, transient=False)

    def _render(self):
        # plain Text throughout — no color/markup, so piping to a file stays readable (req #3)
        lines = [Text(c) for c in self._checkpoints]
        return Group(*lines, Text(RULE), self._spinner)

    def __enter__(self):
        self._live.start()
        return self

    def __exit__(self, *exc):
        self._live.stop()
        return False

    def status(self, text):
        """Redraw the single live line. Called on every completed unit of work (req #2)."""
        with self._lock:
            self._spinner.text = text
            self._live.update(self._render())

    def checkpoint(self, text, ok=True):
        """Append a permanent line above the rule: phase/depth summary, or an error."""
        with self._lock:
            self._checkpoints.append(f"{'✓' if ok else '✗'} {text}")
            self._live.update(self._render())