from pathlib import Path

from amazon_es_bestseller.monitoring.ranking_identity.extract import extract_identity_from_evidence


def test_saved_html_replay_is_deterministic_without_network():
    root = Path("tests/fixtures/amazon_es/ranking_identity")
    first = extract_identity_from_evidence(root)
    second = extract_identity_from_evidence(root)
    assert {row["asin"] for row in first["records"]} == {row["asin"] for row in second["records"]}
    assert {row["product_url"] for row in first["records"]} == {row["product_url"] for row in second["records"]}
    assert [(row["asin"], row["identity_status"]) for row in first["records"]] == [(row["asin"], row["identity_status"]) for row in second["records"]]
