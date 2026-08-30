"""Heuristic classification of whether a policy page indicates a paid bug bounty."""
import re

from .config import log

# skips "$0" so a zero-dollar minimum isn't scored as "pays"
MONEY_RE = r'(?:\$|€|£|usd|eur|gbp)\s*(?!0(?:[.,]0+)?\b)\d[\d,]*(?:\.\d+)?'

POSITIVE_PATTERNS = [
    (rf'(?:bounty|bounties|reward|payout|compensation|minimum|low|medium|high|critical).{{0,30}}{MONEY_RE}', "bounty_money_fwd", 3),
    (rf'{MONEY_RE}.{{0,30}}(?:bounty|bounties|reward|payout)', "bounty_money_bwd", 3),
    # windowed near bounty/reward — not matching a payments/crypto mention anywhere on the whole page
    (r'(?:bounty|reward|vrp).{0,200}(?:paypal|wire transfer|bank transfer|crypto|bitcoin|ethereum|ach|swift)|'
     r'(?:paypal|wire transfer|bank transfer|crypto|bitcoin|ethereum|ach|swift).{0,200}(?:bounty|reward|vrp)',
     "payment_method", 2),
    (r'(?:paid|monetary|cash|financial)\s+(?:reward|bounty|compensation)', "paid_reward", 2),
]

NEGATIVE_PATTERNS = [
    r'no\s+monetary\s+(?:reward|compensation|bounty|payment)',
    r'no\s+cash\s+(?:reward|bounty|payment)',
    r'do(?:es)?\s+not\s+(?:offer|provide|pay)\s+(?:any\s+)?(?:monetary|cash|financial|money)',
    r'will\s+not\s+(?:offer|provide|pay)\s+(?:any\s+)?(?:monetary|cash|financial|money)',
    r'no\s+(?:financial|monetary)\s+(?:reward|incentive|compensation|payment)',
    r'(?:swag|merchandise|hall\s+of\s+fame|acknowledgment|certificate)\s+(?:only|in\s+lieu|instead)',
]


def classify_bounty(text):
    if not text:
        return None
    t, score, evidence, quotes = text.lower(), 0, [], []

    for pattern, label, weight in POSITIVE_PATTERNS:
        m = re.search(pattern, t)
        if m:
            score += weight
            evidence.append(label)
            quotes.append(m.group(0))
            log.info(f"[CLF] signal {label}: '{m.group(0)}'")

    for pattern in NEGATIVE_PATTERNS:
        m = re.search(pattern, t)
        if m:
            score -= 3
            evidence.append("no_money")
            quotes.append(m.group(0))
            log.info(f"[CLF] signal no_money: '{m.group(0)}'")
            break

    if score <= 0 and re.search(r'\bresponsible\s+disclosure\b', t):
        score -= 1
        evidence.append("responsible_disclosure")

    confidence = "high" if score >= 3 or score <= -2 else "medium" if score >= 1 or score < 0 else "low"
    offers_money = True if score > 0 else False if score < 0 else None

    return {
        "offers_money": offers_money,
        "confidence": confidence,
        "evidence": " | ".join(evidence) or "none",
        "evidence_quote": " | ".join(quotes[:3]) if quotes else None,
    }
