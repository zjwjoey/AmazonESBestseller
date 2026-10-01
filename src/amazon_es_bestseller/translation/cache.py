"""Field-level translation cache with atomic writes and safe recovery."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


class TranslationCache:
    VERSION = 1

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.recovered_from_corruption = False
        self.corruption_error: Optional[str] = None
        self.load()

    @staticmethod
    def key(asin: str, field: str, source_hash: str, provider: str,
            model: str, schema_version: str, prompt_version: str) -> str:
        return "|".join((asin.upper(), field, source_hash, provider, model,
                          schema_version, prompt_version))

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = dict(data.get("entries", data)) if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError) as exc:
            self.recovered_from_corruption = True
            self.corruption_error = str(exc)
            # Preserve the bad evidence; never delete or silently overwrite it.
            stamp = str(int(time.time()))
            corrupt = self.path.with_name(self.path.name + ".corrupt-" + stamp)
            try:
                self.path.replace(corrupt)
            except OSError:
                pass
            self.entries = {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        value = self.entries.get(key)
        return dict(value) if isinstance(value, dict) else None

    def put(self, key: str, value: Dict[str, Any]) -> None:
        self.entries[key] = dict(value)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cache_version": self.VERSION, "entries": self.entries}
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".tmp-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def __len__(self) -> int:
        return len(self.entries)
