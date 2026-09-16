"""Console report rendering and file output.

One list, tag column. Sort: PAID_BB → VDP → NOT_PROGRAM, then confidence desc within tag.
"""
import sys

LABEL_ORDER = {"PAID_BB": 0, "VDP": 1, "NOT_PROGRAM": 2}
LABELS = tuple(LABEL_ORDER.keys())


def _flatten(results):
    rows = []
    for r in results:
        rows.extend(r.get("findings", []))
    return rows


def _sort_key(f):
    return (LABEL_ORDER.get(f["label"], 9), -f["confidence"], f["domain"])


def print_report(results, show_all=False):
    rows = _flatten(results)
    if not show_all:
        rows = [f for f in rows if f["label"] != "NOT_PROGRAM"]
    rows.sort(key=_sort_key)

    total_found = len(_flatten(results))
    paid = sum(1 for f in _flatten(results) if f["label"] == "PAID_BB")
    vdp = sum(1 for f in _flatten(results) if f["label"] == "VDP")

    print(f"\n{'=' * 120}")
    print(f"Findings: {total_found} total  |  PAID_BB: {paid}  |  VDP: {vdp}"
          f"  |  hidden NOT_PROGRAM: {total_found - paid - vdp}")
    print(f"{'=' * 120}")
    print(f"{'domain':<28} {'tag':<10} {'conf':<6} {'src':<8} {'url':<44} why")
    print(f"{'-' * 120}")
    for f in rows:
        why = (f.get("decision_path") or "")[:40]
        url = f.get("url") or ""
        if len(url) > 42:
            url = url[:39] + "..."
        print(f"{f['domain']:<28} {f['label']:<10} {f['confidence']:<6.2f} "
              f"{(f.get('source') or ''):<8} {url:<44} {why}")
    print(f"{'=' * 120}\n")


def save_urls(results, path, label_filter=None, min_conf=None):
    """Write findings as TSV: domain\turl\tlabel\tconfidence\tsource\tdecision_path.
    `label_filter` in {PAID_BB,VDP,NOT_PROGRAM}; `min_conf` filters by float confidence."""
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