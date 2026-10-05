import json

import pytest

from amazon_es_bestseller.orchestration.translation_batch import (
    TranslationBatchError,
    create_selection_manifest,
    load_selection_manifest,
    write_selection_manifest,
)


def _master(path, asins):
    path.write_text(json.dumps({"master": {"artifact_hash": "master-content-hash",
                                            "records": [{"asin": asin} for asin in asins]}}), encoding="utf-8")
    return path


def test_selection_manifest_binds_exact_parent_artifact_and_asin_subset(tmp_path):
    master = _master(tmp_path / "spanish-master.json", ["B000000001", "B000000002"])
    manifest = create_selection_manifest(master_artifact_path=master, selected_asins=["B000000002"],
                                         selection_id="batch-01")
    selection = write_selection_manifest(tmp_path / "selection.json", manifest)
    loaded = load_selection_manifest(selection,
                                     parent_artifact_hash=manifest["parent_spanish_master_artifact_hash"],
                                     parent_content_hash="master-content-hash",
                                     available_asins=["B000000001", "B000000002"])
    assert loaded["selected_asins"] == ["B000000002"]

    with pytest.raises(TranslationBatchError, match="PARENT_HASH_MISMATCH"):
        load_selection_manifest(selection, parent_artifact_hash="changed", parent_content_hash="master-content-hash",
                                available_asins=["B000000001", "B000000002"])


def test_selection_manifest_rejects_more_than_1500_unique_asins_before_provider_setup(tmp_path):
    master = _master(tmp_path / "spanish-master.json", ["B000000001"])
    too_many = ["B%09d" % number for number in range(1501)]
    with pytest.raises(TranslationBatchError, match="ASIN_CAP_EXCEEDED"):
        create_selection_manifest(master_artifact_path=master, selected_asins=too_many)
