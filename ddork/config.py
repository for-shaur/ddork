"""Logging setup and shared constants."""
import logging as lg
import sys

log = lg.getLogger("scan")


def configure_logging(debug):
    """Call once, early, from cli.main().

    --debug: full stream+file logging, no live UI.
    default: file-only logging; stdout is owned by ui.ScanUI instead.

    All handlers write UTF-8. Windows' default cp1252 crashes on any non-Latin-1
    character in a logged URL (seen in the wild: an emoji inside a DDG result URL),
    which takes down the whole process because logging's default error path is to
    print a traceback and keep going — but the traceback itself prints to the same
    broken stream, so the run dies.
    """
    _reconfigure_std_streams()

    handlers = [lg.FileHandler("scan.log", encoding="utf-8")]
    if debug:
        handlers.append(lg.StreamHandler(sys.stdout))

    lg.basicConfig(
        level=lg.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )


def _reconfigure_std_streams():
    """Force UTF-8 on stdout/stderr. No-op where reconfigure isn't supported
    (e.g. when the stream is already wrapped by something that isn't a TextIOWrapper)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# --- Fallback user agents (used if the remote UA endpoint is unavailable) ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# --- Request tuning ------------------------------------------------------
DEFAULT_TIMEOUT = 15
IMPERSONATE = "chrome110"