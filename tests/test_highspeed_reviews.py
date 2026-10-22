"""Human review evidence stays offline, bound to saved navigation and DOM facts."""
import hashlib
import json
import pytest

from amazon_es_bestseller.highspeed.links import candidate_id, normalize_product_url
from amazon_es_bestseller.highspeed.reviews import validate_destination_review


def example(tmp_path, *, decision="VERIFIED_SAME_DESTINATION", finals=None, choices=None):
    requests = ["https://www.amazon.es/dp/opaque-token?choice=blue", "https://www.amazon.es/gp/product/opaque-token?choice=red"]
    finals = finals or ["https://www.amazon.es/dp/actual-destination"] * 2
    choices = choices or (["Blue", "Red"] if decision == "VERIFIED_INDEPENDENT_PURCHASABLE" else ["Blue", "Blue"])
    candidates, evidence, receipts = [], [], []
    for i, request in enumerate(requests):
        norm = normalize_product_url(request)["normalized_product_url"]
        cid = candidate_id(norm)
        candidates.append({"candidate_id": cid, "normalized_product_url": norm})
        html = '<h1 id="productTitle">Real item</h1><input id="add-to-cart-button"><select id="choice"><option selected>' + choices[i] + '</option></select>'
        html_path = tmp_path / (str(i) + ".html")
        html_path.write_text(html, encoding="utf-8")
        receipt = {"origin": "LIVE_CAPTURE", "observed_at": "2026-10-13T08:00:00Z",
            "request_url": request, "final_url": finals[i], "http_status": 200, "access_status": "NORMAL",
            "html_path": str(html_path), "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
            "product_page_evidence": {"title": {"selector": "#productTitle", "text_raw": "Real item"},
                "purchase_control": {"selector": "#add-to-cart-button"}},
            "selection_evidence": [{"selector": "#choice option[selected]", "text_raw": choices[i]}]}
        receipt_path = tmp_path / (str(i) + ".json")
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        evidence.append({"candidate_id": cid, "receipt_path": str(receipt_path), "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest()})
        receipts.append(receipt)
    review = {"review_id": "human1", "decision": decision, "reviewed_by": "Reviewer", "reviewed_at": "2026-10-13T09:00:00Z",
        "candidate_ids": [c["candidate_id"] for c in candidates], "evidence": evidence}
    return review, candidates, receipts


def rewrite_receipt(review, receipts, index=0):
    item = review["evidence"][index]
    from pathlib import Path
    path = Path(item["receipt_path"])
    path.write_text(json.dumps(receipts[index]), encoding="utf-8")
    item["receipt_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_same_destination_requires_saved_actual_navigation_every_candidate(tmp_path):
    review, candidates, _ = example(tmp_path)
    result = validate_destination_review(review, candidates)
    assert len(result) == 2
    assert len({r["verified_destination_key"] for r in result.values()}) == 1
    assert all(r["url_identity_status"] == "VERIFIED_SAME_DESTINATION" for r in result.values())
    assert candidates[0]["normalized_product_url"] != candidates[1]["normalized_product_url"]


def test_different_final_variant_params_never_merge(tmp_path):
    review, candidates, _ = example(tmp_path, finals=["https://www.amazon.es/dp/token?th=1", "https://www.amazon.es/dp/token?th=2"])
    with pytest.raises(ValueError, match="DIFFERENT_DESTINATIONS"):
        validate_destination_review(review, candidates)


def test_same_destination_different_selected_variants_never_merge(tmp_path):
    review, candidates, _ = example(tmp_path, choices=["Blue", "Red"])
    with pytest.raises(ValueError, match="DIFFERENT_SELECTED_VARIANTS"):
        validate_destination_review(review, candidates)


def test_independent_review_requires_distinct_final_urls_and_selected_variants(tmp_path):
    review, candidates, _ = example(tmp_path, decision="VERIFIED_INDEPENDENT_PURCHASABLE",
        finals=["https://www.amazon.es/dp/token?choice=blue", "https://www.amazon.es/dp/token?choice=red"])
    result = validate_destination_review(review, {c["candidate_id"]: c for c in candidates})
    assert len({r["verified_destination_key"] for r in result.values()}) == 2
    assert all(r["url_identity_status"] == "VERIFIED_INDEPENDENT_PURCHASABLE" for r in result.values())


@pytest.mark.parametrize("field,value,error", [
    ("origin", "OFFLINE_FIXTURE", "ACTUAL_NAVIGATION_ORIGIN"),
    ("http_status", 403, "ACCESS_NOT_NORMAL"),
    ("access_status", "CHALLENGE", "ACCESS_NOT_NORMAL"),
    ("observed_at", "2026-10-14T08:00:00Z", "PRECEDES_NAVIGATION"),
    ("observed_at", "2026-10-13", "AWARE_TIMESTAMP"),
    ("request_url", "https://www.amazon.es/dp/wrong", "CANDIDATE_DESTINATION_MISMATCH"),
    ("final_url", "https://evil.example/dp/token", "CANDIDATE_DESTINATION_MISMATCH"),
    ("final_url", "https://www.amazon.es/sspa/click?url=/dp/token", "CANDIDATE_DESTINATION_MISMATCH"),
    ("html_sha256", "0" * 64, "PRODUCT_PAGE_HASH_MISMATCH"),
])
def test_invalid_receipt_never_upgrades(field, value, error, tmp_path):
    review, candidates, receipts = example(tmp_path)
    receipts[0][field] = value
    rewrite_receipt(review, receipts)
    with pytest.raises(ValueError, match=error):
        validate_destination_review(review, candidates)


def test_receipt_tampering_detected(tmp_path):
    review, candidates, receipts = example(tmp_path)
    receipts[0]["final_url"] = "https://www.amazon.es/dp/different"
    from pathlib import Path
    Path(review["evidence"][0]["receipt_path"]).write_text(json.dumps(receipts[0]), encoding="utf-8")
    with pytest.raises(ValueError, match="NAVIGATION_RECEIPT_HASH_MISMATCH"):
        validate_destination_review(review, candidates)


@pytest.mark.parametrize("mutation,error", [("title", "TITLE_EVIDENCE_MISMATCH"), ("purchase", "PURCHASE_CONTROL_REQUIRED"),
    ("no_selection", "SELECTED_VARIANT_EVIDENCE"), ("bad_selection", "VARIANT_EVIDENCE_VALUE_MISMATCH")])
def test_dom_proof_cannot_be_replaced_by_claim(mutation, error, tmp_path):
    review, candidates, receipts = example(tmp_path, decision="VERIFIED_INDEPENDENT_PURCHASABLE",
        finals=["https://www.amazon.es/dp/token?choice=blue", "https://www.amazon.es/dp/token?choice=red"])
    if mutation == "title": receipts[0]["product_page_evidence"]["title"]["text_raw"] = "Guessed"
    elif mutation == "purchase": receipts[0]["product_page_evidence"]["purchase_control"]["selector"] = "h1"
    elif mutation == "no_selection": receipts[0]["selection_evidence"] = []
    else: receipts[0]["selection_evidence"][0]["text_raw"] = "Invented"
    rewrite_receipt(review, receipts)
    with pytest.raises(ValueError, match=error): validate_destination_review(review, candidates)


def test_same_selected_variant_cannot_be_claimed_two_independent_products(tmp_path):
    review, candidates, _ = example(tmp_path, decision="VERIFIED_INDEPENDENT_PURCHASABLE",
        finals=["https://www.amazon.es/dp/token?opaque=1", "https://www.amazon.es/dp/token?opaque=2"], choices=["Blue", "Blue"])
    with pytest.raises(ValueError, match="DISTINCT_PURCHASABLE_VARIANT"):
        validate_destination_review(review, candidates)


@pytest.mark.parametrize("field,value", [("reviewed_by", ""), ("review_id", ""), ("decision", "AUTO_TITLE_SIMILARITY")])
def test_explicit_human_review_required(field, value, tmp_path):
    review, candidates, _ = example(tmp_path)
    review[field] = value
    with pytest.raises(ValueError, match="EXPLICIT_HUMAN_REVIEW"):
        validate_destination_review(review, candidates)


def test_partial_evidence_cannot_partially_upgrade(tmp_path):
    review, candidates, _ = example(tmp_path)
    review["evidence"].pop()
    with pytest.raises(ValueError, match="EACH_CANDIDATE_RECEIPT"):
        validate_destination_review(review, candidates)
