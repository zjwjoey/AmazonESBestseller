"""Versioned, recoverable JSON checkpoints for production runs.

The collector historically rewrote a single JSON state file.  This module is
the production-safe replacement: every successful save produces an immutable
version, a validated ``latest`` copy and an independently recoverable
``last_good`` copy.  It is deliberately JSON-only so it remains usable on the
Windows hosts used for long-running collection jobs.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping


class CheckpointRecoveryError(RuntimeError):
    """Raised when neither current nor last-known-good state is valid."""


class VersionedCheckpointStore:
    """Atomically persist one named mapping with version and recovery copies."""

    def __init__(self, directory: str | Path, name: str) -> None:
        self.directory = Path(directory)
        self.name = str(name)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.name):
            raise ValueError("invalid checkpoint name")

    @property
    def latest_path(self) -> Path:
        return self.directory / f"{self.name}.latest.json"

    @property
    def last_good_path(self) -> Path:
        return self.directory / f"{self.name}.last_good.json"

    def save(self, state: Mapping[str, Any]) -> Path:
        """Write and validate a new immutable version before updating pointers."""
        payload = dict(state)
        self.directory.mkdir(parents=True, exist_ok=True)
        version = self._next_version()
        version_path = self.directory / f"{self.name}.{version:06d}.json"
        self._atomic_json(version_path, payload)
        # Validate the durable version before it can be referenced by recovery.
        self._load_json(version_path)
        self._atomic_json(self.last_good_path, payload)
        self._atomic_json(self.latest_path, payload)
        return version_path

    def load(self) -> dict[str, Any]:
        """Load latest state, recovering from last-good only when necessary."""
        latest_error: Exception | None = None
        try:
            return self._load_json(self.latest_path)
        except (OSError, ValueError) as exc:
            latest_error = exc
        try:
            return self._load_json(self.last_good_path)
        except (OSError, ValueError) as exc:
            raise CheckpointRecoveryError(
                f"CHECKPOINT_UNRECOVERABLE:{self.name}:latest={latest_error};last_good={exc}"
            ) from exc

    def _next_version(self) -> int:
        pattern = re.compile(rf"^{re.escape(self.name)}\.(\d{{6}})\.json$")
        versions = [int(match.group(1)) for path in self.directory.glob(f"{self.name}.*.json")
                    if (match := pattern.match(path.name))]
        return max(versions, default=0) + 1

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"checkpoint must be a JSON object: {path}")
        return value

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
        encoded = (json.dumps(dict(payload), ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n").encode("utf-8")
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            # ``replace`` is atomic on supported Windows filesystems.  Failure
            # leaves the prior latest/last-good file intact rather than writing
            # an unvalidated partial JSON document in place.
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = ["CheckpointRecoveryError", "VersionedCheckpointStore"]
