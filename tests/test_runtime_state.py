from __future__ import annotations

import pytest

from amazon_es_bestseller.runtime_state import (
    CheckpointRecoveryError,
    VersionedCheckpointStore,
)


def test_versioned_checkpoint_retains_last_good_and_recovers_malformed_latest(tmp_path):
    store = VersionedCheckpointStore(tmp_path / "state", "category_state")
    first = store.save({"run": 1})
    second = store.save({"run": 2})

    assert first.name == "category_state.000001.json"
    assert second.name == "category_state.000002.json"
    assert store.load() == {"run": 2}

    store.latest_path.write_text("{not json", encoding="utf-8")
    assert store.load() == {"run": 2}


def test_versioned_checkpoint_rejects_unrecoverable_state(tmp_path):
    store = VersionedCheckpointStore(tmp_path / "state", "run")
    store.save({"ok": True})
    store.latest_path.write_text("[]", encoding="utf-8")
    store.last_good_path.write_text("broken", encoding="utf-8")
    for path in store.directory.glob("run.??????.json"):
        path.write_text("broken", encoding="utf-8")

    with pytest.raises(CheckpointRecoveryError, match="CHECKPOINT_UNRECOVERABLE"):
        store.load()


def test_checkpoint_latest_commit_survives_permanently_locked_last_good(monkeypatch, tmp_path):
    store = VersionedCheckpointStore(tmp_path / "state", "run")
    store.save({"run": 1})
    import amazon_es_bestseller.runtime_state as runtime_state

    original_write = runtime_state.atomic_write_json

    def deny_last_good(path, payload, *, sort_keys=False):
        if path == store.last_good_path:
            raise PermissionError(13, "last-good remains locked")
        return original_write(path, payload, sort_keys=sort_keys)

    monkeypatch.setattr(runtime_state, "atomic_write_json", deny_last_good)
    committed = store.save({"run": 2})

    assert committed.name == "run.000002.json"
    assert store.last_save_warning and store.last_save_warning["code"] == "CHECKPOINT_LAST_GOOD_BACKUP_FAILED"
    assert store.load() == {"run": 2}
    store.latest_path.write_text("broken", encoding="utf-8")
    # The immutable version is the recovery path while last_good remains
    # permanently locked at the older state.
    assert store.load() == {"run": 2}


def test_versioned_checkpoint_migrates_legacy_and_never_overwrites_good_state_on_permission_error(monkeypatch, tmp_path):
    legacy = tmp_path / "batch_state_v2.json"
    legacy.write_text('{"run_status": "RUNNING"}', encoding="utf-8")
    store = VersionedCheckpointStore(tmp_path / "checkpoints", "batch_state_v2")
    assert store.load_or_migrate(legacy_path=legacy) == {"run_status": "RUNNING"}
    assert store.latest_path.exists() and store.last_good_path.exists()

    original_replace = __import__("os").replace

    def deny_latest(source, destination):
        if destination == store.latest_path:
            raise PermissionError(13, "locked latest")
        return original_replace(source, destination)

    monkeypatch.setattr("amazon_es_bestseller.runtime_state.os.replace", deny_latest)
    with pytest.raises(PermissionError):
        store.save({"run_status": "COMPLETE"})
    assert store.load() == {"run_status": "RUNNING"}
