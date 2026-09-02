"""Heuristic classification of whether a policy page indicates a paid bug bounty."""
import re

from .config import log

MONEY_RE = r'(?:\$|€|£|¥|₹|₩|﷼|৳)\s*(?!0(?:[.,]0+)?\b)\d[\d,]*(?:\.\d+)?'
MONEY_WORD_RE = r'(?!0\b)\d[\d,]*(?:\.\d+)?\s*(?:USD|EUR|GBP|CNY|BTC|ETH|Tomans?|Toman|won|yen|rupee)'

BOUNTY_POSITIVE = [
    (r'Offers\s+Bounties\s*:\s*True', 5, "offers_bounties_true"),
    (r'pay_for_success', 5, "pay_for_success"),
    (rf'(?:bounty|bounties|reward|payout|compensation|prize|uang\s+pembinaan).{{0,120}}{MONEY_RE}', 4, "bounty_money_fwd"),
    (rf'{MONEY_RE}.{{0,120}}(?:bounty|bounties|reward|payout)', 4, "bounty_money_bwd"),
    (rf'(?:bounty|bounties|reward|payout|compensation).{{0,120}}{MONEY_WORD_RE}', 4, "bounty_money_word_fwd"),
    (rf'{MONEY_WORD_RE}.{{0,120}}(?:bounty|bounties|reward|payout)', 4, "bounty_money_word_bwd"),
    (r'(?:will\s+)?pay\s+(?:you|users?|researchers?|hackers?|finders?|reporters?)', 4, "will_pay_researchers"),
    (r'(?:researchers?|reporters?|hackers?|finders?)\s+(?:will\s+be\s+|are\s+)?(?:rewarded|compensated|paid)\s+(?:with\s+)?(?:cash|money|monetary|financial|bounty|bounties)', 4, "researchers_paid"),
    (r'(?:cash|monetary|financial)\s+(?:reward|bounty|compensation|payout|incentive)s?\s+(?:for|based|depending|paid|offered|awarded|given)', 4, "cash_reward_offered"),
    (r'(?:our|the|this|its)\s+(?:bug\s+)?bounty\s+(?:program|initiative|scheme)', 3, "our_bounty_program"),
    (r'(?:we|the\s+company)\s+(?:offer|provide|have|maintain|operate|run|host)\s+(?:a\s+)?(?:bug\s+)?bounty', 3, "we_offer_bounty"),
    (r'bounty\s+(?:will\s+be\s+|is\s+)?(?:paid|awarded|granted|issued|given|determined)', 3, "bounty_paid"),
    (r'(?:eligible|qualify)\s+(?:for\s+)?(?:a\s+)?(?:bounty|reward|payment|payout|monetary)', 3, "eligible_for_bounty"),
    (r'(?:bounty|reward|vrp|disclosure).{0,200}(?:paypal|wire\s+transfer|bank\s+transfer|bitcoin|ethereum|amazon\s+gift\s+card|direct\s+deposit)', 3, "payment_method_near_bounty"),
    (r'(?:paypal|wire\s+transfer|bank\s+transfer|bitcoin|ethereum|amazon\s+gift\s+card).{0,200}(?:bounty|reward|vrp|disclosure)', 3, "payment_method_near_bounty_rev"),
    (r'(?:low|medium|high|critical|exceptional)\s*(?::|\||-|–|—|=|→)\s*(?:\$|€|£|¥)\s*\d', 3, "reward_tier_money"),
    (r'(?:bounty|bug[_-]?bounty|security[_-]?bounty)(?:\.|\s+program|\s+page)', 1, "bounty_keyword"),
    (r'(?:vulnerabilit|report|submission|finding|disclosure).{0,100}(?:receive\s+(?:a\s+|an\s+)?(?:award|reward|compensation|bounty))', 3, "receive_award"),
    (r'(?:reward|award|compensat).{0,100}(?:vulnerabilit|report|submission|finding|disclosure)', 2, "reward_for_vuln"),
]

BOUNTY_NEGATIVE = [
    (r'Offers\s+Bounties\s*:\s*False', -5, "offers_bounties_false"),
    (r'(?:no|not|without|zero)\s+(?:monetary|financial|cash)\s+(?:reward|compensation|bounty|payment|incentive)s?', -4, "no_monetary_reward"),
    (r'(?:does?|do|will|can)\s+not\s+(?:currently\s+)?(?:offer|provide|pay|operate|run|have|guarantee)\s+(?:any\s+)?(?:a\s+)?(?:paid\s+)?(?:monetary\s+)?(?:bug\s+)?(?:bounty|reward|compensation|payment)', -4, "does_not_offer_bounty"),
    (r'not\s+(?:currently\s+)?(?:possible|able)\s+(?:for\s+us\s+)?to\s+offer\s+(?:a\s+)?(?:paid|monetary|cash|financial)', -4, "not_able_to_offer"),
    (r'(?:this|it)\s+is\s+(?:a\s+)?(?:vulnerability|security)\s+disclosure\s+(?:policy|program)\s*,?\s*(?:and\s+)?not\s+(?:a\s+)?(?:bug\s+)?bounty', -4, "is_vdp_not_bounty"),
    (r'not\s+(?:a\s+)?(?:bug\s+)?bounty\s+program', -4, "not_bounty_program"),
    (r'(?:not\s+obliged?|no\s+obligation)\s+to\s+(?:provide\s+)?(?:remuneration|compensation|fees?|payment|monetary)', -4, "not_obliged_pay"),
    (r'(?:must|should)\s+not\s+(?:request|demand)\s+(?:compensation|payment|money)', -4, "must_not_request_payment"),
    (r'prohibit\w*\s+(?:demanding|requesting)\s+(?:payment|compensation)', -4, "prohibit_demanding_payment"),
    (r'(?:swag|merchandise|hall\s+of\s+fame|acknowledgment|certificate|recognition)\s+(?:only|in\s+lieu|instead\s+of)', -3, "recognition_only"),
    (r'point[- ]based\s+only', -3, "point_based_only"),
]

VDP_POSITIVE = [
    (r'vulnerability\s+disclosure\s+(?:program|policy|process)', 2, "vdp_keyword"),
    (r'responsible\s+disclosure\s+(?:program|policy|guideline)', 2, "resp_disclosure_policy"),
    (r'security\s+disclosure\s+policy', 2, "sec_disclosure_policy"),
    (r'coordinated\s+(?:vulnerability\s+)?disclosure', 1, "coord_disclosure"),
    (r'report\s+(?:a\s+)?(?:security\s+)?vulnerabilit', 1, "report_vuln"),
    (r'security@\w+\.\w+', 1, "security_email"),
]

THIRD_PARTY_NEGATIVE = [
    (r'(?:article|blog\s+post|news|report|podcast|interview|story|guide|tutorial|course|lesson|module)\s+(?:about|on|covering|discussing|explaining|regarding)\s+(?:bug\s+)?bounty', -3, "article_about_bounty"),
    (r'what\s+(?:is|are)\s+(?:a\s+)?(?:bug\s+)?bounties?\b', -2, "what_is_bounty"),
    (r'(?:how|getting\s+started|beginner)\s+(?:to\s+)?(?:start|get\s+into|begin)\s+(?:a\s+)?(?:bug\s+)?bounty', -2, "how_to_start_bounty"),
    (r'(?:learn|learning)\s+(?:about\s+)?(?:bug\s+)?bounty', -1, "learn_about_bounty"),
]

def classify_bounty(text):
    if not text or len(text.strip()) < 20:
        return None
        
    t = text.lower()
    
    bounty_score = 0
    vdp_score = 0
    third_party_score = 0
    evidence = []
    quotes = []
    
    for pattern, weight, label in BOUNTY_POSITIVE:
        m = re.search(pattern, t, re.IGNORECASE)
        if m:
            bounty_score += weight
            evidence.append(f"+bounty:{label}")
            quotes.append(m.group(0))
            log.info(f"[CLF] signal {label}: '{m.group(0)}'")
            
    for pattern, weight, label in BOUNTY_NEGATIVE:
        m = re.search(pattern, t, re.IGNORECASE)
        if m:
            bounty_score += weight
            evidence.append(f"-bounty:{label}")
            quotes.append(m.group(0))
            log.info(f"[CLF] signal {label}: '{m.group(0)}'")
            
    for pattern, weight, label in VDP_POSITIVE:
        m = re.search(pattern, t, re.IGNORECASE)
        if m:
            vdp_score += weight
            evidence.append(f"+vdp:{label}")
            
    for pattern, weight, label in THIRD_PARTY_NEGATIVE:
        m = re.search(pattern, t, re.IGNORECASE)
        if m:
            third_party_score += weight
            evidence.append(f"-3p:{label}")

    effective_bounty = bounty_score + third_party_score
    
    # Classify
    offers_money = None
    if effective_bounty >= 3:
        offers_money = True
    elif vdp_score >= 1 and effective_bounty < 3:
        offers_money = False
    elif vdp_score >= 1 and bounty_score >= 3:
        offers_money = True
        
    # Confidence
    if offers_money is True:
        confidence = "high" if effective_bounty >= 5 else "medium" if effective_bounty >= 3 else "low"
    elif offers_money is False:
        confidence = "high" if vdp_score >= 3 else "medium" if vdp_score >= 1 else "low"
    else:
        confidence = "low"

    return {
        "offers_money": offers_money,
        "confidence": confidence,
        "evidence": " | ".join(evidence) or "none",
        "evidence_quote": " | ".join(quotes[:3]) if quotes else None,
    }