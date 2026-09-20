import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "outputs/scale_4500_final"

# scale_4500_final is a generated local evidence bundle and is intentionally
# ignored by Git.  Keep the real-data contract checks when that bundle exists,
# while allowing a clean GitHub checkout to run the normal offline test suite.
pytestmark = pytest.mark.skipif(
    not FINAL.exists(),
    reason="generated scale_4500_final artifacts are not part of the source checkout",
)


def _json(path):
    return json.loads((FINAL / path).read_text(encoding="utf-8"))


def test_frozen_manifest_and_product_master_contract():
    manifest = _json("manifest/manifest_4500_frozen.json")
    master = _json("master/products_4500_master.json")["records"]
    manifest_asins = [r["asin"] for r in manifest["records"]]
    master_asins = [r["asin"] for r in master]
    assert len(manifest_asins) == 4500
    assert len(set(manifest_asins)) == 4500
    assert len(master) == 4500
    assert len(set(master_asins)) == 4500
    assert set(master_asins) == set(manifest_asins)


def test_category_quota_and_repeated_ranking_evidence_are_preserved():
    summary = _json("audit/product_master_summary.json")
    assert summary["category_count"] == 15
    assert set(summary["per_category_counts"].values()) == {300}
    assert summary["raw_ranking_records"] == 9529
    assert summary["raw_ranking_unique_asin"] == 7365
    assert summary["ranking_evidence"]["rows_for_frozen_4500"] > 4500
    assert summary["ranking_evidence"]["multi_ranking_asins"] > 0


def test_translation_queue_contains_only_nonempty_spanish_sources():
    queue = FINAL / "translation/translation_queue_4500.jsonl"
    rows = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert all(str(row["source_text"]).strip() for row in rows)
    assert all("_zh" not in row["field_name"] for row in rows)
    assert all(row["translation_schema_version"] == "pretranslation-queue-v1" for row in rows)


def test_build_declares_offline_safety_and_no_memory_seed_translation():
    summary = _json("audit/product_master_summary.json")
    assert summary["safety"]["network_requests"] == 0
    assert summary["safety"]["no_captcha_bypass"] == "PASS"
    assert (FINAL / "translation/translation_memory_seed.jsonl").read_text(encoding="utf-8") == ""
