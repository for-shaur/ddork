"""Command-line entry point."""
import argparse
import asyncio as aio

from .analyzer import run_workflow
from .classifier import classifier, print_preflight_error
from .competitors.enumerate import enumerate_domains
from .config import configure_logging, log
from .net import normalize_domain
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
    p.add_argument("-w", type=int, default=5, help="worker concurrency (default: 5)")
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
    p.add_argument("-v", "--verbose", type=int, default=1, choices=(0, 1, 2, 3),
                   metavar="LEVEL",
                   help="0=quiet · 1=normal (default) · 2=verbose · 3=debug "
                        "(level 3 disables the live UI, streams log to stdout)")
    p.add_argument("--debug", action="store_true",
                   help="alias for -v 3")
    p.add_argument("--update", action="store_true",
                   help="update the isbounty classifier via pip and exit")
    p.add_argument("--no-banner", action="store_true",
                   help="skip the startup banner (logo + version lines)")
    p.add_argument(
        "--enumerate-only", nargs="?", const="targets.txt", default=None, metavar="FILE",
        help="stop after enumeration, save discovered domains to FILE and exit. "
             "Does not require the classifier.",
    )
    return p


async def main():
    args = build_parser().parse_args()
    verbose = 3 if args.debug else args.verbose
    configure_logging(verbose)

    # --update short-circuits everything else — it exists precisely to fix
    # a missing classifier, so it must not require one.
    if args.update:
        from .banner import update_classifier
        return 0 if update_classifier() else 1

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

    # Banner first so the user always sees version info, including the
    # "(not installed)" tag on isbounty when it's missing — it sets up the
    # error that follows.
    if verbose >= 1 and not args.no_banner:
        from .banner import print_banner
        print_banner(verbose)

    # Classifier preflight. Skip it in --enumerate-only mode: enumeration
    # does not need isbounty. Everything else does, so fail here with an
    # actionable message instead of deep inside the async workflow.
    if not args.enumerate_only:
        ok, reason, _ = classifier.preflight()
        if not ok:
            print_preflight_error(reason)
            return 2

    ui = ScanUI(verbose) if 1 <= verbose <= 2 else None
    if ui:
        ui.__enter__()

    store = None
    try:
        if args.f:
            with open(args.f, encoding="utf-8") as fh:
                domains = [normalize_domain(l.strip()) for l in fh if l.strip()]
            src = f"file {args.f}"
        elif args.u:
            if not args.min_targets:
                return log.error(
                    "Need -n/--min-targets with -u (pool expansion has no depth cap)"
                )
            if ui:
                ui.checkpoint(f"seeding from {args.u}, target {args.min_targets}")
            domains = enumerate_domains(
                args.u, args.min_targets, args.w, args.delay, ui=ui
            )
            src = f"seed {args.u}"
        else:
            return log.error("Need -u or -f")

        domains = [d for d in domains if d]
        if not domains:
            return log.error("No valid domains")

        if ui and args.f:
            ui.checkpoint(f"loaded {len(domains)} domain(s) from {src}")

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