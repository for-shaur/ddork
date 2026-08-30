"""Console report rendering and file output."""

WIDTH = 130
CONFIDENCE_LEVELS = ("high", "medium", "low")


def print_report(results):
    print(f"\n{'=' * WIDTH}\n{'Domain':<25} {'Src':<8} {'Policy':<60} {'Money':<7} {'Conf':<6} {'Quote'}\n{'=' * WIDTH}")
    for r in sorted(results, key=lambda x: str(x["url"])):
        b = r.get("bounty") or {}
        money = "PAID" if b.get("offers_money") else "VDP" if b.get("offers_money") is False else "NONE" if not r["url"] else "?"
        print(
            f"{r['domain']:<25} {r['source'] or 'none':<8} {(r['url'] or ''):<60} "
            f"{money:<7} {b.get('confidence', 'n/a'):<6} {(b.get('evidence_quote') or '')[:30]}"
        )
    print("=" * WIDTH)


def save_urls(results, path, confidence=None):
    """Write discovered policy/bounty URLs to `path`, one per line as `domain\\turl\\tconfidence`.
    `confidence` (high|medium|low) filters to that level; None (default) keeps all levels."""
    rows = [
        (r["domain"], r["url"], (r.get("bounty") or {}).get("confidence", "n/a"))
        for r in sorted(results, key=lambda x: str(x["url"]))
        if r["url"] and (confidence is None or (r.get("bounty") or {}).get("confidence") == confidence)
    ]
    with open(path, "w", encoding="utf-8") as fh:
        for domain, url, conf in rows:
            fh.write(f"{domain}\t{url}\t{conf}\n")
    return len(rows)