"""Run read-only SQL against the msgvault analytics cache via its CLI."""

from __future__ import annotations

import json
import subprocess
from typing import List, Sequence, Tuple


class MsgvaultError(RuntimeError):
    """The msgvault CLI failed or returned something unparseable."""


def _extract_error(stderr: str, returncode: int) -> str:
    """Turn msgvault's stderr into one readable line.

    msgvault mixes Go log lines (``time=...``), an ``Error:`` block, and
    DuckDB's caret underlines; keep the substance, drop the decoration.
    """
    lines = []
    for line in (stderr or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("time="):
            continue
        if stripped.startswith("LINE ") or set(stripped) <= {"^"}:
            continue
        lines.append(stripped)
    if not lines:
        return f"exit code {returncode}"
    return " | ".join(lines)


def run_query(sql: str, msgvault_bin: str = "msgvault", fresh: bool = False) -> Tuple[List[str], List[Sequence]]:
    """Execute one read-only query and return (columns, rows).

    Rows are arrays aligned with ``columns``, as returned by
    ``msgvault query --format json``.
    """
    cmd = [msgvault_bin, "query", "--format", "json"]
    if fresh:
        cmd.append("--fresh")
    cmd.append(sql)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise MsgvaultError(
            f"msgvault binary not found: {msgvault_bin!r} (is msgvault installed and on PATH?)"
        ) from None

    if proc.returncode != 0:
        raise MsgvaultError(f"msgvault query failed: {_extract_error(proc.stderr, proc.returncode)}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise MsgvaultError(f"msgvault query returned invalid JSON: {exc}") from exc

    columns = list(payload.get("columns") or [])
    rows = list(payload.get("rows") or [])
    return columns, rows
