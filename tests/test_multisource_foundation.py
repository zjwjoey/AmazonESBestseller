import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from amazon_es_bestseller.multisource.contracts import (
    SOURCE_BUDGETS, PRODUCTION_CATEGORY_QUOTAS, observation_id, timestamp)
from amazon_es_bestseller.multisource.storage import (
    atomic_json, immutable_bytes, file_lock, sealed, read_sealed)


def test_utc_and_spain_date_across_dst_boundary():
    assert timestamp("2026-07-01T22:30:00Z")["local_date"] == "2026-07-02"
    assert timestamp("2026-12-01T22:30:00Z")["local_date"] == "2026-12-01"
    with pytest.raises(ValueError):
        timestamp("2026-07-01T22:30:00")


def test_observation_key_is_slot_not_asin_and_budget_contract():
    key = observation_id("snapshot", "source", "https://www.amazon.es/gp/bestsellers?pg=1", 0)
    assert key == observation_id("snapshot", "source", "https://www.amazon.es/gp/bestsellers?pg=1", 0)
    assert key != observation_id("snapshot", "source", "https://www.amazon.es/gp/bestsellers?pg=2", 0)
    assert sum(SOURCE_BUDGETS.values()) == 30000
    assert sum(PRODUCTION_CATEGORY_QUOTAS.values()) == 5500


def test_immutable_publication_rejects_replacement_and_racing_writer(tmp_path):
    path = tmp_path / "html"
    def publish(data):
        try:
            return immutable_bytes(path, data)
        except ValueError:
            return "conflict"
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(publish, [b"first", b"second"]))
    assert sorted(map(str, outcomes)) == ["True", "conflict"]
    data = path.read_bytes()
    assert immutable_bytes(path, data) is False
    with pytest.raises(ValueError):
        immutable_bytes(path, b"different")
    assert path.read_bytes() == data


def test_corrupt_checkpoint_recovers_last_verified_backup(tmp_path):
    path = tmp_path / "checkpoint.json"
    atomic_json(path, sealed({"completed": 1}))
    atomic_json(path, sealed({"completed": 2}), backup=True)
    path.write_text('{"corrupt":', encoding="utf8")
    assert read_sealed(path, recover_backup=True) == {"completed": 1}
    atomic_json(path, sealed({"completed": 3}), backup=True)
    path.write_text("broken", encoding="utf8")
    assert read_sealed(path, recover_backup=True) == {"completed": 1}
    with pytest.raises(ValueError):
        read_sealed(path)


def test_process_crash_releases_lock_without_stale_pid_cleanup(tmp_path):
    lock = tmp_path / "run.lock"
    script = ("from amazon_es_bestseller.multisource.storage import file_lock; "
              "import os; "
              f"context=file_lock({str(lock)!r}); context.__enter__(); os._exit(0)")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / "src"),
               PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, "-c", script], env=env, check=True)
    with file_lock(lock):
        pass


def test_unknown_schema_or_checksum_is_not_silently_accepted(tmp_path):
    path = tmp_path / "state.json"
    value = sealed({"entities": []})
    value["schema_version"] = 99
    atomic_json(path, value)
    with pytest.raises(ValueError):
        read_sealed(path)
