"""Console report rendering and file output.

Layout: one entry per finding, grouped under a label header.

    PAID_BB
      acme.com                                              sec.txt
        https://acme.com/security
        bug_bounty_signal → PAID_BB_VERIFIED

The source tag is right-aligned on the domain line in muted grey. URLs are
never truncated; they sit on their own line. Hidden NOT_PROGRAM findings are
still counted in the header.

Color roles: labels use success/brand/muted, URLs use link, decision path
and source are muted.
"""
from .colors import palette

LABEL_ORDER = {"PAID_BB": 0, "VDP": 1, "NOT_PROGRAM": 2}
LABELS = tuple(LABEL_ORDER.keys())

_LABEL_COLOR = {
    "PAID_BB":     "success",
    "VDP":         "brand",
    "NOT_PROGRAM": "muted",
}

_WIDTH = 72


def _flatten(results):
    rows = []
    for r in results:
        rows.extend(r.get("findings", []))
    return rows


def _sort_key(f):
    return (LABEL_ORDER.get(f["label"], 9), -f["confidence"], f["domain"])


def _print_header(total, paid, vdp, hidden):
    print(
        f"  {palette.bold(str(total))} findings   "
        f"{palette.muted('·')}   "
        f"{palette.success('PAID_BB')} {paid}   "
        f"{palette.brand('VDP')} {vdp}   "
        f"{palette.muted('NOT_PROGRAM')} {hidden} {palette.muted('(hidden)')}"
    )
    print()


def _print_group(label, rows):
    role = _LABEL_COLOR.get(label, "neutral")
    print(getattr(palette, role)(f"  {label}"))
    for f in rows:
        print()
        _print_finding(f)


def _print_finding(f):
    domain = f["domain"]
    source = f.get("source") or ""
    left = f"  {domain}"

    if source:
        pad = " " * max(1, _WIDTH - len(left) - len(source))
        print(f"{palette.bold(left)}{pad}{palette.muted(source)}")
    else:
        print(palette.bold(left))

    url = f.get("url") or ""
    if url:
        print(f"    {palette.link(url)}")

    why = (f.get("decision_path") or "").strip()
    if why:
        print(f"    {palette.muted(why)}")


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
        print(palette.muted("  (nothing to show)"))
        print()
        return

    by_label = {}
    for f in rows:
        by_label.setdefault(f["label"], []).append(f)
    for label in sorted(by_label, key=lambda L: LABEL_ORDER.get(L, 9)):
        _print_group(label, by_label[label])


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