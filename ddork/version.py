"""Version detection and update checking.

Two comparison sources:

  * GitHub raw — parse the `version` field from the repo's pyproject.toml.
    Used for git-installed packages.
  * PyPI JSON API — used as a fallback when the package is published there.

Every network call is best-effort: a failure returns None, never raises.
Stdlib only (urllib + json), so no new dependency.
"""
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from .config import (
    CLASSIFIER_GITHUB,
    CLASSIFIER_PACKAGE,
    DDORK_GITHUB,
    DDORK_PACKAGE,
    log,
)

_TIMEOUT = 3


# --- installed versions --------------------------------------------------

def get_installed_version(package):
    """Return the installed distribution version, or None if not installed."""
    try:
        return _pkg_version(package)
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def get_ddork_version():
    """ddork's own version. Falls back to the package __version__."""
    try:
        from . import __version__
        if __version__:
            return __version__
    except Exception:
        pass
    return get_installed_version(DDORK_PACKAGE)


def get_classifier_version():
    """isbounty's installed version, however it was installed.

    Import is best-effort — a missing or broken isbounty returns None and
    the banner renders "(not installed)". The classifier wrapper in
    classifier.py performs the real availability check; this is only for
    display.
    """
    try:
        import isbounty
        v = getattr(isbounty, "__version__", None)
        if v:
            return str(v)
    except Exception:
        pass
    return get_installed_version(CLASSIFIER_PACKAGE)


# --- version comparison --------------------------------------------------

def _version_tuple(v):
    """Parse '1.2.3' into (1, 2, 3). Non-numeric segments become 0."""
    parts = []
    for seg in (v or "").split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_outdated(current, latest):
    """True if `latest` is strictly newer than `current`."""
    if not current or not latest:
        return False
    try:
        return _version_tuple(latest) > _version_tuple(current)
    except Exception:
        return False


# --- latest versions -----------------------------------------------------

def fetch_pypi_latest(package, timeout=_TIMEOUT):
    """Latest version on PyPI, or None on failure / not published."""
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return data.get("info", {}).get("version")
    except Exception:
        return None


def fetch_github_version(repo, path="pyproject.toml", timeout=_TIMEOUT):
    """Version declared in `path` on the repo's default branch, or None."""
    if not repo:
        return None
    url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ddork"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        return _parse_version_from_toml(text)
    except Exception:
        return None


def _parse_version_from_toml(text):
    """Extract `version = "x.y.z"` from a pyproject.toml snippet."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("version") and "=" in line:
            _, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            if val:
                return val
    return None


# --- combined check ------------------------------------------------------

def check_updates():
    """Return (ddork_info, classifier_info). Never raises.

    Each info is a dict::
        {name, version, latest, outdated, error}
    """
    d_installed = get_ddork_version()
    c_installed = get_classifier_version()

    # ddork's own check only runs when a repo is configured. The PyPI fallback
    # is skipped on purpose: a similarly-named package there would produce a
    # false "outdated" warning (this already bit us once).
    def _d_latest():
        if not DDORK_GITHUB:
            return None
        return (fetch_github_version(DDORK_GITHUB)
                or fetch_pypi_latest(DDORK_PACKAGE))

    def _c_latest():
        return (fetch_github_version(CLASSIFIER_GITHUB)
                or fetch_pypi_latest(CLASSIFIER_PACKAGE))

    with ThreadPoolExecutor(max_workers=2) as pool:
        d_fut = pool.submit(_d_latest)
        c_fut = pool.submit(_c_latest)
        d_latest = d_fut.result()
        c_latest = c_fut.result()

    d_info = {
        "name": DDORK_PACKAGE,
        "version": d_installed,
        "latest": d_latest,
        "outdated": is_outdated(d_installed, d_latest),
        "error": None if d_installed else "not installed",
    }
    c_info = {
        "name": CLASSIFIER_PACKAGE,
        "version": c_installed,
        "latest": c_latest,
        "outdated": is_outdated(c_installed, c_latest),
        "error": None if c_installed else "not installed",
    }
    return d_info, c_info