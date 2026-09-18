"""Minimal ANSI palette. Auto-disabled when the target stream is not a TTY,
when NO_COLOR is set, or when FORCE_COLOR=0. FORCE_COLOR=1 forces on.

Every method wraps text in a self-contained escape pair, so nested colors
work without tracking state.
"""
import os
import re
import sys

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s):
    return _ANSI_RE.sub("", s)


def visible_len(s):
    return len(strip_ansi(s))


def _enabled(stream):
    if os.environ.get("NO_COLOR"):
        return False
    fc = os.environ.get("FORCE_COLOR")
    if fc is not None:
        return fc not in ("", "0", "false", "no")
    try:
        return bool(stream.isatty())
    except Exception:
        return False


class Palette:
    def __init__(self, stream=None):
        self.enabled = _enabled(stream or sys.stdout)

    def _wrap(self, code, s):
        if not self.enabled or not s:
            return s
        return f"\x1b[{code}m{s}\x1b[0m"

    # styles
    def bold(self, s):    return self._wrap("1", s)
    def dim(self, s):     return self._wrap("2", s)
    def underline(self, s): return self._wrap("4", s)

    # base colors
    def red(self, s):     return self._wrap("31", s)
    def green(self, s):   return self._wrap("32", s)
    def yellow(self, s):  return self._wrap("33", s)
    def blue(self, s):    return self._wrap("34", s)
    def magenta(self, s): return self._wrap("35", s)
    def cyan(self, s):    return self._wrap("36", s)
    def grey(self, s):    return self._wrap("90", s)

    # bright
    def bright_red(self, s):     return self._wrap("91", s)
    def bright_green(self, s):   return self._wrap("92", s)
    def bright_yellow(self, s):  return self._wrap("93", s)
    def bright_blue(self, s):    return self._wrap("94", s)
    def bright_magenta(self, s): return self._wrap("95", s)
    def bright_cyan(self, s):    return self._wrap("96", s)
    def white(self, s):          return self._wrap("97", s)  


palette = Palette()