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
import time
from pathlib import Path
from typing import Any, Mapping


class CheckpointRecoveryError(RuntimeError):
    """Raised when neither current nor last-known-good state is valid."""


class VersionedCheckpointStore:
    """Atomically persist one named mapping with version and recovery copies."""

    def __init__(self, directory: str | Path, name: str) -> None:
        self.directory = Path(directory)
        self.name = str(name)
        self._last_save_warning: dict[str, str] | None = None
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.name):
            raise ValueError("invalid checkpoint name")

    @property
    def latest_path(self) -> Path:
        return self.directory / f"{self.name}.latest.json"

    @property
    def last_good_path(self) -> Path:
        return self.directory / f"{self.name}.last_good.json"

    @property
    def last_save_warning(self) -> Mapping[str, str] | None:
        """Non-fatal post-commit backup warning from the most recent save.

        ``latest`` is the commit point. A locked recovery copy therefore must
        not make a caller retry a state transition that has already committed.
        """
        return self._last_save_warning

    def save(self, state: Mapping[str, Any]) -> Path:
        """Write and validate a new immutable version before updating pointers."""
        self._last_save_warning = None
        payload = dict(state)
        self.directory.mkdir(parents=True, exist_ok=True)
        version = self._next_version()
        version_path = self.directory / f"{self.name}.{version:06d}.json"
        self._atomic_json(version_path, payload)
        # Validate the durable version before it can be referenced by recovery.
        self._load_json(version_path)
        self._atomic_json(self.latest_path, payload)
        # latest is the commit point.  Do not advance the recovery pointer
        # until the current pointer is durably visible as the same state.
        try:
            self._atomic_json(self.last_good_path, payload)
        except OSError as exc:
            # The new state is already committed through ``latest`` and the
            # immutable version. Preserve that truthful outcome, expose an
            # observable warning, and leave the staged temporary evidence for
            # an operator/retry instead of risking a direct overwrite.
            self._last_save_warning = {
                "code": "CHECKPOINT_LAST_GOOD_BACKUP_FAILED",
                "path": str(self.last_good_path),
                "error": str(exc),
            }
        return version_path

    def load_or_migrate(self, *, legacy_path: str | Path | None = None,
                        default: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Load versioned state, migrating one valid legacy JSON object once.

        An absent store is a normal first-run condition.  A malformed existing
        state is not: callers must fail closed rather than silently restart a
        long-running collection task from an empty scheduler state.
        """
        if self.latest_path.exists() or self.last_good_path.exists():
            return self.load()
        if legacy_path is not None:
            legacy = Path(legacy_path)
            if legacy.exists():
                try:
                    value = self._load_json(legacy)
                except (OSError, ValueError) as exc:
                    raise CheckpointRecoveryError(
                        f"CHECKPOINT_UNRECOVERABLE:{self.name}:legacy={exc}"
                    ) from exc
                self.save(value)
                return value
        return dict(default or {})

    def load(self) -> dict[str, Any]:
        """Load latest state, then immutable versions, then last-good state."""
        latest_error: Exception | None = None
        try:
            return self._load_json(self.latest_path)
        except (OSError, ValueError) as exc:
            latest_error = exc
        version_error: Exception | None = None
        try:
            return self._load_newest_version()
        except (OSError, ValueError) as exc:
            version_error = exc
            pass
        try:
            return self._load_json(self.last_good_path)
        except (OSError, ValueError) as exc:
            raise CheckpointRecoveryError(
                f"CHECKPOINT_UNRECOVERABLE:{self.name}:latest={latest_error};"
                f"versions={version_error};last_good={exc}"
            ) from exc

    def _load_newest_version(self) -> dict[str, Any]:
        pattern = re.compile(rf"^{re.escape(self.name)}\.(\d{{6}})\.json$")
        versions = sorted(
            (path for path in self.directory.glob(f"{self.name}.*.json")
             if pattern.match(path.name)),
            reverse=True,
        )
        if not versions:
            raise FileNotFoundError(f"no immutable checkpoint versions for {self.name}")
        errors: list[Exception] = []
        for path in versions:
            try:
                return self._load_json(path)
            except (OSError, ValueError) as exc:
                errors.append(exc)
        raise ValueError(f"all immutable versions invalid for {self.name}: {errors}")

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
        atomic_write_json(path, dict(payload), sort_keys=True)


def atomic_write_json(path: str | Path, payload: Any, *, sort_keys: bool = False) -> None:
    """Write verified JSON through fsync + replace, never direct-overwrite.

    Windows readers can transiently deny a rename.  Retrying preserves the
    last complete target; falling back to ``open(path, 'w')`` would risk a torn
    scheduler checkpoint, so an exhausted retry surfaces PermissionError and
    leaves the uniquely named staged evidence in place for recovery.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2,
                          sort_keys=sort_keys) + "\n").encode("utf-8")
    # Validate before mutating any visible checkpoint path.
    json.loads(encoded.decode("utf-8"))
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        last_error: PermissionError | None = None
        for delay in (0.0, 0.05, 0.15, 0.35, 0.75):
            try:
                os.replace(temporary, target)
                replaced = True
                return
            except PermissionError as exc:
                last_error = exc
                if delay:
                    time.sleep(delay)
        if last_error is not None:
            raise last_error
    finally:
        if replaced:
            temporary.unlink(missing_ok=True)


__all__ = ["CheckpointRecoveryError", "VersionedCheckpointStore", "atomic_write_json"]
