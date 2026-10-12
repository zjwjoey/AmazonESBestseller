"""Evidence-bound final review of the full offline 5480 candidate, never promotion.

This is a postprocessor: it does not launch replay workers or modify prior files.
Unchanged source with a vanished finding is a policy change, not a source repair.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import socket
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from lxml import html as lxml_html

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from amazon_es_bestseller.export.excel import HEAD_ES, HEAD_ZH, WIDTH_ES, WIDTH_ZH, _es_values  # noqa: E402
from amazon_es_bestseller.production.spanish_source_closure import _merged_records  # noqa: E402
from amazon_es_bestseller.translation.full_detail import render_details_es, render_bullets_es, detail_bullets_to_attributes  # noqa: E402
from amazon_es_bestseller.normalization.price import parse_price  # noqa: E402
from amazon_es_bestseller.normalization.parent_asin import confirmed_self_parent  # noqa: E402
from amazon_es_bestseller.quality.source_fields import _sf_record_binding  # noqa: E402
from amazon_es_bestseller.quality.detail_identity import extract_detail_identity_signals, review_visible_asin_identity  # noqa: E402


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def gate_summary(gate):
    statuses = (gate.get("evidence") or {}).get("sku_status") or {}
    return {"status": gate.get("status"), "ready": gate.get("ready"), "audit_hash": gate.get("audit_hash"),
            "sku_status_counts": dict(Counter(statuses.values()))}


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def write_csv(path, rows, fields):
    with Path(path).open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: canonical(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def label_of(issue):
    evidence = issue.get("evidence") or {}
    locator = evidence.get("evidence_locator") or evidence.get("locator") or {}
    label = locator.get("label_raw") or evidence.get("label")
    if label:
        return str(label)
    message = str(issue.get("message") or "")
    suffix = " has the wrong unit type"
    return message[:-len(suffix)] if message.endswith(suffix) else ""


def field_value(record, field, label=""):
    if field == "attributes" and label:
        return [a for a in record.get("attributes") or []
                if str(a.get("label_raw") or "").strip().casefold() == label.strip().casefold()]
    aliases = {"brand": "brand_raw", "specification": "spec_v2"}
    return record.get(field, record.get(aliases.get(field, "")))


def complete_display_details(record):
    return render_details_es(list(record.get("attributes") or [])
                             + detail_bullets_to_attributes(record.get("detail_bullets_raw")))


def related(issue, current):
    if current.get("asin") != issue.get("asin") or current.get("field") != issue.get("field"):
        return False
    label, other = label_of(issue), label_of(current)
    if issue.get("field") == "attributes" and label:
        return label.strip().casefold() == other.strip().casefold()
    return current.get("issue_code") == issue.get("issue_code") or current.get("message") == issue.get("message")


def positive_repair(issue, before, after, value):
    """Only explicitly revalidated predicates can claim a confirmed repair."""
    field, code, message = issue.get("field"), issue.get("issue_code"), issue.get("message", "")
    if code == "FIELD_MISPLACED" and field == "brand":
        explicit = [a.get("value_raw") for a in after.get("attributes") or []
                    if str(a.get("label_raw") or "").casefold() in {"marca", "brand"}]
        return bool(value and value in explicit and value != field_value(before, field))
    if code == "FIELD_MISPLACED" and field == "product_details_es" and "ranking/UI" in message:
        return (bool(value) and value == complete_display_details(after)
                and not re.search(r"\b(?:best sellers rank|m[aá]s vendidos|ranking source url)\b", str(value), re.I))
    if code == "CATEGORY_COPIED":
        a, b, c = (after.get(k) for k in ("category_l1", "category_l2", "category_l3"))
        return bool(a and b and a != b and (not c or c != b) and after.get("category_provenance"))
    return False


def compare_issue(issue, before, after, current_issues):
    field, label = issue.get("field"), label_of(issue)
    previous, current = field_value(before or {}, field, label), field_value(after or {}, field, label)
    matching = [i for i in current_issues if related(issue, i)]
    if not before or not after or not field or after.get("offline_replay_status") != "REPARSED_IDENTITY_MATCH":
        status, reason = "EVIDENCE_INSUFFICIENT", "Missing exact field binding, scope record or verified same-ASIN replay."
    elif matching:
        status, reason = "STILL_ISSUE", "Current audit still flags this field/attribute (possibly under a different issue code)."
    elif canonical(previous) == canonical(current):
        status, reason = "RULE_CHANGE_ONLY", "Relevant source/candidate field is unchanged; absence of a current finding is not a fact repair."
    elif positive_repair(issue, before, after, current):
        status, reason = "CONFIRMED_FIXED", "Changed field passes an explicit source-bound repair predicate; original evidence is retained."
    else:
        status, reason = "EVIDENCE_INSUFFICIENT", "Field changed and finding vanished, but that alone does not prove the specific historical defect was repaired."
    return {"resolution": status, "confirmed_repair": status == "CONFIRMED_FIXED", "reason": reason,
            "current_matching_count": len(matching), "current_issue_codes": sorted({i.get("issue_code", "") for i in matching}),
            "historical_field_value": previous, "candidate_field_value": current}


def bind_historical(issue, audit):
    item = dict(issue)
    if item.get("field"):
        return item
    candidates = [row.get("field") for row in audit.get("field_audits") or []
                  if row.get("asin") == item.get("asin") and row.get("message") == item.get("message")
                  and canonical(row.get("evidence", {})) == canonical(item.get("evidence", {}))]
    fields = set(candidates)
    if len(fields) == 1:
        item["field"] = next(iter(fields))
    return item


def attribute_tuple(item):
    return canonical({k: item.get(k) for k in ("section", "label_raw", "value_raw", "position", "source")})


def html_inventory(html):
    """Independent XPath census of detail blocks, not a call to the replay parser.

    Check all additional-information blocks (not only the first non-empty one).
    Duplicate pairs across sections do not imply distinct lost facts.
    """
    dom = lxml_html.fromstring(html)

    def text(element):
        visible_text = element.xpath('.//text()[not(ancestor::script) and not(ancestor::style)]')
        return re.sub(r"\s+", " ", " ".join(s.strip() for s in visible_text if s.strip())).strip()

    pairs = []
    for container in dom.xpath('//*[@id="productOverview_feature_div"]'):
        for row in container.xpath('.//table//tr'):
            cells = row.xpath('.//td')
            if len(cells) >= 2 and text(cells[0]) and text(cells[1]):
                pairs.append((text(cells[0]), text(cells[1])))
    for container in dom.xpath('//*[@id="prodDetails"]'):
        for table in container.xpath('.//table'):
            table_id = table.get("id", "")
            if table_id.startswith("productDetails_detailBullets") or "feedback" in table_id:
                continue
            for row in table.xpath('.//tr'):
                keys, values = row.xpath('.//th'), row.xpath('.//td')
                if keys and values and text(keys[0]) and text(values[0]):
                    pairs.append((text(keys[0]), text(values[0])))
    for container in dom.xpath('//*[@id="detailBullets_feature_div"]'):
        for key, value in zip(container.xpath('.//dt'), container.xpath('.//dd')):
            if text(key) and text(value):
                pairs.append((text(key), text(value)))
    for container in dom.xpath('//*[@id="productDetails_detailBullets_sections1"]'):
        for row in container.xpath('.//tr'):
            keys, values = row.xpath('.//th'), row.xpath('.//td')
            if keys and values and text(keys[0]) and text(values[0]):
                pairs.append((text(keys[0]), text(values[0])))
    bullets = []
    for block_id in ("feature-bullets", "featurebullets_feature_div", "pqv-feature-bullets"):
        containers = dom.xpath('//*[@id=$block_id]', block_id=block_id)
        if containers:
            bullets = [text(li) for li in containers[0].xpath('.//li') if text(li)]
        if bullets:
            break
    return {"attribute_pairs": list(dict.fromkeys(pairs)), "feature_bullets": bullets}


def check_evidence(evidence):
    """Read-only offline worker; no parser replay and no page request."""
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("OFFLINE_NETWORK_FORBIDDEN"))
    path = evidence.get("html_path")
    html_valid = bool(path and Path(path).is_file() and sha(path) == evidence.get("html_sha256"))
    meta_path = Path(path).with_suffix(".meta.json") if path else None
    meta_valid = (sha(meta_path) == evidence.get("meta_sha256")) if meta_path and meta_path.is_file() else evidence.get("meta_sha256") is None
    try:
        html = Path(path).read_bytes().decode("utf-8") if html_valid else ""
        inventory = html_inventory(html) if html_valid else {}
        identity_signals = extract_detail_identity_signals(html) if html_valid else {}
        error = None
    except Exception as exc:
        inventory, identity_signals, error = {}, {}, f"{type(exc).__name__}: {exc}"
    return {"html_valid": html_valid, "meta_valid": meta_valid, "inventory": inventory, "identity_signals": identity_signals, "inventory_error": error}


def register_fact_checks(original, historical, after, replayed):
    """Check the register's normalization/identity cases against actual rerun data."""
    rows = []
    details = {r["asin"]: r for r in replayed}
    for asin, current in after.items():
        old, raw = historical[asin], original[asin]
        cur, orig = parse_price(raw.get("current_price_raw")), parse_price(raw.get("original_price_raw"))
        if cur and orig:
            detail = details[asin]
            refresh = detail.get("offline_price_refresh") or {}
            corrected_raw = detail.get("original_price_raw")
            evidence_hash = (detail.get("offline_replay_evidence") or {}).get("html_sha256")
            refresh_bound = bool(evidence_hash and refresh.get("producer") == "saved_html_price_parser"
                and refresh.get("html_sha256") == evidence_hash
                and refresh.get("original_price_raw_after") == corrected_raw
                and refresh.get("original_price_raw_before") == raw.get("original_price_raw"))
            effective_orig = parse_price(corrected_raw) if refresh_bound else orig
            excluded = refresh_bound and not corrected_raw
            relation_valid = bool(effective_orig and effective_orig > cur)
            expected = round((effective_orig - cur) / effective_orig, 4) if relation_valid else None
            expected_original = effective_orig if relation_valid else None
            valid = current.get("original_price") == expected_original and current.get("discount_rate") == expected
            repaired_attribution = refresh_bound and corrected_raw != raw.get("original_price_raw") and (excluded or relation_valid)
            changed = old.get("original_price") != expected_original or old.get("discount_rate") != expected
            resolution = ("CONFIRMED_FIXED" if valid and (repaired_attribution or relation_valid and changed)
                else "RULE_CHANGE_ONLY" if valid and relation_valid else "STILL_ISSUE")
            rows.append({"register_issue_id": "ISS-PRICE-GROUPED-THOUSANDS-3094" if orig > cur else "ISS-PRICE-RELATION-261",
                "asin": asin, "resolution": resolution, "passed": valid,
                "check": "HTML-bound original-price attribution and canonical discount checked; original capture retained"
                    if repaired_attribution else "unambiguous Spanish price and discount relation" if relation_valid
                    else "invalid source price relation remains unexplained; no factual correction claimed",
                "source_invalid": not (excluded or relation_valid), "original_raw": raw.get("original_price_raw"),
                "reparsed_original_raw": corrected_raw, "refresh_bound": refresh_bound,
                "repair_kind": "PARSER_FALSE_ORIGINAL_REMOVED" if repaired_attribution and excluded
                    else "PARSER_REFERENCE_PRICE_RECOVERED" if repaired_attribution else None,
                "current_raw": raw.get("current_price_raw"), "expected_original": expected_original,
                "expected_discount": expected, "candidate_original": current.get("original_price"),
                "candidate_discount": current.get("discount_rate")})
        if str(raw.get("parent_asin") or "").upper() == asin:
            self_parent = str(current.get("parent_asin") or "").upper() == asin
            proof = confirmed_self_parent(details[asin], asin) if self_parent else False
            valid = not self_parent or proof
            rows.append({"register_issue_id": "ISS-IDENTITY-SELF-PARENT-845", "asin": asin,
                "resolution": "CONFIRMED_FIXED" if self_parent and proof else "EVIDENCE_INSUFFICIENT" if valid else "STILL_ISSUE",
                "check": "self-parent only with HTML-bound parser family evidence", "passed": valid,
                "source_parent": raw.get("parent_asin"), "candidate_parent": current.get("parent_asin"),
                "family_evidence_verified": proof, "note": "Clearing an unconfirmed parent is safe, but does not recover a missing parent identity."})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--issue-register", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    # Fail if still parsing or if an earlier completed review would be overwritten.
    if not (output / "summary.json").is_file():
        raise RuntimeError("REPLAY_NOT_COMPLETE")
    target = output / "final_review"
    target.mkdir(exist_ok=False)
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("OFFLINE_NETWORK_FORBIDDEN"))
    metadata = read(output / "input_metadata.json")
    inputs = {key: Path(value) for key, value in metadata["inputs"].items()}
    unchanged = {key: sha(path) == metadata["input_sha256"][key] for key, path in inputs.items()}
    source_history = read(inputs["source_audit"])
    closure_history = read(inputs["historical_audit"])["closure_audit"]
    scope = set(source_history["record_bindings"])
    candidates, rankings, original_rows = (read(inputs[name]) for name in ("candidate_manifest", "rankings", "details"))
    original = {r["asin"]: r for r in original_rows if r.get("asin") in scope}
    prior_master = read(inputs["historical_candidate"])
    prior_records = prior_master["records"] if isinstance(prior_master, dict) else prior_master
    historical = {r["asin"]: r for r in prior_records}
    replayed = read(output / "replayed_details.json")
    candidate_file = output / "spanish_candidate/spanish_master_5480.json"
    master = read(candidate_file)
    records = master["records"]
    after = {r["asin"]: r for r in records}
    raw_current = read(output / "replayed_source_audit.json")
    current_audit = read(output / "spanish_candidate/audit.json")["closure_audit"]
    logs = {r["asin"]: r for r in read(output / "replay_log.json")}
    primary = {r["asin"]: r for r in candidates}
    before_raw = {r["asin"]: r for r in _merged_records(candidates, original_rows, rankings, scope)}
    comparisons = []
    for layer, audit, previous, current in (
        ("historical_source_5480", source_history, before_raw, raw_current),
        ("historical_closure_r3", closure_history, historical, current_audit),
    ):
        binding_verified = {asin: _sf_record_binding(row)["record_hash"] in
            [b.get("record_hash") for b in audit.get("record_bindings", {}).get(asin, [])]
            for asin, row in previous.items()}
        history_fields_by_asin, current_by_asin = defaultdict(list), defaultdict(list)
        for item in audit.get("field_audits") or []:
            history_fields_by_asin[item.get("asin")].append(item)
        current_findings = current["issues"] + current_audit["issues"] if layer == "historical_source_5480" else current["issues"]
        for item in current_findings:
            current_by_asin[item.get("asin")].append(item)
        for index, finding in enumerate(audit["issues"]):
            asin = finding.get("asin")
            issue = bind_historical(finding, {"field_audits": history_fields_by_asin[asin]})
            record = {**after.get(asin, {}), "offline_replay_status": logs.get(asin, {}).get("status")}
            # Both layers are checked against the delivered candidate as well as raw-current findings.
            result = compare_issue(issue, previous.get(asin), record, current_by_asin[asin])
            if not binding_verified.get(asin) and result["resolution"] in {"CONFIRMED_FIXED", "RULE_CHANGE_ONLY"}:
                result.update({"resolution": "EVIDENCE_INSUFFICIENT", "confirmed_repair": False,
                    "reason": "The reconstructed historical stage record does not match the audit's recorded hash. Raw inputs are preserved, but exact old derived-field contents cannot be asserted from that alone."})
            comparisons.append({"historical_id": f"{layer}:{index}", "layer": layer, "asin": asin,
                "field": issue.get("field"), "label": label_of(issue), "issue_code": issue.get("issue_code"),
                "severity": issue.get("severity"), **result, "historical_record_binding_verified": binding_verified.get(asin, False),
                "historical_issue": finding,
                "evidence_pointer": f"{inputs['source_audit' if layer == 'historical_source_5480' else 'historical_audit']}#{'/issues/' if layer == 'historical_source_5480' else '/closure_audit/issues/'}{index}"})
    register = read(args.issue_register)
    register_review = []
    for section in ("summary_issues", "runtime_status_updates", "known_gaps"):
        for index, entry in enumerate(register.get(section, [])):
            item = entry if isinstance(entry, dict) else {"description": entry}
            register_review.append({"historical_id": item.get("issue_id", f"{section}:{index}"), "section": section,
                "resolution": "EVIDENCE_INSUFFICIENT", "reason": "Summary/development statement is not a per-SKU fact repair; see exact historical issue comparisons. Translation paths were not executed in this offline source task.",
                "historical_entry": item})
    # Check that every original r3 pointer is covered, rather than silently ignoring the register.
    pointer_indexes = [r.get("audit_issue_index") for r in register.get("current_closure_issue_pointers", [])]
    pointer_coverage = set(pointer_indexes) == set(range(len(closure_history["issues"])))
    fidelity = []
    evidence_checks = []
    unresolved = []
    for origin, audit in (("replayed_raw_audit", raw_current), ("candidate_closure_audit", current_audit)):
        for index, issue in enumerate(audit["issues"]):
            unresolved.append({"origin": origin, "index": index, **issue})
    # Source review queue includes missing evidence, multilingual values and builder identity decisions.
    queue = read(output / "spanish_candidate/source_review_queue.json")
    for index, item in enumerate(queue):
        if item.get("classification") != "PASS" and item.get("status") != "PASS":
            unresolved.append({"origin": "source_review_queue", "index": index, **item})
    print(json.dumps({"stage": "INDEPENDENT_HTML_CENSUS_START", "total": len(replayed), "offline_workers": 8}), flush=True)
    checks = []
    with ProcessPoolExecutor(max_workers=8) as pool:
        for check in pool.map(check_evidence, [r.get("offline_replay_evidence", {}) for r in replayed]):
            checks.append(check)
            if len(checks) % 250 == 0:
                print(json.dumps({"stage": "INDEPENDENT_HTML_CENSUS", "checked": len(checks)}), flush=True)
    evidence_results = dict(zip((r["asin"] for r in replayed), checks))
    for row in replayed:
        asin = row["asin"]
        before, candidate = original[asin], after[asin]
        attrs_old = Counter(attribute_tuple(a) for a in before.get("attributes") or [])
        attrs_new = Counter(attribute_tuple(a) for a in row.get("attributes") or [])
        lost_attrs = list((attrs_old - attrs_new).elements())
        old_bullets, new_bullets = before.get("feature_bullets_raw") or [], row.get("feature_bullets_raw") or []
        lost_bullets = list((Counter(old_bullets) - Counter(new_bullets)).elements())
        attributes_bound = candidate.get("attributes") == row.get("attributes")
        bullets_bound = candidate.get("feature_bullets_raw") == new_bullets
        detail_bullets_bound = candidate.get("detail_bullets_raw") == row.get("detail_bullets_raw")
        categories_bound = all(candidate.get(k) == primary[asin].get(k) for k in ("category_l1", "category_l2", "category_l3", "leaf_category", "bestseller_rank"))
        notes_preserved = all(not before.get(k) or candidate.get(k) == before.get(k) for k in ("notes", "备注"))
        display_details_bound = candidate.get("product_details_es") == complete_display_details(candidate)
        evidence = row.get("offline_replay_evidence", {})
        path = evidence.get("html_path")
        check = evidence_results[asin]
        html_valid, meta_valid, inventory = check["html_valid"], check["meta_valid"], check["inventory"]
        captured_pairs = {(a.get("label_raw"), a.get("value_raw")) for a in row.get("attributes") or []}
        missing_dom_pairs = [pair for pair in inventory.get("attribute_pairs", []) if pair not in captured_pairs]
        dom_bullets_bound = inventory.get("feature_bullets") == new_bullets if html_valid else False
        url_asins = re.findall(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", str(candidate.get("product_url") or ""), re.I)
        url_bound = bool(url_asins and all(v.upper() == asin for v in url_asins))
        identity_review = review_visible_asin_identity(row, check["identity_signals"])
        identity_conflicts = identity_review["conflicts"] + identity_review["strong_signal_conflicts"]
        identity_signals_bound = row.get("detail_identity_signals") == check["identity_signals"]
        info = {"asin": asin, "result": logs[asin]["status"], "html_hash_verified": html_valid,
            "meta_hash_verified": meta_valid, "url_asin_bound": url_bound,
            "attributes_before": sum(attrs_old.values()), "attributes_after": sum(attrs_new.values()),
            "attributes_lost": len(lost_attrs), "lost_attributes": lost_attrs,
            "bullets_before": len(old_bullets), "bullets_after": len(new_bullets), "bullets_lost": len(lost_bullets),
            "attributes_exact_candidate_binding": attributes_bound, "bullets_exact_candidate_binding": bullets_bound,
            "detail_bullets_exact_binding": detail_bullets_bound, "ranking_category_binding": categories_bound,
            "notes_preserved": notes_preserved, "detail_attribute_asin_conflicts": identity_conflicts,
            "detail_identity_status": identity_review["status"], "identity_review": identity_review,
            "identity_signals_html_binding": identity_signals_bound,
            "complete_display_details_binding": display_details_bound}
        info.update({"html_attribute_pairs_checked": len(inventory.get("attribute_pairs", [])),
                     "missing_html_attribute_pairs": missing_dom_pairs, "dom_bullet_order_binding": dom_bullets_bound,
                     "inventory_error": check["inventory_error"]})
        fidelity.append(info)
        evidence_checks.append({"asin": asin, "html_path": path, "html_sha256": evidence.get("html_sha256"),
                                "html_verified": html_valid, "meta_verified": meta_valid})
        failures = [key for key in ("html_hash_verified", "meta_hash_verified", "url_asin_bound",
            "attributes_exact_candidate_binding", "bullets_exact_candidate_binding", "detail_bullets_exact_binding",
            "ranking_category_binding", "notes_preserved", "dom_bullet_order_binding", "complete_display_details_binding", "identity_signals_html_binding") if not info[key]]
        if identity_review["status"] == "PARENT_DETAIL_REVIEW_REQUIRED":
            unresolved.append({"origin": "typed_identity_review", "asin": asin, "field": "attributes",
                "issue_code": "PARENT_DETAIL_SCOPE_REVIEW", "severity": "P1", "status": "REVIEW",
                "reason": "Source-proven parent identity explains the difference, not every child-specific attribute.", "evidence": identity_review})
        if failures or lost_attrs or lost_bullets or identity_conflicts or missing_dom_pairs or info["result"] != "REPARSED_IDENTITY_MATCH":
            unresolved.append({"origin": "final_integrity", "asin": asin, "issue_code": "INTEGRITY_REVIEW_REQUIRED",
                "severity": "P0" if failures or identity_conflicts else "P1", "status": "BLOCK",
                "evidence": {"failed_checks": failures, "lost_attributes": lost_attrs, "lost_bullets": lost_bullets,
                             "identity_conflicts": identity_conflicts, "missing_html_attribute_pairs": missing_dom_pairs}})
    exact_scope = len(records) == 5480 and len(after) == 5480 and set(after) == scope and len(replayed) == 5480
    code_drift = {name: sha(ROOT / name) != digest for name, digest in metadata["code_file_sha256"].items()}
    verified_inputs = {name: {"path": str(path), "before_sha256": metadata["input_sha256"][name],
        "after_sha256": sha(path), "unchanged": unchanged[name]} for name, path in inputs.items()}
    verified_inputs["issue_register"] = {"path": str(args.issue_register.resolve()), "sha256": sha(args.issue_register)}
    counts = dict(Counter(r["resolution"] for r in comparisons))
    summary = read(output / "summary.json")
    fact_checks = register_fact_checks(original, historical, after, replayed)
    fact_groups = defaultdict(list)
    for row in fact_checks:
        fact_groups[row["register_issue_id"]].append(row)
    for entry in register_review:
        checks_for_issue = fact_groups.get(entry["historical_id"], [])
        if checks_for_issue:
            entry["fact_check_count"] = len(checks_for_issue)
            entry["resolution_counts"] = dict(Counter(r["resolution"] for r in checks_for_issue))
            entry["acceptance_failures"] = sum(not r["passed"] for r in checks_for_issue)
            entry["reason"] = "Actual full-scope normalization/identity checks are in register_fact_checks.json. A safe empty canonical value is not recovered source evidence."
            if any(r["resolution"] == "STILL_ISSUE" for r in checks_for_issue):
                entry["resolution"] = "STILL_ISSUE"
            elif any(r["resolution"] == "EVIDENCE_INSUFFICIENT" for r in checks_for_issue):
                entry["resolution"] = "EVIDENCE_INSUFFICIENT"
            elif any(r["resolution"] == "CONFIRMED_FIXED" for r in checks_for_issue):
                entry["resolution"] = "CONFIRMED_FIXED"
            else:
                entry["resolution"] = "RULE_CHANGE_ONLY"
    normalization_failures = [r for r in fact_checks if not r["passed"]]
    for item in fact_checks:
        if item["resolution"] in {"STILL_ISSUE", "EVIDENCE_INSUFFICIENT"}:
            unresolved.append({"origin": "register_source_evidence", "asin": item["asin"],
                "issue_code": item["register_issue_id"], "severity": "P2", "status": "REVIEW",
                "reason": item["check"], "evidence": item})
    for item in normalization_failures:
        unresolved.append({"origin": "register_acceptance", "asin": item["asin"], "issue_code": "REGRESSION_ACCEPTANCE_FAILED",
                           "severity": "P0", "status": "BLOCK", "evidence": item})
    summary.update({"comparison_resolution_counts": counts, "historical_total_compared": len(comparisons),
        "comparison_by_layer": {layer: dict(Counter(r["resolution"] for r in comparisons if r["layer"] == layer))
                                for layer in ("historical_source_5480", "historical_closure_r3")},
        "register_pointer_coverage": pointer_coverage, "register_non_sku_entries_reviewed": len(register_review),
        "exact_5480_asin_scope": exact_scope, "used_html_hash_verified_count": sum(r["html_hash_verified"] for r in fidelity),
        "source_code_drift_files": [k for k, v in code_drift.items() if v], "raw_inputs_unchanged": all(unchanged.values()),
        "attribute_facts_lost_count": sum(r["attributes_lost"] for r in fidelity),
        "feature_bullets_lost_count": sum(r["bullets_lost"] for r in fidelity),
        "integrity_unresolved_count": sum(r.get("origin") == "final_integrity" for r in unresolved),
        "missing_html_attribute_pairs": sum(len(r["missing_html_attribute_pairs"]) for r in fidelity),
        "dom_bullet_order_verified_count": sum(r["dom_bullet_order_binding"] for r in fidelity),
        "detail_identity_status_counts": dict(Counter(r["detail_identity_status"] for r in fidelity)),
        "direction_character_normalized_skus": sum(bool(r["identity_review"]["normalized_only"]) for r in fidelity),
        "unresolved_queue_count_with_layer_duplicates": len(unresolved),
        "candidate_json_path": str(candidate_file), "candidate_csv_path": str(output / "spanish_candidate/spanish_master_5480.csv"),
        "actual_raw_field_changes": dict(Counter(key for r in replayed for key in (
            "title_es_raw", "current_price_raw", "original_price_raw", "brand_raw", "parent_asin", "attributes",
            "feature_bullets_raw", "detail_bullets_raw", "date_first_available_raw", "seller_raw", "selected_variation_raw")
            if original[r["asin"]].get(key) != r.get(key))),
        "register_fact_checks": {k: {"count": len(v), "statuses": dict(Counter(r["resolution"] for r in v)),
                                      "acceptance_failures": sum(not r["passed"] for r in v)} for k, v in fact_groups.items()},
        "not_formally_promoted": True, "translation_run": False, "no_live_access": True})
    fatal_integrity = not exact_scope or not all(unchanged.values()) or any(code_drift.values())
    summary["final_integrity_gate"] = not fatal_integrity and summary["integrity_unresolved_count"] == 0 and not normalization_failures
    summary["final_review_status"] = "BLOCKED_INTEGRITY" if not summary["final_integrity_gate"] else "CANDIDATE_REVIEW_COMPLETE_NOT_FORMAL"
    write(target / "summary.json", summary)
    write(target / "input_and_evidence_hashes.json", {"original_inputs": verified_inputs, "used_evidence": evidence_checks,
        "code_before_sha256": metadata["code_file_sha256"], "code_drift": code_drift})
    write(target / "historical_issue_comparison.json", comparisons)
    write(target / "register_summary_comparison.json", register_review)
    write(target / "register_fact_checks.json", fact_checks)
    write_csv(target / "register_fact_checks.csv", fact_checks, list(dict.fromkeys(k for row in fact_checks for k in row)))
    write(target / "unresolved.json", unresolved)
    write(target / "sku_processing_results.json", fidelity)
    write_csv(target / "historical_issue_comparison.csv", comparisons, list(comparisons[0]))
    write_csv(target / "sku_processing_results.csv", fidelity, list(fidelity[0]))
    write_csv(target / "unresolved.csv", unresolved,
        ["origin", "index", "asin", "field", "issue_code", "classification", "severity", "status", "message", "reason", "evidence", "evidence_locator"])
    # Fully rendered business data: no translation call and no Spanish fallback masquerading as Chinese.
    rows = []
    zh_rows = []
    for index, row in enumerate(sorted(records, key=lambda r: r["asin"]), 1):
        display = dict(row)
        values = _es_values(display, index)
        rows.append(values)
        zh = [None] * 26
        for position, value in ((1, index), (2, row["asin"]), (3, row.get("parent_asin")), (5, row.get("brand")),
            (6, values[5]), (7, values[6]), (8, values[7]), (9, values[8]), (10, values[9]), (11, values[10]),
            (16, values[15]), (21, values[20]), (22, values[21]), (23, values[22]), (24, values[23]), (25, values[24])):
            zh[position] = value
        zh_rows.append(zh)
    write_csv(target / "spanish_candidate_business.csv", [dict(zip(HEAD_ES, r)) for r in rows], HEAD_ES)
    # Audit tabs are review deliverables, not replacement source evidence.
    compare_columns = ["historical_id", "layer", "asin", "field", "label", "issue_code", "severity", "resolution", "reason", "current_matching_count"]
    unresolved_columns = ["asin", "origin", "field", "issue_code", "classification", "severity", "status", "message", "reason"]
    planning = [["5480 SKU 西语候选 — 非正式版", "值 / 说明"],
        ["处理范围", 5480], ["唯一ASIN", len(after)], ["ASIN集合核验", exact_scope],
        ["原始输入哈希未变化", all(unchanged.values())], ["Source Gate", canonical(gate_summary(summary["source_gate"]))],
        ["Closure Gate", canonical(gate_summary(summary["closure_gate"]))], ["交付状态", summary["final_review_status"]],
        ["历史对照数（两层审查，不加总作错误SKU数）", len(comparisons)],
        ["当前候选问题记录数", summary["current_issue_count"]], ["当前候选问题SKU数", summary["current_affected_skus"]],
        *[[key, value] for key, value in counts.items()],
        ["中文页说明", "保留冻结26列和5480行身份对照；中文翻译字段未生成。请使用西班牙语选品清单审阅。"],
        ["使用限制", "候选文件可审阅，不代表SOURCE_READY；原始疑点和缺失不猜测、不删除。"],
        ["源证据", str(inputs["details"])], ["冻结候选", str(inputs["candidate_manifest"])],
        ["历史对照JSON", str(target / "historical_issue_comparison.json")],
        ["未解决完整证据", str(target / "unresolved.json")]]
    planning.extend([["详情身份分类", canonical(summary["detail_identity_status_counts"])],
                     ["处理模式", summary.get("replay_mode", "FULL_HTML_REPARSE")],
                     ["价格块重新解析数量", summary.get("price_refreshed_from_verified_html", 0)],
                     ["本次原价解析结果变化SKU", summary.get("price_changed_from_previous_replay", 0)]])
    planning.extend([[f"登记表实际复核：{k}", canonical(v)] for k, v in summary["register_fact_checks"].items()])
    planning.extend([[f"研究类目：{k}", v] for k, v in sorted(Counter(
        (r.get("metadata") or {}).get("research_category", "UNKNOWN") for r in records).items())])
    sheets = [
        {"name": "类目规划", "headers": planning[0], "rows": planning[1:], "widths": [48, 110]},
        {"name": "西班牙语选品清单", "headers": HEAD_ES, "rows": rows, "widths": WIDTH_ES},
        {"name": "中文选品清单", "headers": HEAD_ZH, "rows": zh_rows, "widths": WIDTH_ZH},
        {"name": "历史问题对照", "headers": compare_columns, "rows": [[r.get(k) for k in compare_columns] for r in comparisons],
         "widths": [38, 30, 16, 26, 35, 38, 12, 27, 100, 15]},
        {"name": "剩余问题", "headers": unresolved_columns, "rows": [[r.get(k) for k in unresolved_columns] for r in unresolved],
         "widths": [16, 29, 30, 40, 28, 12, 15, 80, 85]},
        {"name": "SKU核验", "headers": ["ASIN", "处理结果", "HTML哈希", "属性绑定", "卖点绑定", "排名类目绑定", "属性事实丢失", "卖点丢失", "DOM未捕获属性", "卖点DOM顺序"],
         "rows": [[r["asin"], r["result"], r["html_hash_verified"], r["attributes_exact_candidate_binding"],
            r["bullets_exact_candidate_binding"], r["ranking_category_binding"], r["attributes_lost"], r["bullets_lost"],
            len(r["missing_html_attribute_pairs"]), r["dom_bullet_order_binding"]] for r in fidelity],
         "widths": [16, 40, 18, 18, 18, 20, 20, 18, 24, 20]},
        {"name": "登记表声明对照", "headers": ["历史编号", "来源", "判定", "说明", "原始声明"],
         "rows": [[r["historical_id"], r["section"], r["resolution"], r["reason"], canonical(r["historical_entry"])] for r in register_review],
         "widths": [46, 30, 28, 110, 130]},
    ]
    oversized = [(s["name"], i + 2, j + 1, len(v)) for s in sheets for i, r in enumerate(s["rows"])
                 for j, v in enumerate(r) if isinstance(v, str) and len(v) > 32767]
    if oversized:
        raise RuntimeError(f"XLSX_CELL_SIZE_REQUIRES_EXPLICIT_FULL_TEXT_HANDLING: {oversized[:5]}")
    write(target / "workbook_payload.json", {"sheets": sheets})
    report = ("# 5480 SKU 全量离线回归交付\n\n"
        f"处理结果：{summary['replayed']}/5480重解析；{summary['replay_unverified']}条未验证。5480集合完整：{exact_scope}。\n\n"
        f"Source Gate：{canonical(gate_summary(summary['source_gate']))}\n\nClosure Gate：{canonical(gate_summary(summary['closure_gate']))}\n\n"
        f"当前候选问题：{summary['current_issue_count']}条，影响{summary['current_affected_skus']}个SKU。\n\n"
        f"历史原始审查6933条与r3闭合审查1109条分别对照：{canonical(counts)}。不能将两层记录数直接当作去重错误数。\n\n"
        "CONFIRMED_FIXED=字段改变且明确修复谓词通过；STILL_ISSUE=当前仍报相同字段/属性；RULE_CHANGE_ONLY=相关字段未改变但审查不再报；EVIDENCE_INSUFFICIENT=无法证明具体问题解决。\n\n"
        "全部原始数据、旧审查和冻结manifest保持原状。候选中保留问题字段、原始属性和卖点；未减少到5478。未访问Amazon、未调用翻译、未提交Git。中文页仅身份对照，尚未生成中文翻译。\n\n"
        f"属性事实丢失：{summary['attribute_facts_lost_count']}；卖点丢失：{summary['feature_bullets_lost_count']}；原始输入哈希未变化：{all(unchanged.values())}。\n\n"
        "字段位置/绑定核验通过并不代表Amazon源事实全部正确；属性内错误单位、身份冲突和证据不足仍保留在unresolved.json。\n")
    with (target / "交付说明.md").open("x", encoding="utf-8") as stream:
        stream.write(report)
    print(json.dumps({"final_review": str(target), "summary": summary}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
