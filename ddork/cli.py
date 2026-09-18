"""Command-line entry point."""
import argparse
import asyncio as aio

from .analyzer import run_workflow
from .competitors.enumerate import enumerate_domains
from .config import configure_logging, log
from .net import normalize_domain, GlobalRateLimiter
from .report import LABELS, print_report, save_urls
from .ui import ScanUI


def build_parser():
    p = argparse.ArgumentParser(
        description="Discover competitor domains and classify their bug-bounty/VDP policy."
    )
    p.add_argument("-u", help="seed URL/domain to enumerate competitors from")
    p.add_argument("-f", help="file of domains to analyze (one per line), skips enumeration")
    p.add_argument(
        "-o", nargs="+", metavar=("FILE", "LABEL"),
        help="save findings to FILE as TSV. Optional LABEL filters to one of "
             f"({'|'.join(LABELS)}).",
    )
    p.add_argument("-n", "--min-targets", type=int, default=None,
                   help="stop enumeration once this many total targets are found")
    p.add_argument("-w", type=int, default=8, help="worker concurrency (default: 8)")
    p.add_argument("--delay", type=float, default=0.1,
                   help="min seconds between requests for provider calls (default: 0.1)")
    p.add_argument("--min-conf", type=float, default=None,
                   help="minimum confidence for -o output (within-label; do not use as "
                        "a global filter — NOT_PROGRAM always scores 0.9)")
    p.add_argument("--store", default=None,
                   help="SQLite path for cross-run seen/new tracking")
    p.add_argument("--new-only", action="store_true",
                   help="show only findings first seen this run (requires --store)")
    p.add_argument("--all", action="store_true",
                   help="include NOT_PROGRAM in the stdout report")
    p.add_argument(
        "--enumerate-only", nargs="?", const="targets.txt", default=None, metavar="FILE",
        help="stop after enumeration, save discovered domains to FILE and exit",
    )
    p.add_argument("--debug", action="store_true",
                   help="raw log output instead of the live progress UI")
    return p


async def main():
    args = build_parser().parse_args()
    configure_logging(args.debug)

    label_filter = None
    if args.o and len(args.o) > 2:
        return log.error(f"-o takes at most FILE and LABEL, got: {args.o}")
    if args.o and len(args.o) == 2:
        label_filter = args.o[1].upper()
        if label_filter not in LABELS:
            return log.error(
                f"Invalid label '{label_filter}', expected one of {LABELS}"
            )

    if args.new_only and not args.store:
        return log.error("--new-only requires --store")

    ui = ScanUI() if not args.debug else None
    if ui:
        ui.__enter__()

    store = None
    try:
        if args.f:
            with open(args.f, encoding="utf-8") as fh:
                domains = [normalize_domain(l.strip()) for l in fh if l.strip()]
        elif args.u:
            if not args.min_targets:
                return log.error(
                    "Need -n/--min-targets with -u (pool expansion has no depth cap)"
                )
            domains = enumerate_domains(
                args.u, args.min_targets, args.w, args.delay, ui=ui
            )
        else:
            return log.error("Need -u or -f")

        domains = [d for d in domains if d]
        if not domains:
            return log.error("No valid domains")

        if args.enumerate_only:
            with open(args.enumerate_only, "w", encoding="utf-8") as fh:
                fh.write("\n".join(domains) + "\n")
            log.info(f"Enumerated {len(domains)} targets, saved to {args.enumerate_only}")
            return

        results = await run_workflow(domains, args.w, ui=ui)

        if args.store:
            from .store import Store
            store = Store(args.store)
            run_id = store.record(results, seed=args.u)
            if args.new_only:
                results = store.filter_new(results, run_id)
    finally:
        if ui:
            ui.__exit__(None, None, None)
        if store:
            store.close()

    print_report(results, show_all=args.all)

    if args.o:
        count = save_urls(
            results, args.o[0],
            label_filter=label_filter, min_conf=args.min_conf,
        )
        extras = []
        if label_filter:
            extras.append(f"label={label_filter}")
        if args.min_conf is not None:
            extras.append(f"min_conf={args.min_conf}")
        log.info(
            f"Saved {count} row(s) to {args.o[0]}"
            + (f" ({', '.join(extras)})" if extras else "")
        )


def run():
    aio.run(main())