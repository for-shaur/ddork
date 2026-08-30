"""Look up a domain's security.txt (RFC 9116) file for a disclosure/bounty policy URL."""
import asyncio as aio
import re

from curl_cffi import requests as rq

from ..config import IMPERSONATE, log
from ..net import retry_async

# Includes the legacy top-level /security.txt (plenty of sites still only have that one),
# plus a cheap "contact:" sanity check so a 200-OK WAF/CDN block page isn't scored as "found".
CANDIDATE_PATHS = (
    "https://{d}/.well-known/security.txt",
    "https://www.{d}/.well-known/security.txt",
    "https://{d}/security.txt",
)

PGP_BLOCK_RE = re.compile(r'-----BEGIN PGP SIGNED MESSAGE-----.*?-----END PGP SIGNATURE-----', re.S)


async def check_security_txt(domain, tries=2):
    for u in (p.format(d=domain) for p in CANDIDATE_PATHS):
        try:
            log.info(f"[SEC] Try {u}")
            r = await retry_async(lambda u=u: aio.to_thread(rq.get, u, impersonate=IMPERSONATE, timeout=8), tries=tries)
            log.info(f"[SEC] {u} -> {r.status_code}")
            if r.status_code == 200 and "contact:" in r.text.lower():
                t = PGP_BLOCK_RE.sub('', r.text)
                policy = next((l.split(":", 1)[1].strip() for l in t.splitlines() if l.lower().startswith("policy:")), None)
                log.info(f"[SEC] {domain} policy: {policy}")
                return {"found": True, "source_url": u, "policy_url": policy, "snippet": t[:400]}
        except Exception as e:
            log.error(f"[SEC] {u} err: {e}")
    log.info(f"[SEC] {domain} not found")
    return {"found": False, "source_url": None, "policy_url": None, "snippet": None}