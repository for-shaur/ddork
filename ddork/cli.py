"""Command-line entry point."""
import argparse
import asyncio as aio

from .analyzer import run_workflow
from .competitors.enumerate import enumerate_domains
from .config import configure_logging, log
from .net import normalize_domain
from .report import CONFIDENCE_LEVELS, print_report, save_urls
#from .selftest import run_selftest
from .ui import ScanUI


def build_parser():
    p = argparse.ArgumentParser(description="Discover competitor domains and classify their bug-bounty/VDP policy.")
    p.add_argument("-u", help="seed URL/domain to enumerate competitors from")
    p.add_argument("-f", help="file of domains to analyze (one per line), skips enumeration")
    p.add_argument(
        "-o", nargs="+", metavar=("FILE", "CONF"),
        help="save discovered URLs to FILE as 'domain<TAB>url<TAB>confidence' lines. "
             f"Optional CONF filters to one confidence level ({'|'.join(CONFIDENCE_LEVELS)}); default: all",
    )
    p.add_argument("-d", type=int, default=1, help="competitor enumeration depth (default: 1)")
    p.add_argument("-w", type=int, default=8, help="worker concurrency (default: 8)")
    p.add_argument("--delay", type=float, default=0.5, help="min seconds between requests, enforced by the adaptive limiter (default: 0.5)")
    p.add_argument("--selftest", action="store_true", help="run the regression test suite and exit")
    p.add_argument("--debug", action="store_true", help="raw log output instead of the live progress UI")
    return p


async def main():
    args = build_parser().parse_args()
    configure_logging(args.debug)

    if args.selftest:
        return run_selftest()

    conf_filter = None
    if args.o:
        if len(args.o) > 2:
            return log.error(f"-o takes at most FILE and CONF, got: {args.o}")
        if len(args.o) == 2:
            conf_filter = args.o[1].lower()
            if conf_filter not in CONFIDENCE_LEVELS:
                return log.error(f"Invalid confidence filter '{conf_filter}', expected one of {CONFIDENCE_LEVELS}")

    # one UI session spans enumeration + probing, so enumeration's checkpoints stay
    # visible (above the rule) while the probing live line runs below them
    ui = ScanUI() if not args.debug else None
    if ui:
        ui.__enter__()
    try:
        if args.f:
            with open(args.f, encoding="utf-8") as fh:
                domains = [normalize_domain(l.strip()) for l in fh if l.strip()]
        elif args.u:
            domains = enumerate_domains(args.u, args.d, args.w, args.delay, ui=ui)
        else:
            return log.error("Need -u or -f")

        domains = [d for d in domains if d]  # drops blanks/Nones so a stray line doesn't burn a request on "https://None/..."
        if not domains:
            return log.error("No valid domains")

        results = await run_workflow(domains, args.w, ui=ui)
    finally:
        if ui:
            ui.__exit__(None, None, None)

    print_report(results)

    if args.o:
        count = save_urls(results, args.o[0], conf_filter)
        log.info(f"Saved {count} URL(s) to {args.o[0]}" + (f" (confidence={conf_filter})" if conf_filter else ""))


def run():
    aio.run(main())