"""Console report rendering and file output.

Layout: one block per finding, grouped by label.

    PAID_BB
    ──────────────────────────────────────────────────────────────────
    acme.com                          conf 0.94   sec.txt
      https://acme.com/security
      bug_bounty_signal → PAID_BB_VERIFIED

URLs are never truncated — they sit on their own line, wrapping if the
terminal is narrow. Hidden NOT_PROGRAM findings are still counted in the
header.
"""
from .colors import palette

LABEL_ORDER = {"PAID_BB": 0, "VDP": 1, "NOT_PROGRAM": 2}
LABELS = tuple(LABEL_ORDER.keys())

# Section header color per label.
_LABEL_COLOR = {
    "PAID_BB":     "bright_green",
    "VDP":         "bright_cyan",
    "NOT_PROGRAM": "grey",
}

# Finding-row domain column width (visible chars). Long domains overflow
# gracefully — conf/source shift right rather than truncating.
_DOMAIN_W = 42
_RULE_W = 72


def _flatten(results):
    rows = []
    for r in results:
        rows.extend(r.get("findings", []))
    return rows


def _sort_key(f):
    return (LABEL_ORDER.get(f["label"], 9), -f["confidence"], f["domain"])


def _conf_color(conf):
    if conf >= 0.90:
        return "bright_green"
    if conf >= 0.70:
        return "bright_yellow"
    return "grey"


def _conf_text(conf):
    s = f"{conf:.2f}"
    return getattr(palette, _conf_color(conf))(s)


def _print_header(total, paid, vdp, hidden):
    rule = "═" * _RULE_W
    print(palette.bold(rule))
    stats = (
        f"  {palette.bold(str(total))} findings   "
        f"{palette.dim('·')}   "
        f"{palette.bright_green('PAID_BB')} {paid}   "
        f"{palette.bright_cyan('VDP')} {vdp}   "
        f"{palette.grey('NOT_PROGRAM')} {hidden} {palette.dim('(hidden)')}"
    )
    print(stats)
    print(palette.bold(rule))
    print()


def _print_group(label, rows):
    color = _LABEL_COLOR.get(label, "bold")
    header = getattr(palette, color)(f"  {label}")
    print(header)
    print(palette.grey("  " + "─" * (_RULE_W - 2)))
    for f in rows:
        _print_finding(f)
    print()


def _print_finding(f):
    domain = palette.bold(f["domain"])
    conf = _conf_text(f["confidence"])
    source = palette.grey(f.get("source") or "")

    # Line 1: domain, padded to a fixed visible width. If longer, let it
    # overflow — format spec width is a minimum, not a maximum.
    pad = " " * max(1, _DOMAIN_W - len(f["domain"]))
    print(f"  {domain}{pad}{palette.dim('conf')} {conf}   {source}")

    # Line 2: full URL, never truncated.
    url = f.get("url") or ""
    if url:
        print(f"    {palette.bright_blue(url)}")

    # Line 3: decision path, if present.
    why = (f.get("decision_path") or "").strip()
    if why:
        print(f"    {palette.dim(why)}")


def print_report(results, show_all=False):
    all_rows = _flatten(results)
    rows = all_rows if show_all else [f for f in all_rows if f["label"] != "NOT_PROGRAM"]
    rows.sort(key=_sort_key)

    total = len(all_rows)
    paid = sum(1 for f in all_rows if f["label"] == "PAID_BB")
    vdp = sum(1 for f in all_rows if f["label"] == "VDP")
    hidden = total - paid - vdp

    _print_header(total, paid, vdp, hidden)

    if not rows:
        print(palette.dim("  (nothing to show)"))
        print()
        return

    # Group in label order, preserving confidence-desc within each group.
    by_label = {}
    for f in rows:
        by_label.setdefault(f["label"], []).append(f)
    for label in sorted(by_label, key=lambda L: LABEL_ORDER.get(L, 9)):
        _print_group(label, by_label[label])

    print(palette.bold("═" * _RULE_W))
    print()


def save_urls(results, path, label_filter=None, min_conf=None):
    """Write findings as TSV: domain\turl\tlabel\tconfidence\tsource\tdecision_path."""
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("domain\turl\tlabel\tconfidence\tsource\tdecision_path\n")
        for f in sorted(_flatten(results), key=_sort_key):
            if label_filter and f["label"] != label_filter:
                continue
            if min_conf is not None and f["confidence"] < min_conf:
                continue
            fh.write(
                f"{f['domain']}\t{f.get('url', '')}\t{f['label']}\t"
                f"{f['confidence']:.2f}\t{f.get('source', '')}\t"
                f"{(f.get('decision_path') or '').replace(chr(9), ' ')}\n"
            )
            n += 1
    return n