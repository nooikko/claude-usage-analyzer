"""Command-line interface — argument parsing and main entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from . import __version__
from .analyzer import analyze_projects
from .formatters import FORMATS, get_formatter
from .utils import parse_timestamp


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-usage",
        description="Analyze Claude Code JSONL session data for token cost attribution.",
    )
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")

    # Filtering
    p.add_argument(
        "--projects-dir",
        default=os.path.expanduser("~/.claude/projects"),
        help="Path to Claude projects directory (default: ~/.claude/projects)",
    )
    p.add_argument("--project", default=None,
                   help="Filter to a specific project (substring match)")

    # Date range
    p.add_argument("--since", default=None,
                   help="Only include records after this date (YYYY-MM-DD or ISO datetime)")
    p.add_argument("--until", default=None,
                   help="Only include records before this date (YYYY-MM-DD or ISO datetime)")
    p.add_argument("--days", type=int, default=None,
                   help="Shorthand: only include the last N days (e.g. --days 30)")

    # Output
    p.add_argument("--top", type=int, default=20,
                   help="Number of top items to show per section (default: 20)")
    p.add_argument("-f", "--format", choices=FORMATS, default="table",
                   help="Output format (default: table)")
    p.add_argument("-o", "--output", default=None,
                   help="Output file path (for non-table formats)")
    p.add_argument("--daily", action="store_true",
                   help="Show a per-day breakdown for a single project (use with --project)")

    # Logging
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable verbose (DEBUG) logging")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress informational output")

    return p


def _resolve_dates(args) -> tuple[datetime | None, datetime | None]:
    since_dt = None
    until_dt = None

    if args.days is not None:
        since_dt = datetime.now(timezone.utc) - timedelta(days=args.days)
    if args.since:
        s = args.since
        if len(s) == 10:
            s += "T00:00:00+00:00"
        since_dt = parse_timestamp(s)
    if args.until:
        u = args.until
        if len(u) == 10:
            u += "T23:59:59+00:00"
        until_dt = parse_timestamp(u)

    return since_dt, until_dt


def _setup_logging(verbose: bool, quiet: bool):
    level = logging.WARNING
    if verbose:
        level = logging.DEBUG
    elif not quiet:
        level = logging.INFO

    logging.basicConfig(
        format="%(levelname)-8s %(name)s  %(message)s",
        level=level,
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    _setup_logging(args.verbose, args.quiet)
    logger = logging.getLogger("claude_usage_analyzer")

    since_dt, until_dt = _resolve_dates(args)

    date_label = ""
    if since_dt:
        date_label += f" since {since_dt.strftime('%Y-%m-%d')}"
    if until_dt:
        date_label += f" until {until_dt.strftime('%Y-%m-%d')}"

    logger.info("Scanning %s ...%s", args.projects_dir, date_label)
    if not args.quiet:
        print(f"Scanning {args.projects_dir}...{date_label}", file=sys.stderr)

    stats = analyze_projects(args.projects_dir, args.project, since_dt, until_dt)

    if args.daily:
        from .formatters.table import write_daily_report
        write_daily_report(stats, args.project, args.top)
    else:
        write_report, _ = get_formatter(args.format)
        write_report(stats, args.output, args.top)
