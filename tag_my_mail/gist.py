"""Tag texts with the Gist topic model through the desertant CLI (`da gist`)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from typing import Dict, List, Optional


class GistError(RuntimeError):
    """The desertant CLI failed or returned something unparseable."""


class GistTagger:
    """One Gist call per text, with a small on-disk cache.

    The cache key is sha256(top + text), so raising --top invalidates entries
    instead of serving stale, shorter topic lists.
    """

    def __init__(self, da_bin: str = "da", top: int = 3, cache_path: Optional[str] = None):
        self.da_bin = da_bin
        self.top = top
        self.cache_path = cache_path
        self._cache: Dict[str, List[dict]] = {}
        self.hits = 0
        self.misses = 0
        self._dirty = 0
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self._cache = {k: v for k, v in loaded.items() if isinstance(v, list)}
            except (OSError, json.JSONDecodeError):
                # A broken cache must never block a run; it gets rewritten.
                self._cache = {}

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.top}\x00{text}".encode("utf-8")).hexdigest()

    def tag(self, text: str) -> List[dict]:
        """Return topics best-first: [{slug, name, score}, ...]."""
        text = text.strip()
        if not text:
            return []

        key = self._key(text)
        if key in self._cache:
            self.hits += 1
            return self._cache[key]

        try:
            proc = subprocess.run(
                [self.da_bin, "gist", "--json", "--top", str(self.top)],
                input=text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            raise GistError(
                f"desertant binary not found: {self.da_bin!r} "
                "(install with: brew install desert-ant-labs/tap/desertant)"
            ) from None

        if proc.returncode != 0:
            detail = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
            raise GistError(f"da gist failed: {detail}")

        try:
            topics = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise GistError(f"da gist returned invalid JSON: {exc}") from exc
        if not isinstance(topics, list):
            raise GistError(f"da gist returned unexpected JSON: {proc.stdout[:200]!r}")

        normalized = [
            {
                "slug": str(t.get("slug", "")),
                "name": str(t.get("name", "")),
                "score": float(t.get("score", 0.0)),
            }
            for t in topics
            if isinstance(t, dict)
        ]

        self.misses += 1
        self._cache[key] = normalized
        self._dirty += 1
        if self.cache_path and self._dirty >= 25:
            self.save()
        return normalized

    def save(self) -> None:
        if not self.cache_path or self._dirty == 0:
            return
        directory = os.path.dirname(os.path.abspath(self.cache_path))
        os.makedirs(directory, exist_ok=True)
        tmp_path = self.cache_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(self._cache, fh, ensure_ascii=False)
        os.replace(tmp_path, self.cache_path)
        self._dirty = 0
