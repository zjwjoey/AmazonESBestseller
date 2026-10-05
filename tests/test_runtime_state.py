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

    with pytest.raises(CheckpointRecoveryError, match="CHECKPOINT_UNRECOVERABLE"):
        store.load()
