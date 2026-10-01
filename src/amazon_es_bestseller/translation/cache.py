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
        self.memory: Dict[str, Dict[str, Any]] = {}
        self.recovered_from_corruption = False
        self.corruption_error: Optional[str] = None
        self._corruption_preserved = False
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
            if isinstance(data, dict) and "entries" in data:
                self.entries = dict(data.get("entries") or {})
                self.memory = dict(data.get("memory") or {})
            else:
                # Backward-compatible V1 cache shape: old records remain
                # field entries; no implicit migration is attempted.
                self.entries = dict(data) if isinstance(data, dict) else {}
                self.memory = {}
        except (OSError, ValueError, TypeError) as exc:
            self.recovered_from_corruption = True
            self.corruption_error = str(exc)
            # Preserve the bad evidence; never delete or silently overwrite it.
            stamp = str(time.time_ns())
            corrupt = self.path.with_name(self.path.name + ".corrupt-" + stamp)
            try:
                self.path.replace(corrupt)
                self._corruption_preserved = True
            except OSError:
                # Do not later overwrite a malformed cache if its evidence
                # could not be moved out of the way.
                self._corruption_preserved = False
            self.entries = {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        value = self.entries.get(key)
        return dict(value) if isinstance(value, dict) else None

    def put(self, key: str, value: Dict[str, Any]) -> None:
        self.entries[key] = dict(value)

    @staticmethod
    def memory_key(source_text: str, source_language: str, target_language: str,
                   field_type: str, provider: str, model: str,
                   schema_version: str, prompt_version: str) -> str:
        import hashlib
        digest = hashlib.sha256(str(source_text or "").encode("utf-8")).hexdigest()
        return "|".join((digest, source_language, target_language, field_type,
                          provider, model, schema_version, prompt_version))

    def get_memory(self, key: str) -> Optional[Dict[str, Any]]:
        value = self.memory.get(key)
        return dict(value) if isinstance(value, dict) else None

    def put_memory(self, key: str, value: Dict[str, Any]) -> None:
        self.memory[key] = dict(value)

    def save(self) -> None:
        if self.recovered_from_corruption and not self._corruption_preserved and self.path.exists():
            raise RuntimeError("corrupt translation cache could not be preserved: %s" % self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cache_version": self.VERSION, "entries": self.entries,
                   "memory": self.memory}
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
