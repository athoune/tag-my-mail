"""Tests for the CLI wiring: default SQL fallback and time-bound defaults."""

import io
import sys
import unittest
from datetime import date
from unittest import mock

from tag_my_mail.cli import DEFAULT_SQL, build_parser, parse_bounds, resolve_sql


class FakeStdin:
    def __init__(self, text: str, tty: bool):
        self._text = text
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty

    def read(self) -> str:
        return self._text


def make_args(argv):
    return build_parser().parse_args(argv)


class ParseBoundsTests(unittest.TestCase):
    def test_no_bounds(self):
        self.assertEqual(parse_bounds(make_args([])), (None, None))

    def test_from_without_to_ends_today(self):
        from_dt, to_dt = parse_bounds(make_args(["--from", "2026-11-10"]))
        self.assertEqual(from_dt.date(), date(2026, 11, 10))
        self.assertEqual(to_dt.date(), date.today())
        self.assertEqual((to_dt.hour, to_dt.minute, to_dt.second), (23, 59, 59))

    def test_explicit_to_is_kept(self):
        _, to_dt = parse_bounds(make_args(["--from", "2026-11-10", "--to", "2026-11-12"]))
        self.assertEqual(to_dt.date(), date(2026, 11, 12))

    def test_to_without_from_is_kept(self):
        from_dt, to_dt = parse_bounds(make_args(["--to", "2026-11-12"]))
        self.assertIsNone(from_dt)
        self.assertEqual(to_dt.date(), date(2026, 11, 12))

    def test_from_after_to_raises(self):
        with self.assertRaises(ValueError):
            parse_bounds(make_args(["--from", "2026-11-12", "--to", "2026-11-10"]))


class ResolveSqlTests(unittest.TestCase):
    def test_default_query_when_no_sql(self):
        notice = io.StringIO()
        with mock.patch("sys.stdin", FakeStdin("", tty=True)):
            sql = resolve_sql(make_args([]), notice=notice)
        self.assertEqual(sql, DEFAULT_SQL)
        self.assertIn("v_messages", sql)
        self.assertIn("SPAM", sql)
        self.assertIn("default query", notice.getvalue())

    def test_default_query_when_piped_stdin_is_empty(self):
        with mock.patch("sys.stdin", FakeStdin("\n", tty=False)):
            sql = resolve_sql(make_args([]), notice=io.StringIO())
        self.assertEqual(sql, DEFAULT_SQL)

    def test_piped_stdin_wins_over_default(self):
        with mock.patch("sys.stdin", FakeStdin("SELECT 1\n", tty=False)):
            sql = resolve_sql(make_args([]), notice=io.StringIO())
        self.assertEqual(sql, "SELECT 1")

    def test_explicit_sql_wins(self):
        with mock.patch("sys.stdin", FakeStdin("SELECT 1", tty=False)):
            sql = resolve_sql(make_args(["--sql", "SELECT 2"]), notice=io.StringIO())
        self.assertEqual(sql, "SELECT 2")

    def test_dash_reads_stdin(self):
        with mock.patch("sys.stdin", FakeStdin("SELECT 3\n", tty=False)):
            sql = resolve_sql(make_args(["--sql", "-"]), notice=io.StringIO())
        self.assertEqual(sql, "SELECT 3")

    def test_empty_explicit_sql_is_an_error(self):
        with self.assertRaises(SystemExit):
            resolve_sql(make_args(["--sql", "   "]), notice=io.StringIO())


if __name__ == "__main__":
    unittest.main()
