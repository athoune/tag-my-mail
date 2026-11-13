"""Command-line entry point: msgvault query -> Gist tags -> JSONL/CSV."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date
from typing import Dict, List, Optional, TextIO

from . import __version__
from .core import (
    build_text,
    has_time_placeholder,
    parse_time_bound,
    substitute_placeholders,
    within_time_range,
)
from .gist import GistError, GistTagger
from .msgvault import MsgvaultError, run_query

DEFAULT_TEXT_COLUMNS = "subject,snippet"
DEFAULT_CACHE = ".tag-my-mail.cache.json"

# Used when no --sql/--sql-file/stdin SQL is provided: recent messages with
# the fields Gist needs, minus what Gmail filed as SPAM (the label exists in
# the analytics views; both counts agree with total minus 967 spam rows).
# The LIMIT keeps an accidental no-args run bounded; it is printed on stderr
# so nothing is silently truncated.
DEFAULT_SQL = (
    "SELECT id, subject, snippet, sent_at, from_email "
    "FROM v_messages "
    "WHERE NOT list_contains(labels::VARCHAR[], 'SPAM') "
    "ORDER BY sent_at DESC LIMIT 1000"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tag-my-mail",
        description=(
            "Run a read-only SQL query against msgvault's analytics cache and "
            "tag each resulting message with the Gist topic model (via `da gist`). "
            "Results are written as JSONL or CSV."
        ),
        epilog=(
            "Time range: pass --from/--to and use {from}/{to} placeholders in the SQL "
            "(push-down into DuckDB). When the query selects a sent_at column "
            "(--time-column), rows are also filtered client-side as a safety net. "
            "--from without --to ends the range at today. "
            "Without any SQL, a default query over v_messages is used (shown on stderr)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sql_group = parser.add_argument_group("query")
    sql_group.add_argument("--sql", help="SQL to run against msgvault ('-' reads stdin)")
    sql_group.add_argument("--sql-file", help="Read the SQL from a file")
    sql_group.add_argument(
        "--fresh",
        action="store_true",
        help="Forward --fresh to msgvault query (wait for analytics to include recent writes)",
    )

    time_group = parser.add_argument_group("time range")
    time_group.add_argument("--from", dest="time_from", metavar="DATE", help="Lower bound, e.g. 2024-01-31 or '2024-01-31 18:00'")
    time_group.add_argument("--to", dest="time_to", metavar="DATE", help="Upper bound, inclusive; date-only means end of day. Defaults to today when --from is given")
    time_group.add_argument("--time-column", default="sent_at", help="Column used for client-side range filtering")

    tag_group = parser.add_argument_group("tagging")
    tag_group.add_argument(
        "--text-columns",
        default=DEFAULT_TEXT_COLUMNS,
        help="Comma-separated row columns to feed to Gist, in order",
    )
    tag_group.add_argument("--top", type=int, default=3, help="Number of topics per message")
    tag_group.add_argument("--max-chars", type=int, default=2000, help="Truncate the tagging text to this many characters")
    tag_group.add_argument("--max-rows", type=int, default=0, help="Stop after tagging N rows (0 = no limit)")
    tag_group.add_argument("--da", default="da", help="desertant binary name or path")
    tag_group.add_argument("--msgvault", default="msgvault", help="msgvault binary name or path")

    out_group = parser.add_argument_group("output")
    out_group.add_argument("--out", help="Write results to this file (default: stdout)")
    out_group.add_argument("--format", choices=("jsonl", "csv"), default="jsonl", help="Output format")
    out_group.add_argument("--no-text", action="store_true", help="Do not include the tagged text in each record")
    out_group.add_argument("--cache", default=DEFAULT_CACHE, help="Tag cache file")
    out_group.add_argument("--no-cache", action="store_true", help="Disable the tag cache")

    parser.add_argument("--dry-run", action="store_true", help="Show the final SQL and row preview, tag nothing")
    parser.add_argument("--quiet", "-q", action="store_true", help="No per-row progress on stderr")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print query details on stderr")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def resolve_sql(args: argparse.Namespace, notice: Optional[TextIO] = None) -> str:
    notice = sys.stderr if notice is None else notice
    if args.sql is not None and args.sql_file:
        raise SystemExit("error: use either --sql or --sql-file, not both")

    if args.sql_file:
        try:
            with open(args.sql_file, "r", encoding="utf-8") as fh:
                sql = fh.read()
        except OSError as exc:
            raise SystemExit(f"error: cannot read SQL file: {exc}") from None
        sql = sql.strip()
        if not sql:
            raise SystemExit(f"error: empty SQL file: {args.sql_file}")
        return sql

    if args.sql == "-":
        sql = sys.stdin.read().strip()
        if not sql:
            raise SystemExit("error: empty SQL on stdin")
        return sql

    if args.sql is not None:
        sql = args.sql.strip()
        if not sql:
            raise SystemExit("error: empty SQL")
        return sql

    # No SQL given: piped stdin still counts as SQL, otherwise fall back to
    # the default query over v_messages.
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            return piped

    print(
        "[tag-my-mail] no --sql given; using the default query:\n"
        f"  {DEFAULT_SQL}",
        file=notice,
    )
    return DEFAULT_SQL


def parse_bounds(args: argparse.Namespace):
    from_dt = parse_time_bound(args.time_from) if args.time_from else None
    if args.time_to:
        to_dt = parse_time_bound(args.time_to, end_of_day=True)
    elif from_dt:
        # --from alone means "up to now": bound the range by today, inclusive.
        to_dt = parse_time_bound(date.today().isoformat(), end_of_day=True)
    else:
        to_dt = None
    if from_dt and to_dt and from_dt > to_dt:
        raise ValueError("--from is after --to")
    return from_dt, to_dt


def row_to_dict(columns: List[str], row) -> Dict:
    # Duplicate column names: the last one wins, matching dict semantics.
    return {name: (row[i] if i < len(row) else None) for i, name in enumerate(columns)}


def print_preview(columns: List[str], rows: List, out: TextIO) -> None:
    print(f"columns: {', '.join(columns)}", file=out)
    print(f"rows: {len(rows)}", file=out)
    for row in rows[:3]:
        print(f"  {json.dumps(row_to_dict(columns, row), ensure_ascii=False)[:300]}", file=out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        from_dt, to_dt = parse_bounds(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if from_dt is not None and not args.time_to:
        print(
            f"[tag-my-mail] no --to given; range ends today ({to_dt.date()}, inclusive)",
            file=sys.stderr,
        )

    sql = resolve_sql(args)
    # Check the original SQL for placeholders before substituting them away.
    original_had_placeholder = has_time_placeholder(sql) if (from_dt or to_dt) else False
    sql = substitute_placeholders(sql, from_dt, to_dt)

    text_columns = [c.strip() for c in args.text_columns.split(",") if c.strip()]
    if not text_columns:
        print("error: --text-columns is empty", file=sys.stderr)
        return 2

    if args.verbose or args.dry_run:
        print(f"[tag-my-mail] SQL:\n{sql}", file=sys.stderr)

    try:
        columns, rows = run_query(sql, msgvault_bin=args.msgvault, fresh=args.fresh)
    except MsgvaultError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.verbose or args.dry_run:
        print_preview(columns, rows, sys.stderr)

    missing = [c for c in text_columns if c not in columns]
    if missing:
        available = ", ".join(columns)
        print(
            f"error: text column(s) not in query result: {', '.join(missing)}\navailable: {available}",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        return 0

    if (from_dt or to_dt) and args.time_column not in columns and not original_had_placeholder:
        print(
            f"warning: --from/--to given but the query selects no {args.time_column!r} "
            "column and has no {from}/{to} placeholder; the time range is NOT applied",
            file=sys.stderr,
        )

    tagger = GistTagger(
        da_bin=args.da,
        top=args.top,
        cache_path=None if args.no_cache else args.cache,
    )

    out_fh: Optional[TextIO] = None
    out_target = sys.stdout
    writer = None
    stats = {"tagged": 0, "out_of_range": 0, "empty": 0, "errors": 0, "kept_unparsed_time": 0}

    try:
        if args.out:
            out_fh = open(args.out, "w", encoding="utf-8", newline="")
            out_target = out_fh

        extra_fields = ([] if args.no_text else ["text"]) + ["tags", "error"]
        csv_fields = list(columns)
        for field in extra_fields:
            while field in csv_fields:
                field += "_"
            csv_fields.append(field)
        if args.format == "csv":
            writer = csv.DictWriter(out_target, fieldnames=csv_fields, extrasaction="ignore")
            writer.writeheader()

        started = time.monotonic()
        considered = 0
        total = len(rows)

        for index, raw_row in enumerate(rows, start=1):
            if args.max_rows and considered >= args.max_rows:
                break
            row = row_to_dict(columns, raw_row)

            if from_dt or to_dt:
                verdict = within_time_range(row.get(args.time_column), from_dt, to_dt)
                if verdict is False:
                    stats["out_of_range"] += 1
                    continue
                if verdict is None and row.get(args.time_column) is not None:
                    stats["kept_unparsed_time"] += 1

            considered += 1
            text = build_text(row, text_columns, max_chars=args.max_chars)
            if not text:
                stats["empty"] += 1
                continue

            error_msg = ""
            try:
                topics = tagger.tag(text)
            except GistError as exc:
                topics = []
                stats["errors"] += 1
                error_msg = str(exc)

            stats["tagged"] += 1
            record = {"row": row}
            if not args.no_text:
                record["text"] = text
            record["tags"] = topics
            if error_msg:
                record["error"] = error_msg

            if writer is not None:
                flat = dict(row)
                if not args.no_text:
                    flat["text"] = text
                flat["tags"] = json.dumps(topics, ensure_ascii=False)
                flat["error"] = error_msg
                writer.writerow(flat)
            else:
                out_target.write(json.dumps(record, ensure_ascii=False) + "\n")
                out_target.flush()

            if not args.quiet:
                subject = str(row.get("subject") or text)[:80]
                n = len(topics)
                suffix = f" ERROR: {error_msg}" if error_msg else ""
                print(f"[{index}/{total}] {n} topics  {subject}{suffix}", file=sys.stderr)

        elapsed = time.monotonic() - started
        tagger.save()

        summary = (
            f"done: {stats['tagged']} tagged, {stats['empty']} empty, "
            f"{stats['out_of_range']} out of range, {stats['errors']} errors, "
            f"cache {tagger.hits} hits / {tagger.misses} misses, {elapsed:.1f}s"
        )
        if stats["kept_unparsed_time"]:
            summary += f", {stats['kept_unparsed_time']} rows with unparseable {args.time_column} kept"
        print(summary, file=sys.stderr)
        if args.out:
            print(f"written: {args.out}", file=sys.stderr)

        return 1 if stats["errors"] else 0

    except KeyboardInterrupt:
        tagger.save()
        print("\ninterrupted (partial output kept; cache saved)", file=sys.stderr)
        return 130
    finally:
        if out_fh is not None:
            out_fh.close()
