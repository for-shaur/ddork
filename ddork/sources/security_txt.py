"""Look up a domain's security.txt (RFC 9116). Returns dict with:
found, source_url, policy_url, snippet (short), body (full text).

Every HTTP call goes through the shared AIMD limiter so global request concurrency is
capped regardless of how many domains run concurrently.

v3.1: uses the shared per-thread session from net.get_session() so we don't pay
TCP+TLS setup on every candidate path.
"""
import re

from ..config import log
from ..net import get_session, gated_sync

CANDIDATE_PATHS = (
    "https://{d}/.well-known/security.txt",
    "https://www.{d}/.well-known/security.txt",
    "https://{d}/security.txt",
)

PGP_BLOCK_RE = re.compile(
    r'-----BEGIN PGP SIGNED MESSAGE-----.*?-----END PGP SIGNATURE-----', re.S
)

_EMPTY = {"found": False, "source_url": None, "policy_url": None,
          "snippet": None, "body": None}


def _fetch(u):
    try:
        r = get_session().get(u, timeout=8)
    except Exception:
        return None
    if r.status_code != 200 or "contact:" not in r.text.lower():
        return None
    return r.text


async def check_security_txt(domain, limiter):
    for u in (p.format(d=domain) for p in CANDIDATE_PATHS):
        text = await gated_sync(limiter, _fetch, u, domain=domain)
        if not text:
            continue
        body = PGP_BLOCK_RE.sub('', text).strip()
        policy = next(
            (l.split(":", 1)[1].strip()
             for l in body.splitlines() if l.lower().startswith("policy:")),
            None,
        )
        log.info(f"[SEC] {domain} policy: {policy}")
        return {
            "found": True,
            "source_url": u,
            "policy_url": policy,
            "snippet": body[:400],
            "body": body,
        }
    log.info(f"[SEC] {domain} not found")
    return dict(_EMPTY)