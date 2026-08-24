from pathlib import Path

from amazon_es_bestseller.run_store import RunStore


def test_run_store_creates_required_artifact_folders(tmp_path: Path):
    store = RunStore.create(tmp_path, "20260824_120000")
    for name in ("html", "screenshots", "raw", "failures", "parsed", "logs"):
        assert (store.root / name).is_dir()
