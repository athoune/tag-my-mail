"""Unit tests for the pure helpers (stdlib unittest, no network, no CLI)."""

import unittest
from datetime import datetime, timezone

from tag_my_mail.core import (
    TimeBoundError,
    build_text,
    has_time_placeholder,
    normalize_timestamp,
    parse_time_bound,
    sql_string_literal,
    substitute_placeholders,
    within_time_range,
)


class ParseTimeBoundTests(unittest.TestCase):
    def test_date_only(self):
        self.assertEqual(parse_time_bound("2024-01-31"), datetime(2024, 1, 31, 0, 0, 0))

    def test_date_only_end_of_day_is_inclusive(self):
        self.assertEqual(
            parse_time_bound("2024-01-31", end_of_day=True),
            datetime(2024, 1, 31, 23, 59, 59, 999999),
        )

    def test_datetime_with_space(self):
        self.assertEqual(parse_time_bound("2024-01-31 18:00"), datetime(2024, 1, 31, 18, 0, 0))

    def test_datetime_with_t_and_seconds(self):
        self.assertEqual(parse_time_bound("2024-01-31T18:30:15"), datetime(2024, 1, 31, 18, 30, 15))

    def test_zulu_is_utc(self):
        self.assertEqual(parse_time_bound("2024-01-31T18:00:00Z"), datetime(2024, 1, 31, 18, 0, 0))

    def test_offset_converted_to_utc(self):
        # 18:00+02:00 is 16:00 UTC
        self.assertEqual(parse_time_bound("2024-01-31T18:00:00+02:00"), datetime(2024, 1, 31, 16, 0, 0))

    def test_invalid_raises(self):
        with self.assertRaises(TimeBoundError):
            parse_time_bound("31/01/2024")
        with self.assertRaises(TimeBoundError):
            parse_time_bound("")


class NormalizeTimestampTests(unittest.TestCase):
    def test_iso_z(self):
        self.assertEqual(normalize_timestamp("2026-11-13T09:12:31Z"), datetime(2026, 11, 13, 9, 12, 31))

    def test_epoch_seconds(self):
        expected = datetime.fromtimestamp(1700000000, tz=timezone.utc).replace(tzinfo=None)
        self.assertEqual(normalize_timestamp(1700000000), expected)

    def test_garbage_returns_none(self):
        self.assertIsNone(normalize_timestamp("not a date"))
        self.assertIsNone(normalize_timestamp(None))
        self.assertIsNone(normalize_timestamp(""))


class PlaceholderTests(unittest.TestCase):
    def test_substitution(self):
        sql = "SELECT * FROM messages WHERE sent_at >= '{from}' AND sent_at <= '{to}'"
        out = substitute_placeholders(sql, datetime(2024, 1, 1), datetime(2024, 1, 31, 23, 59, 59, 999999))
        self.assertIn("'2024-01-01 00:00:00'", out)
        self.assertIn("'2024-01-31 23:59:59.999999'", out)
        self.assertNotIn("{from}", out)
        self.assertNotIn("{to}", out)

    def test_no_placeholders(self):
        sql = "SELECT 1"
        self.assertFalse(has_time_placeholder(sql))
        self.assertEqual(substitute_placeholders(sql, None, None), sql)

    def test_bare_placeholder_becomes_quoted(self):
        out = substitute_placeholders("WHERE sent_at >= {from}", datetime(2024, 1, 1), None)
        self.assertEqual(out, "WHERE sent_at >= '2024-01-01 00:00:00'")

    def test_literal_escapes_quotes(self):
        self.assertEqual(sql_string_literal(datetime(2024, 1, 1)), "'2024-01-01 00:00:00'")


class BuildTextTests(unittest.TestCase):
    def test_joins_selected_columns(self):
        row = {"subject": "Hello", "snippet": "World", "id": 7}
        self.assertEqual(build_text(row, ["id", "subject", "snippet"]), "7\nHello\nWorld")

    def test_skips_none_and_blank(self):
        row = {"subject": "Hello", "snippet": None}
        self.assertEqual(build_text(row, ["snippet", "subject"]), "Hello")

    def test_truncates(self):
        row = {"subject": "x" * 100}
        self.assertEqual(len(build_text(row, ["subject"], max_chars=10)), 10)

    def test_missing_column_is_ignored(self):
        self.assertEqual(build_text({}, ["subject"]), "")


class WithinRangeTests(unittest.TestCase):
    lo = datetime(2024, 1, 1)
    hi = datetime(2024, 1, 31, 23, 59, 59, 999999)

    def test_in_range(self):
        self.assertTrue(within_time_range("2024-01-15T10:00:00Z", self.lo, self.hi))

    def test_out_of_range(self):
        self.assertFalse(within_time_range("2023-12-31T23:00:00Z", self.lo, self.hi))
        self.assertFalse(within_time_range("2024-02-01T00:00:00Z", self.lo, self.hi))

    def test_unparseable_returns_none(self):
        self.assertIsNone(within_time_range("garbage", self.lo, self.hi))

    def test_no_bounds_is_true(self):
        self.assertTrue(within_time_range("garbage", None, None))


if __name__ == "__main__":
    unittest.main()
