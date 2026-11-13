"""Pure helpers: time bounds, SQL placeholders, text assembly.

No I/O here so the logic stays unit-testable.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TimeBoundError(ValueError):
    """A --from/--to value could not be parsed."""


def parse_time_bound(value: str, *, end_of_day: bool = False) -> datetime:
    """Parse a CLI time bound into a naive datetime.

    Accepts ``YYYY-MM-DD``, ``YYYY-MM-DD HH:MM[:SS]``, and ISO-8601 with a
    ``T`` separator or a UTC offset. Aware values are converted to UTC.
    Naive values are assumed to be UTC.

    ``end_of_day=True`` rolls a date-only upper bound to the last instant of
    that day, so ``--to 2024-01-31`` is inclusive of January 31st.
    """
    raw = (value or "").strip()
    if not raw:
        raise TimeBoundError("empty time bound (expected e.g. 2024-01-31 or '2024-01-31 18:00')")

    dt: Optional[datetime] = None
    if _DATE_ONLY.match(raw):
        dt = datetime.strptime(raw, "%Y-%m-%d")
        if end_of_day:
            dt = dt + timedelta(hours=23, minutes=59, seconds=59, microseconds=999999)
    else:
        normalized = raw.replace(" ", "T", 1)
        if normalized.endswith(("Z", "z")):
            normalized = normalized[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            raise TimeBoundError(
                f"unrecognized time bound: {value!r} "
                "(expected 2024-01-31, 2024-01-31 18:00, or full ISO-8601)"
            ) from None

    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def normalize_timestamp(value) -> Optional[datetime]:
    """Best-effort conversion of a msgvault timestamp cell to a naive UTC datetime.

    Handles ISO-8601 strings (with or without ``Z``/offset) and epoch
    seconds. Returns None when the value is missing or unparseable.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return parse_time_bound(str(value))
    except TimeBoundError:
        return None


def sql_string_literal(dt: datetime) -> str:
    """Render a datetime as a single-quoted SQL literal for DuckDB."""
    return "'" + dt.isoformat(sep=" ") + "'"


def substitute_placeholders(sql: str, from_dt: Optional[datetime], to_dt: Optional[datetime]) -> str:
    """Substitute ``{from}`` / ``{to}`` with an ISO datetime literal.

    If the placeholder is already wrapped in single quotes in the SQL
    (``sent_at >= '{from}'``), only its value is replaced; otherwise a quoted
    literal is emitted so the SQL stays valid (``sent_at >= {from}``).
    """
    for token, dt in (("{from}", from_dt), ("{to}", to_dt)):
        if dt is None:
            continue
        value = sql_string_literal(dt)
        quoted_token = f"'{token}'"
        if quoted_token in sql:
            sql = sql.replace(quoted_token, value)
        sql = sql.replace(token, value)
    return sql


def has_time_placeholder(sql: str) -> bool:
    return "{from}" in sql or "{to}" in sql


def build_text(row: dict, columns: Iterable[str], max_chars: int = 2000) -> str:
    """Join the selected columns of a row into one text for Gist to tag."""
    parts: List[str] = []
    for name in columns:
        value = row.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            parts.append(text)
    joined = "\n".join(parts)
    if max_chars and len(joined) > max_chars:
        joined = joined[:max_chars]
    return joined


def within_time_range(value, from_dt: Optional[datetime], to_dt: Optional[datetime]) -> Optional[bool]:
    """Check a raw timestamp cell against the bounds.

    Returns None when the value cannot be parsed or no bound applies, so the
    caller keeps the row instead of silently dropping data.
    """
    if from_dt is None and to_dt is None:
        return True
    dt = normalize_timestamp(value)
    if dt is None:
        return None
    if from_dt is not None and dt < from_dt:
        return False
    if to_dt is not None and dt > to_dt:
        return False
    return True
