"""Offline identity proofs and journal recovery; no live Amazon requests."""
from copy import deepcopy
import json

import pytest

from amazon_es_bestseller.multisource.identity import ProductIdentityRegistry, IdentityEvidenceResolver, normalize_product_url, identity_fingerprint
from amazon_es_bestseller.multisource.storage import digest

A = "B08J7PQGD7"
B = "B091BRJSRN"
PARENT = "B0HGLYVZ5G"


def observation(key="obs1", asin=A, **values):
    return {"observation_id": key, "raw_product_url": f"https://www.amazon.es/dp/{asin}?tag=tracking&th=1&psc=1",
            "asin_from_card": asin, "asin_observed": asin, "observed_at": "2026-10-12T08:00:00Z",
            "identity_evidence": [], **values}


def proof(tmp_path, asin=A, **values):
    path = tmp_path / f"detail_{asin}.html"
    html = f'<html><input id="ASIN" name="ASIN" value="{asin}"><link rel="canonical" href="https://www.amazon.es/dp/{asin}"></html>'
    path.write_text(html, encoding="utf-8")
    return {"kind": "DETAIL_PAGE", "asin": asin, "evidence_path": str(path), "evidence_sha256": digest(path.read_bytes()),
            "requested_url": f"https://www.amazon.es/dp/{asin}", "final_url": f"https://www.amazon.es/dp/{asin}",
            "observed_at": "2026-10-12T08:05:00Z", "access_status": "NORMAL", **values}


def confirmed(tmp_path, key="obs1", asin=A, **values):
    return observation(key, asin, identity_evidence=[proof(tmp_path, asin)], **values)


def test_raw_url_and_reconstructed_canonical_not_independent(tmp_path):
    registry = ProductIdentityRegistry(tmp_path / "registry")
    row = registry.ingest(observation(canonical_asin_observed=A))[0]
    assert row["identity_status"] == "UNCONFIRMED"
    assert row["product_id"] is None
    assert not registry.legacy_mapping()
    assert normalize_product_url(row["raw_product_url"]) == f"https://www.amazon.es/dp/{A}?th=1&psc=1"


def test_independent_saved_detail_confirms_and_tracking_does_not_split(tmp_path):
    directory = tmp_path / "registry"
    registry = ProductIdentityRegistry(directory)
    one = confirmed(tmp_path)
    two = confirmed(tmp_path, "obs2", title_raw="Another translated title", source_type="MOVERS_SHAKERS")
    two["raw_product_url"] = f"https://www.amazon.es/dp/{A}?tag=other&psc=1&th=1"
    mapped = registry.ingest([one, two])
    assert all(r["identity_status"] == "CONFIRMED" for r in mapped)
    assert mapped[0]["product_id"] == mapped[1]["product_id"]
    assert ProductIdentityRegistry(directory).ingest(one)[0]["product_id"] == mapped[0]["product_id"]
    assert registry.summary()["confirmed_unique_products"] == 1
    assert registry.summary()["observations"] == 2
    assert registry.legacy_mapping() == {A: mapped[0]["product_id"]}


@pytest.mark.parametrize("fields", [
    {"asin_from_card": B}, {"asin_observed": B}, {"final_product_url": f"https://www.amazon.es/dp/{B}"},
    {"asin_from_raw_url": B}, {"canonical_asin_observed": B},
])
def test_conflicting_identity_hints_quarantined(tmp_path, fields):
    row = ProductIdentityRegistry(tmp_path / "registry").ingest(confirmed(tmp_path, **fields))[0]
    assert row["identity_status"] == "CONFLICT"
    assert row["product_id"] is None and row["identity_conflict_reason"]


@pytest.mark.parametrize("alteration", ["hash", "asin", "redirect", "metadata", "naive_time", "access", "foreign_host"])
def test_detail_proof_metadata_and_actual_page_are_verified(tmp_path, alteration):
    item = proof(tmp_path)
    if alteration == "hash":
        item["evidence_sha256"] = "0" * 64
    elif alteration == "asin":
        item["asin"] = B
    elif alteration == "redirect":
        item["final_url"] = f"https://www.amazon.es/dp/{B}"
    elif alteration == "metadata":
        del item["requested_url"]
    elif alteration == "naive_time":
        item["observed_at"] = "2026-10-12T08:00:00"
    elif alteration == "access":
        item["access_status"] = "BLOCKED"
    elif alteration == "foreign_host":
        item["final_url"] = f"https://www.amazon.es.evil.test/dp/{A}"
    row = IdentityEvidenceResolver().resolve(observation(identity_evidence=[item]))
    assert row["identity_status"] == "CONFLICT" and not row["product_id"]


def test_recommendation_asin_and_canonical_alone_not_detail_identity(tmp_path):
    item = proof(tmp_path)
    path = tmp_path / f"detail_{A}.html"
    path.write_text(f'<div id="recommendations" data-asin="{A}"></div><link rel="canonical" href="https://www.amazon.es/dp/{A}">', encoding="utf-8")
    item["evidence_sha256"] = digest(path.read_bytes())
    assert IdentityEvidenceResolver().resolve(observation(identity_evidence=[item]))["identity_status"] == "CONFLICT"


def test_ranking_and_detail_same_file_cannot_claim_two_independent_evidence(tmp_path):
    item = proof(tmp_path)
    row = observation(identity_evidence=[item], evidence_path=item["evidence_path"], evidence_sha256=item["evidence_sha256"])
    resolved = IdentityEvidenceResolver().resolve(row)
    assert "DETAIL_PROOF_NOT_INDEPENDENT_OF_RANKING_PAGE" in resolved["identity_conflict_reason"]


def test_foreign_raw_product_url_does_not_become_amazon_identity(tmp_path):
    row = confirmed(tmp_path)
    row["raw_product_url"] = f"https://evil.test/dp/{A}"
    assert IdentityEvidenceResolver().resolve(row)["identity_status"] == "CONFLICT"


def test_real_challenge_inside_saved_detail_blocks_confirmation(tmp_path):
    item = proof(tmp_path)
    path = tmp_path / f"detail_{A}.html"
    path.write_text(path.read_text() + "<div>Robot Check</div>", encoding="utf-8")
    item["evidence_sha256"] = digest(path.read_bytes())
    assert "DETAIL_PROOF_ACCESS_RESTRICTED" in IdentityEvidenceResolver().resolve(observation(identity_evidence=[item]))["identity_conflict_reason"]


def test_missing_asin_and_similar_titles_are_never_auto_merged(tmp_path):
    registry = ProductIdentityRegistry(tmp_path / "registry")
    rows = registry.ingest([
        {"observation_id": "empty", "title_raw": "Cable", "observed_at": "2026-10-12T08:00:00Z"},
        observation("hint1", A, title_raw="Cable", image_url_raw="same.jpg"),
        observation("hint2", B, title_raw="Cable", image_url_raw="same.jpg"),
    ])
    assert all(row["product_id"] is None for row in rows)
    assert registry.summary()["confirmed_unique_products"] == 0


def test_parent_family_and_different_variants_remain_two_products(tmp_path):
    registry = ProductIdentityRegistry(tmp_path / "registry")
    rows = registry.ingest([confirmed(tmp_path, "child1", A, parent_asin_observed=PARENT, selected_variant="Black"),
                            confirmed(tmp_path, "child2", B, parent_asin_observed=PARENT, selected_variant="Grey")])
    assert rows[0]["product_id"] != rows[1]["product_id"]
    registry.review("family", ["child1", "child2"], actor="reviewer", evidence="Saved variation-family selectors show both children")
    rows = registry.materialize()
    assert rows[0]["variation_family_id"] == rows[1]["variation_family_id"]
    assert len({r["product_id"] for r in rows}) == 2


def test_wrong_merge_undo_regenerates_all_mapping_and_stats_without_raw_changes(tmp_path):
    directory = tmp_path / "registry"
    registry = ProductIdentityRegistry(directory)
    initial = registry.ingest([confirmed(tmp_path, "a1", A), confirmed(tmp_path, "a2", A), confirmed(tmp_path, "b1", B)])
    initial_pids = {r["observation_id"]: r["product_id"] for r in initial}
    raw_before = {p.name: p.read_bytes() for p in (directory / "observations").glob("*.json")}
    event = registry.review("same", ["a1", "b1"], actor="human", evidence={"reason": "manual mistaken match"}, event_id="merge-one")
    assert registry.summary()["confirmed_unique_products"] == 1
    assert len({r["product_id"] for r in registry.materialize()}) == 1
    assert registry.legacy_mapping() == {}  # Conflicted legacy mapping cannot touch translation caches.
    restarted = ProductIdentityRegistry(directory)
    restarted.undo(event["event_id"], actor="human", evidence="Review corrected the mistaken merge")
    assert restarted.summary()["confirmed_unique_products"] == 2
    assert {r["observation_id"]: r["product_id"] for r in restarted.materialize()} == initial_pids
    assert {p.name: p.read_bytes() for p in (directory / "observations").glob("*.json")} == raw_before
    assert [e["action"] for e in restarted.history()] == ["INGEST", "same", "undo"]


def test_manual_pending_different_and_review_idempotency(tmp_path):
    registry = ProductIdentityRegistry(tmp_path / "registry")
    registry.ingest([observation("a"), observation("b")])
    event = registry.review("different", ["a", "b"], actor="user", evidence="The actual packages differ", event_id="split")
    assert registry.review("different", ["b", "a"], actor="user", evidence="The actual packages differ", event_id="split") == event
    assert registry.summary()["confirmed_unique_products"] == 2
    registry.review("pending", ["a"], actor="user", evidence="Needs a new proof")
    assert registry.summary()["confirmed_unique_products"] == 1
    with pytest.raises(ValueError, match="IDEMPOTENCY"):
        registry.review("same", ["a", "b"], actor="user", evidence="different action", event_id="split")
    with pytest.raises(ValueError, match="REVIEW_ACTOR"):
        registry.review("same", ["a", "b"], actor="", evidence="unspecified")


def test_legacy_import_never_defaults_to_trusted_but_explicit_proof_maps(tmp_path):
    registry = ProductIdentityRegistry(tmp_path / "registry")
    raw = {"asin": A, "title_es": "INIU"}
    assert registry.import_legacy([raw])[0]["product_id"] is None
    trusted = {"asin": B, "title_es": "Another"}
    row = registry.import_legacy([trusted], evidence_by_asin={B: [proof(tmp_path, B)]})[0]
    assert row["identity_status"] == "CONFIRMED"
    assert registry.legacy_mapping() == {B: row["product_id"]}


def test_raw_observation_overwrite_refused_and_checkpoint_rebuilds(tmp_path):
    directory = tmp_path / "registry"
    registry = ProductIdentityRegistry(directory)
    first = registry.ingest(confirmed(tmp_path))[0]
    with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_CONFLICT"):
        registry.ingest(observation())
    (directory / "checkpoint.json").write_text("broken", encoding="utf-8")
    assert ProductIdentityRegistry(directory).materialize()[0]["product_id"] == first["product_id"]


def test_tampered_or_missing_journal_and_raw_evidence_rejected(tmp_path):
    directory = tmp_path / "registry"
    registry = ProductIdentityRegistry(directory)
    registry.ingest(observation())
    path = next((directory / "observations").glob("*.json"))
    data = path.read_bytes()
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="OBSERVATION_CORRUPT"):
        registry.materialize()
    path.write_bytes(data)
    event_path = next((directory / "events").glob("*.json"))
    event_data = event_path.read_bytes()
    event = json.loads(event_data)
    event["action"] = "same"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(ValueError, match="JOURNAL_CORRUPT"):
        registry.materialize()
    event_path.write_bytes(event_data)
    event_path.unlink()
    with pytest.raises(ValueError, match="JOURNAL_TAIL_MISSING"):
        registry.materialize()


def test_orphan_raw_file_after_crash_not_counted_until_committed_ingest(tmp_path):
    from amazon_es_bestseller.multisource.storage import immutable_json
    directory = tmp_path / "registry"
    registry = ProductIdentityRegistry(directory)
    row = observation()
    immutable_json(directory / "observations" / (digest(row["observation_id"]) + ".json"), row)
    assert registry.summary()["observations"] == 0
    assert registry.ingest(row)[0]["observation_id"] == "obs1"
    assert registry.summary()["observations"] == 1


def test_post_capture_detail_proof_is_durable_and_raw_observation_unchanged(tmp_path):
    directory = tmp_path / "registry"
    registry = ProductIdentityRegistry(directory)
    registry.ingest(observation())
    raw = next((directory / "observations").glob("*.json"))
    original = raw.read_bytes()
    item = proof(tmp_path)
    row = registry.resolve("obs1", evidence=item, actor="review-run", event_id="proof1")
    assert row["identity_status"] == "CONFIRMED"
    assert registry.resolve("obs1", evidence=item, actor="review-run", event_id="proof1")["product_id"] == row["product_id"]
    assert raw.read_bytes() == original
    assert ProductIdentityRegistry(directory).resolve("obs1")["product_id"] == row["product_id"]
    registry.undo("proof1", actor="reviewer", evidence="Proof belonged to an outdated capture")
    assert registry.resolve("obs1")["product_id"] is None
    assert raw.read_bytes() == original


def test_post_capture_conflicting_detail_is_quarantined_and_excluded(tmp_path):
    registry = ProductIdentityRegistry(tmp_path / "registry")
    registry.ingest(observation())
    row = registry.resolve("obs1", evidence=proof(tmp_path, B))
    assert row["identity_status"] == "CONFLICT"
    assert registry.summary()["confirmed_unique_products"] == 0
    assert registry.legacy_mapping() == {}


def test_cli_attachment_reason_history_and_undo_survive_restart(tmp_path):
    directory = tmp_path / "registry"
    registry = ProductIdentityRegistry(directory)
    registry.ingest(observation())
    attached = registry.attach("obs1", [proof(tmp_path)], actor="operator", reason="Compare saved selected-child page", event_id="proof-cli")
    assert attached["event_id"] == "proof-cli"
    assert attached["observation"]["identity_status"] == "CONFIRMED"
    restarted = ProductIdentityRegistry(directory)
    assert restarted.history()[-1]["reason"] == "Compare saved selected-child page"
    restarted.undo(attached["event_id"], actor="operator", evidence="Retire an incorrect evidence attachment")
    assert restarted.resolve("obs1")["product_id"] is None


def test_detail_parent_page_or_conflicting_selected_child_never_confirms(tmp_path):
    row = observation(parent_asin_observed=PARENT, identity_evidence=[proof(tmp_path, PARENT)])
    assert IdentityEvidenceResolver().resolve(row)["identity_status"] == "CONFLICT"
    row = observation(identity_evidence=[proof(tmp_path, selected_variant_asin=B)])
    assert "DETAIL_SELECTED_CHILD_PRIMARY_ASIN_CONFLICT" in IdentityEvidenceResolver().resolve(row)["identity_conflict_reason"]


def test_reparse_identity_change_cannot_inherit_prior_reviewed_entity(tmp_path):
    directory = tmp_path / "registry"
    registry = ProductIdentityRegistry(directory)
    original = observation()
    registry.ingest(original)
    registry.review("same", ["obs1"], actor="human", evidence="Review of original extraction")
    old_pid = registry.resolve("obs1")["product_id"]
    raw_path = directory / "observations" / (digest("obs1") + ".json")
    raw_bytes = raw_path.read_bytes()
    corrected = observation(asin=B)
    row = registry.materialize_views([corrected], "v2")[0]
    assert row["observation_id"] == "obs1"
    assert row["identity_view_id"] != "obs1" and row["identity_view_changed"]
    assert row["product_id"] is None and row["identity_status"] == "UNCONFIRMED"
    event = registry.review("same", [row["identity_view_id"]], actor="human", evidence="Review corrected selected child")
    reviewed = ProductIdentityRegistry(directory).materialize_views([corrected], "v2")[0]
    assert reviewed["product_id"] and reviewed["product_id"] != old_pid
    assert registry.summary()["observations"] == 1
    assert registry.summary()["identity_view_count"] == 1
    assert registry.summary()["confirmed_identity_views"] == 1
    assert len(registry.materialize()) == 1
    registry.undo(event["event_id"], actor="human", evidence="Incorrect new review")
    assert registry.materialize_views([corrected], "v2")[0]["product_id"] is None
    assert registry.summary()["confirmed_identity_views"] == 0
    assert raw_path.read_bytes() == raw_bytes


def test_identity_view_proof_attachment_and_restart_preserve_base_slot(tmp_path):
    directory = tmp_path / "registry"
    registry = ProductIdentityRegistry(directory)
    registry.ingest(observation())
    corrected = observation(asin=B)
    view = registry.materialize_views([corrected], "v2")[0]
    registry.attach(view["identity_view_id"], [proof(tmp_path, B)], actor="verifier", reason="Verify corrected child against actual saved page")
    refreshed = ProductIdentityRegistry(directory).materialize_views([corrected], "v3")[0]
    assert refreshed["observation_id"] == "obs1"
    assert refreshed["identity_view_id"] == view["identity_view_id"]
    assert refreshed["identity_status"] == "CONFIRMED"
    assert registry.legacy_mapping() == {B: refreshed["product_id"]}
    assert registry.summary()["observations"] == 1


def test_titles_and_tracking_changes_do_not_create_identity_views(tmp_path):
    registry = ProductIdentityRegistry(tmp_path / "registry")
    original = observation(title_raw="Cable")
    registry.ingest(original)
    registry.review("same", ["obs1"], actor="user", evidence="Reviewed cable")
    changed = deepcopy(original)
    changed.update(title_raw="Power cable revised title", raw_product_url=f"https://www.amazon.es/dp/{A}?tag=other&th=1&psc=1")
    assert identity_fingerprint(original) == identity_fingerprint(changed)
    row = registry.materialize_views([changed], "v2")[0]
    assert row["product_id"] == registry.resolve("obs1")["product_id"]
    assert not row["identity_view_changed"]
    assert row["title_raw"] == "Power cable revised title"
    assert registry.summary()["identity_view_count"] == 0


@pytest.mark.parametrize("change", [
    {"selected_variant": "Blue 2-pack"}, {"parent_asin_observed": PARENT},
    {"requested_product_url": f"https://www.amazon.es/dp/{B}"},
    {"final_product_url": f"https://www.amazon.es/dp/{B}"},
    {"canonical_asin_observed": B},
])
def test_identity_bearing_variant_or_parent_evidence_changes_require_own_view(tmp_path, change):
    registry = ProductIdentityRegistry(tmp_path / "registry")
    original = observation()
    registry.ingest(original)
    registry.review("same", ["obs1"], actor="human", evidence="Prior observation review")
    changed = {**original, **change}
    row = registry.materialize_views([changed], "v2")[0]
    assert row["identity_view_changed"] and row["product_id"] is None


def test_same_identity_view_across_parser_versions_keeps_immutable_first_view(tmp_path):
    registry = ProductIdentityRegistry(tmp_path / "registry")
    registry.ingest(observation())
    corrected = observation(asin=B, title_raw="First title")
    first = registry.materialize_views([corrected], "v2")[0]
    second = registry.materialize_views([{**corrected, "title_raw": "Further wording update"}], "v3")[0]
    assert second["identity_view_id"] == first["identity_view_id"]
    assert second["title_raw"] == "Further wording update"
    assert registry.summary()["identity_view_count"] == 1
    assert registry.summary()["observations"] == 1
