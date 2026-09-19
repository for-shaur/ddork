"""Startup banner: logo, version lines, update hint.

Every bracket and paren is drawn in the `neutral` role (bright white); only
the content between them takes a semantic color:

    (latest)              success — green
    (outdated → x.y.z)    warn    — yellow
    (not installed)       error   — red
    <empty>               no tag when the remote check could not be reached
"""
import subprocess
import sys

from .colors import palette
from .config import CLASSIFIER_GIT_URL, log
from .version import check_updates, is_outdated

LOGO = r"""
   ┓ ┓    ┓ 
  ┏┫┏┫┏┓┏┓┃┏
  ┗┻┗┻┗┛┛ ┛┗
"""


def _parens(inner):
    """`(inner)` with white parens and the caller-provided colored inner."""
    return f"{palette.neutral('(')}{inner}{palette.neutral(')')}"


def _tag(installed, latest, error):
    """Return a colored suffix for one version line."""
    if error:
        return _parens(palette.error("not installed"))
    if not installed or not latest:
        return ""   # no source of truth; don't claim anything
    if is_outdated(installed, latest):
        return _parens(palette.warn(f"outdated → {latest}"))
    return _parens(palette.success("latest"))


def print_banner(verbose=1):
    """Print the logo and version lines. Silenced by -v 0."""
    if verbose < 1:
        return

    from . import __version__ as ddork_ver

    d_info, c_info = check_updates()

    print(palette.brand(LOGO.rstrip("\n")))
    print()

    d_tag = _tag(ddork_ver, d_info.get("latest"), d_info.get("error"))
    d_line = f"  {palette.bold('ddork')}    {palette.success(ddork_ver)}"
    if d_tag:
        d_line += f" {d_tag}"
    print(d_line)

    c_tag = _tag(c_info.get("version"), c_info.get("latest"), c_info.get("error"))
    c_ver = (palette.success(c_info["version"])
             if c_info.get("version") else palette.muted("?"))
    c_line = f"  {palette.bold('isbounty')} {c_ver}"
    if c_tag:
        c_line += f" {c_tag}"
    print(c_line)

    if d_info.get("outdated") or c_info.get("outdated"):
        print()
        print(palette.muted("  run with --update to refresh the classifier"))

    print()


def update_classifier():
    """pip install --upgrade git+https://github.com/forshaur/isbounty."""
    log.info(f"[UPD] installing classifier from git+{CLASSIFIER_GIT_URL}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade",
             f"git+{CLASSIFIER_GIT_URL}"],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        log.error("[UPD] pip install timed out after 180s")
        return False
    except Exception as e:
        log.error(f"[UPD] pip install failed: {e}")
        return False

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-600:]
        log.error(f"[UPD] pip install failed:\n{tail}")
        return False

    log.info("[UPD] classifier updated")
    return True