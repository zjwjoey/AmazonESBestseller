"""Offline source-audit contract, historical replay and corruption regressions."""
from __future__ import annotations

import copy
import gzip
import json
import socket
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.detail_bridge5500 import parse_candidate_detail
from amazon_es_bestseller.multisource.storage import canonical_bytes, digest, immutable_json, sealed
from amazon_es_bestseller.source_audit.audit import audit_snapshot
from amazon_es_bestseller.source_audit.cli import main
from amazon_es_bestseller.source_audit.contracts import file_sha, read_json, verify_bundle
from amazon_es_bestseller.source_audit.loader import Snapshot, snapshot
from amazon_es_bestseller.source_audit.report import prepare_translation, repair_plan, run_audit
from amazon_es_bestseller.source_audit.translation_prep import prepare

URL = "https://www.amazon.es/dp/B0GK9PLT15?th=1"
HTML = """<html><head><link rel="canonical" href="https://www.amazon.es/dp/B0GK9PLT15"></head>
<body><input id="ASIN" value="B0GK9PLT15"><div id="dp"><span id="productTitle">Botella de agua 500 ml, 2 unidades</span>
<a id="bylineInfo">Marca: Ejemplo</a><div id="availability">En stock</div>
<div id="corePriceDisplay_desktop_feature_div"><span class="a-price"><span class="a-offscreen">12,99 €</span></span></div>
<div id="wayfinding-breadcrumbs_feature_div"><ul><li><a>Hogar</a></li><li><a>Cocina</a></li><li><a>Botellas</a></li><li><a>Agua</a></li></ul></div>
<div id="productOverview_feature_div"><table><tr><td><span>Color</span></td><td><span>Natural</span></td></tr>
<tr><td><span>Material</span></td><td><span>Natural</span></td></tr>
<tr><td><span>Capacidad</span></td><td><span>500 ml</span></td></tr>
<tr><td><span>Marca</span></td><td><span>Ejemplo</span></td></tr></table></div>
<div id="feature-bullets"><ul><li><span class="a-list-item">Dos botellas de 500 ml</span></li></ul></div>
<div id="productDescription"><p>Botella reutilizable.</p><p>Sin bisfenol.</p></div></div></body></html>"""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("NETWORK_FORBIDDEN_IN_SOURCE_AUDIT")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def make_batch(root, *, html=HTML, change=None, status="SUCCESS", count=1, export_format=None):
    root.mkdir(parents=True)
    selected, tasks = [], []
    for n in range(count):
        cid, sid = f"candidate_{n}", f"selection_{n}"
        task = dict(task_id=f"detail_{n}", candidate_id=cid, selection_id=sid, request_url=URL,
                    normalized_product_url=URL, raw_product_url=URL, source_snapshot_id="historical_sample",
                    source_memberships=[], run_id="source_audit_test")
        tasks.append(task)
        selected.append(dict(candidate_id=cid, selection_id=sid, absolute_product_url=URL))
    immutable_json(root / "selected_5500.json", selected)
    immutable_json(root / "detail_plan_5500.json", dict(run_id='source_audit_test', tasks=tasks, selection_sha256=file_sha(root / "selected_5500.json")))
    if count != 1:
        return tasks
    task = tasks[0]
    hp = root / "tasks/detail_0/html/source.html"
    hp.parent.mkdir(parents=True)
    hp.write_text(html, encoding="utf8")
    meta = dict(status_code=200, final_url=URL, collected_at="2026-10-01T00:00:00+00:00")
    parsed = parse_candidate_detail(task, html, meta)
    assert parsed["status"] == "SUCCESS", parsed
    record = parsed["record"]
    if change:
        change(record)
    rp = root / "receipts/detail_0_1.json"
    immutable_json(rp, sealed(dict(task_id=task["task_id"], run_id=task["run_id"], request_url=URL,
        final_url=URL, state="NORMAL", status_code=200, html_sha256=file_sha(hp),
        plan_sha256=digest(read_json(root / "detail_plan_5500.json")))))
    outcome = dict(status=status, record=record, candidate_id=task["candidate_id"], selection_id=task["selection_id"],
        task_id=task["task_id"], run_id=task["run_id"], html_path=str(hp.resolve()), html_sha256=file_sha(hp),
        receipt_path=str(rp.resolve()), receipt_sha256=file_sha(rp))
    immutable_json(root / "tasks/detail_0/checkpoint.json", sealed(outcome))
    if export_format == "jsonl":
        (root / "details_5500_raw.jsonl").write_bytes(canonical_bytes(record) + b"\n")
    elif export_format == "csv":
        from amazon_es_bestseller.source_audit.contracts import csv_bytes
        (root / "details_5500.csv").write_bytes(csv_bytes([record], list(record)))
    return task, record, outcome


def run_case(tmp_path, *, change=None, html=HTML, status="SUCCESS", export_format=None):
    source, snap = tmp_path / "source", tmp_path / "snapshot"
    make_batch(source, change=change, html=html, status=status, export_format=export_format)
    snapshot(source, snap, data_kind="SYNTHETIC_TEST")
    return audit_snapshot(Snapshot(snap)), source, snap


def test_review_normalized_numbers_are_quarantined(tmp_path):
    result, _, _ = run_case(tmp_path, change=lambda r: r.update(current_price=999.0, rating_value=99.0, monthly_bought_min=999999))
    names = {i['field_name'] for i in result['issues']}
    assert {'current_price', 'rating_value', 'monthly_bought_min'} <= names
    p = prepare(result['products'], input_manifest_sha256='a' * 64)['products'][0]
    assert 'current_price' not in p['passthrough_fields']
    assert p['raw_record']['current_price'] == 999.0


def test_review_product_url_wrong_asin_blocks(tmp_path):
    result, _, _ = run_case(tmp_path, change=lambda r: r.update(product_url='https://www.amazon.es/dp/B000000000'))
    assert result['products'][0]['preparation_status'] == 'IDENTITY_BLOCKED'


def test_review_all_exports_compared(tmp_path):
    from amazon_es_bestseller.source_audit.contracts import csv_bytes
    source, snap = tmp_path/'s', tmp_path/'f'
    _, r, _ = make_batch(source, export_format='jsonl')
    wrong = {**r, 'title_es_raw': 'OTRO PRODUCTO'}
    (source/'details_5500.csv').write_bytes(csv_bytes([wrong], list(wrong)))
    snapshot(source, snap, data_kind='SYNTHETIC_TEST')
    result = audit_snapshot(Snapshot(snap))
    assert not result['summary']['batch_integrity_passed']
    assert any(i['issue_code'] == 'EXPORT_CHECKPOINT_MISMATCH' for i in result['issues'])


def test_review_parsed_detail_compared(tmp_path):
    source, snap = tmp_path/'s', tmp_path/'f'
    _, r, _ = make_batch(source)
    (source/'tasks/detail_0/parsed_detail.json').write_text(json.dumps({**r, 'asin':'B000000000'}), encoding='utf8')
    snapshot(source, snap, data_kind='SYNTHETIC_TEST')
    result = audit_snapshot(Snapshot(snap))
    assert not result['summary']['batch_integrity_passed']
    assert any(i['issue_code'] == 'PARSED_CHECKPOINT_MISMATCH' for i in result['issues'])


def test_review_family_metadata_needs_html_proof(tmp_path):
    result, _, _ = run_case(tmp_path, change=lambda r: r.update(variation_family_asins=['B000000000']))
    assert any(i['field_name'] == 'variation_family_asins' for i in result['issues'])


def test_review_same_capacity_conflict_held(tmp_path):
    html = HTML.replace('<div id="availability">', '<div id="variation_name"><span class="selection">900 ml</span></div><div id="availability">')
    result, _, _ = run_case(tmp_path, html=html)
    assert any(i['issue_code'] == 'SPEC_EVIDENCE_CONFLICT' for i in result['issues'])
    assert result['products'][0]['field_statuses']['specification_es'] != 'READY'


def test_review_title_source_rehash_not_enough(tmp_path):
    from amazon_es_bestseller.source_audit.translation_prep import validate_preparation
    result, _, _ = run_case(tmp_path)
    prep = prepare(result['products'], input_manifest_sha256='a' * 64)
    u = next(u for u in prep['units'] if u['field_name'] == 'title_es_raw')
    u['source_text_es'] = 'OTRO PRODUCTO 999 kg'
    u['source_text_sha256'] = digest(u['source_text_es'].encode('utf8'))
    with pytest.raises(ValueError):
        validate_preparation(prep)


def test_review_manual_brand_not_automatic(tmp_path):
    _, _, snap = run_case(tmp_path, change=lambda r: r.update(brand_raw='Inventada'))
    run_audit(snap, tmp_path/'a')
    p = repair_plan(tmp_path/'a', tmp_path/'p')
    assert p['automatic_offline_candidates'] == 0
    assert p['manual_candidates'] == 1


def test_review_repair_reaudit_prepare_closed_loop(tmp_path):
    from amazon_es_bestseller.source_audit.repair import repair_copy, reaudit_repair
    _, source, snap = run_case(tmp_path, change=lambda r: r.update(attributes='collapsed'))
    original = file_sha(source/'tasks/detail_0/checkpoint.json')
    run_audit(snap, tmp_path/'a')
    repair_copy(tmp_path/'a', tmp_path/'r')
    reaudit_repair(tmp_path/'r', tmp_path/'reaudit')
    prepare_translation(tmp_path/'reaudit', tmp_path/'prep')
    products = [json.loads(s) for s in (tmp_path/'prep/translation_ready_products.jsonl').read_text(encoding='utf8').splitlines()]
    assert products[0]['field_statuses']['attributes'] == 'READY'
    assert isinstance(products[0]['raw_record']['attributes'], list)
    assert file_sha(source/'tasks/detail_0/checkpoint.json') == original


@pytest.mark.parametrize('field,value', [('current_price', True), ('rating_value', 6.0),
    ('rating_count', -1), ('monthly_bought_min', '100')])
def test_review_numeric_type_and_range(tmp_path, field, value):
    result, _, _ = run_case(tmp_path, change=lambda r: r.update({field:value}))
    assert any(i['field_name'] == field and i['issue_code'] == 'NORMALIZED_NUMBER_UNVERIFIED' for i in result['issues'])


def test_review_correct_parallel_artifacts_are_accepted(tmp_path):
    from amazon_es_bestseller.source_audit.contracts import csv_bytes
    source, snap = tmp_path/'s', tmp_path/'f'
    _, r, _ = make_batch(source, export_format='jsonl')
    (source/'details_5500.csv').write_bytes(csv_bytes([r], list(r)))
    (source/'tasks/detail_0/parsed_detail.json').write_text(json.dumps(r), encoding='utf8')
    snapshot(source, snap, data_kind='SYNTHETIC_TEST')
    assert audit_snapshot(Snapshot(snap))['summary']['batch_integrity_passed']


def test_review_equivalent_units_do_not_conflict(tmp_path):
    html = HTML.replace('<div id="availability">', '<div id="variation_name"><span class="selection">0,5 L</span></div><div id="availability">')
    result, _, _ = run_case(tmp_path, html=html)
    assert not any(i['issue_code'] == 'SPEC_EVIDENCE_CONFLICT' for i in result['issues'])
    assert result['products'][0]['field_statuses']['specification_es'] == 'READY'


@pytest.mark.parametrize('mutation', ['ledger', 'derived', 'binding'])
def test_review_repair_forged_self_consistent_bundle_rejected(tmp_path, mutation):
    from amazon_es_bestseller.source_audit.repair import repair_copy, reaudit_repair
    _, _, snap = run_case(tmp_path, change=lambda r: r.update(attributes='collapsed'))
    run_audit(snap, tmp_path/'a')
    repair_copy(tmp_path/'a', tmp_path/'r')
    manifest_path = tmp_path/'r/manifest.json'
    manifest = read_json(manifest_path)
    if mutation == 'binding':
        manifest['inputs']['audit_manifest_sha256'] = '0' * 64
    else:
        name = 'source_repair_application_ledger.jsonl' if mutation == 'ledger' else 'source_repaired_products.jsonl'
        path = tmp_path/'r'/name
        if mutation == 'ledger':
            path.write_bytes(b'')
        else:
            rows = [json.loads(s) for s in path.read_text(encoding='utf8').splitlines()]
            rows[0]['derived_record']['product_url'] = 'https://www.amazon.es/dp/B000000000'
            path.write_bytes(b''.join(canonical_bytes(r)+b'\n' for r in rows))
        manifest['files'][name] = file_sha(path)
    manifest_path.write_bytes(canonical_bytes(manifest))
    with pytest.raises(ValueError):
        reaudit_repair(tmp_path/'r', tmp_path/'reaudit')
    assert not (tmp_path/'reaudit/manifest.json').exists()


def test_review_reaudit_cli_idempotent_and_manual_stays_held(tmp_path):
    from amazon_es_bestseller.source_audit.repair import repair_copy
    _, _, snap = run_case(tmp_path, change=lambda r: r.update(attributes='collapsed', brand_raw='Inventada'))
    run_audit(snap, tmp_path/'a')
    repair_copy(tmp_path/'a', tmp_path/'r')
    args = ['reaudit-repair', '--repair', str(tmp_path/'r'), '--out', str(tmp_path/'reaudit')]
    assert main(args) == 0
    first = file_sha(tmp_path/'reaudit/manifest.json')
    assert main(args) == 0
    assert file_sha(tmp_path/'reaudit/manifest.json') == first
    prepare_translation(tmp_path/'reaudit', tmp_path/'prep')
    row = json.loads((tmp_path/'prep/translation_ready_products.jsonl').read_text(encoding='utf8'))
    assert row['raw_record']['brand_raw'] == 'Inventada'
    assert 'brand_raw' not in row['passthrough_fields']
    assert row['field_statuses']['attributes'] == 'READY'


def test_current_html_replay_identity_and_independent_fields(tmp_path):
    result, _, _ = run_case(tmp_path)
    p = result["products"][0]
    assert p["identity_passed"]
    assert p["field_statuses"]["title_es_raw"] == "READY"
    assert p["preparation_status"] in {"READY", "PARTIAL_READY"}
    assert result["summary"]["success_count"] == 1
    assert p['translation_source']['specification_es'] == '500 ml'
    assert p['specification_derivation']['source_field'] == 'title_es_raw'
    assert 'specification_es' not in p['raw_record']


@pytest.mark.parametrize("field,value,code", [
    ("title_es_raw", "Productos recomendados", "NAVIGATION_TITLE"),
    ("title_es_raw", "<b>Botella</b>", "HTML_RESIDUE"),
    ("title_es_raw", "Botella 中文", "LANGUAGE_POLLUTION"),
    ("title_es_raw", "Botella \ufffd", "MOJIBAKE"),
    ("title_es_raw", "", "TITLE_REQUIRED"),
    ("product_description_raw", "Ver los 100 más vendidos", "CONTENT_CONTAMINATION"),
    ("brand_raw", "Vendido por Comercio Distinto", "BRAND_EVIDENCE_CONFLICT"),
    ("parent_asin", "B0GK9PLT15", "PARENT_RELATION_UNCONFIRMED"),
    ("attributes", "Color: Negro; Capacidad: 500 ml", "DETAIL_ATTRIBUTES_SCHEMA"),
    ("feature_bullets_raw", "Una cadena", "BULLETS_NOT_LIST"),
    ("current_price_raw", "200,00 €", "CURRENT_ABOVE_REFERENCE"),
    ("current_price_raw", "12.99 USD", "PRICE_OR_CURRENCY_REVIEW"),
    ("discount_rate", 0.99, "DISCOUNT_UNVERIFIED"),
])
def test_historical_risks_are_detected_without_source_rewrite(tmp_path, field, value, code):
    def change(r):
        r[field] = value
        if code == "CURRENT_ABOVE_REFERENCE":
            r["reference_price_raw"] = "10,00 €"
    result, source, snap = run_case(tmp_path, change=change)
    assert code in {i["issue_code"] for i in result["issues"]}
    assert read_json(source / "tasks/detail_0/checkpoint.json")["payload"]["record"][field] == value
    assert read_json(snap / "tasks/detail_0/checkpoint.json")["payload"]["record"][field] == value


def test_actual_saved_real_html_replay_and_hash(tmp_path):
    fixture = Path(__file__).parent / "fixtures/detail_bridge5500"
    provenance = read_json(fixture / "provenance.json")
    raw = gzip.decompress((fixture / "actual_product.html.gz").read_bytes())
    assert digest(raw) == provenance["sha256"]
    result, _, _ = run_case(tmp_path, html=raw.decode("utf8"))
    p = result["products"][0]
    assert p["identity_passed"]
    assert p["field_statuses"]["title_es_raw"] == "READY"
    assert p["raw_record"]["attributes"]
    assert not any(i['issue_code'] == 'UNIT_TYPE_CONFLICT' for i in result['issues'])
    assert p['field_statuses']['attributes'] == 'READY'


def test_partial_missing_description_is_not_identity_failure(tmp_path):
    html = HTML.replace('<div id="productDescription"><p>Botella reutilizable.</p><p>Sin bisfenol.</p></div>', '')
    result, _, _ = run_case(tmp_path, html=html)
    assert result["products"][0]["preparation_status"] == "PARTIAL_READY"
    assert any(i["field_name"] == "product_description_raw" and i["audit_status"] == "SOURCE_NOT_DISPLAYED" for i in result["issues"])


def test_empty_present_source_is_not_claimed_not_displayed(tmp_path):
    html = HTML.replace('Botella reutilizable.', '').replace('Sin bisfenol.', '')
    result, _, _ = run_case(tmp_path, html=html)
    assert any(i["field_name"] == "product_description_raw" and i["audit_status"] == "FIELD_MISSING" for i in result["issues"])


def test_identity_mismatch_blocks_all_fields_even_matching_title(tmp_path):
    result, _, _ = run_case(tmp_path, change=lambda r:r.update(asin="B000000001"))
    assert result["products"][0]["preparation_status"] == "IDENTITY_BLOCKED"
    assert not prepare(result["products"], input_manifest_sha256="a" * 64)["units"]


@pytest.mark.parametrize("status,expected", [("IDENTITY_REVIEW", "IDENTITY_BLOCKED"), ("SOURCE_BLOCKED", "SOURCE_BLOCKED"),
    ("ACCESS_STOP", "SOURCE_BLOCKED"), ("FAILED", "NOT_READY"), ("PARSE_FAILED", "NOT_READY"), ("SOURCE_MISSING", "NOT_READY")])
def test_collection_states_remain_distinct(tmp_path, status, expected):
    result, _, _ = run_case(tmp_path, status=status)
    assert result["products"][0]["preparation_status"] == expected
    assert result["summary"]["collection_status_counts"] == {status: 1}


def test_ordered_duplicate_labels_and_unknown_parameters_preserved(tmp_path):
    html = HTML.replace('</table></div>', '<tr><td><span>Color</span></td><td><span>Natural</span></td></tr><tr><td><span>Campo futuro desconocido</span></td><td><span>Dato especial</span></td></tr></table></div>')
    result, _, _ = run_case(tmp_path, html=html)
    prepared = prepare(result["products"], input_manifest_sha256="b" * 64)
    labels = [u for u in prepared["units"] if u["attribute_role"] == "label"]
    assert len([u for u in labels if u["source_text_es"] == "Color"]) == 2
    assert any(u["source_text_es"] == "Campo futuro desconocido" for u in labels)
    assert len({u["translation_unit_id"] for u in labels}) == len(labels)


def test_semantic_cache_distinguishes_color_material_and_identity_passthrough(tmp_path):
    result, _, _ = run_case(tmp_path)
    prepared = prepare(result["products"], input_manifest_sha256="b" * 64)
    values = [u for u in prepared["units"] if u["attribute_role"] == "value"]
    natural = [u for u in values if u["source_text_es"] == "Natural"]
    assert len(natural) == 2
    assert natural[0]["semantic_reuse_key"] != natural[1]["semantic_reuse_key"]
    for u in values:
        if u["source_text_es"] in {"500 ml", "Ejemplo"}:
            assert u["preparation_status"] == "PASSTHROUGH" and not u["model_translation_required"]
    assert all(u["field_name"] not in {"current_price_raw", "asin", "brand_raw"} for u in prepared["units"])
    title = next(u for u in prepared["units"] if u["field_name"] == "title_es_raw")
    assert "500 ml" in title["protected_terms"]


def test_cache_requires_bound_source_semantic_qa_and_translation_hash(tmp_path):
    result, _, _ = run_case(tmp_path)
    first = prepare(result["products"], input_manifest_sha256="c" * 64)
    title = next(u for u in first["units"] if u["field_name"] == "title_es_raw")
    key = title["semantic_reuse_key"]
    entry = dict(semantic_reuse_key=key, source_text_sha256=title["source_text_sha256"],
                 status="success", qa_status="pass", translation_text="水瓶", translation_sha256=digest("水瓶".encode()))
    reused = prepare(result["products"], input_manifest_sha256="c" * 64, cache_memory={key: entry})
    assert reused["summary"]["cache_reusable_units"] == 1
    for wrong in ({"qa_status": "failed"}, {"status": "partial"}, {"translation_sha256": "bad"}, {"semantic_reuse_key": "wrong"}, {"source_text_sha256": "wrong"}):
        bad = prepare(result["products"], input_manifest_sha256="c" * 64, cache_memory={key: {**entry, **wrong}})
        assert bad["summary"]["cache_reusable_units"] == 0


@pytest.mark.parametrize("export_format", ["jsonl", "csv"])
def test_detail5500_actual_export_formats(tmp_path, export_format):
    result, _, _ = run_case(tmp_path, export_format=export_format)
    assert result["summary"]["detail_input_count"] == 1
    assert result["summary"]["identity_passed_count"] == 1
    assert "EXPORT_CHECKPOINT_MISMATCH" not in {i["issue_code"] for i in result["issues"]}


def test_5500_pending_is_not_5500_details_success(tmp_path):
    source, snap = tmp_path / "source", tmp_path / "snap"
    make_batch(source, count=5500)
    snapshot(source, snap)
    result = audit_snapshot(Snapshot(snap), expected_count=5500)
    assert result["summary"]["input_task_count"] == 5500
    assert result["summary"]["identity_passed_count"] == 0
    assert result["summary"]["blocked_products"] == 5500
    assert not result["summary"]["formal_5500_fully_audited"]


def test_safe_repairs_proposals_and_reports_are_idempotent(tmp_path):
    result, source, snap = run_case(tmp_path, change=lambda r:r.update(attributes="collapsed"))
    original_hashes = {str(p): file_sha(p) for p in source.rglob('*') if p.is_file()}
    audit, repair, prep = tmp_path / "audit", tmp_path / "repair", tmp_path / "prep"
    run_audit(snap, audit)
    run_audit(snap, audit)
    repair_plan(audit, repair)
    prepare_translation(audit, prep)
    prepare_translation(audit, prep)
    plan = read_json(repair / "source_repair_plan.json")
    assert plan["automatic_offline_candidates"] > 0
    assert not plan["production_apply"]
    rows = [json.loads(x) for x in (prep / "translation_units.jsonl").read_text(encoding="utf8").splitlines()]
    assert any(u["field_name"] == "title_es_raw" for u in rows)
    assert not any(u["field_name"] == "attributes" for u in rows)
    assert original_hashes == {str(p): file_sha(p) for p in source.rglob('*') if p.is_file()}
    verify_bundle(prep, "translation_preparation_manifest.json", kind="translation_preparation")


def test_snapshot_tamper_and_path_escape_fail_closed(tmp_path):
    _, _, snap = run_case(tmp_path)
    (snap / "selected_5500.json").write_text('[]', encoding='utf8')
    with pytest.raises(ValueError, match="SHA_MISMATCH"):
        Snapshot(snap)
    from amazon_es_bestseller.source_audit.contracts import child, separate
    for path in ('../source/file', '/etc/passwd', 'C:/outside.json'):
        with pytest.raises(ValueError):
            child(snap, path)
    with pytest.raises(ValueError, match="OVERLAP"):
        separate(snap, snap / 'child')


def test_snapshot_detects_mutation_before_publication(tmp_path, monkeypatch):
    source, snap = tmp_path / 'source', tmp_path / 'snap'
    make_batch(source)
    import amazon_es_bestseller.source_audit.loader as loader
    actual = loader.immutable_bytes
    changed = False
    def mutate(path, body):
        nonlocal changed
        actual(path, body)
        if not changed:
            changed = True
            (source / 'selected_5500.json').write_bytes(b'[]')
    monkeypatch.setattr(loader, 'immutable_bytes', mutate)
    with pytest.raises(ValueError, match='CHANGED_DURING_SNAPSHOT'):
        snapshot(source, snap)
    assert not (snap / 'source_snapshot_manifest.json').exists()


def test_invalid_receipt_binding_quarantined_without_repair(tmp_path):
    source, snap = tmp_path / 'source', tmp_path / 'snap'
    make_batch(source)
    path = source / 'tasks/detail_0/checkpoint.json'
    data = read_json(path)['payload']
    data['receipt_sha256'] = '0' * 64
    path.write_bytes(canonical_bytes(sealed(data)))
    snapshot(source, snap)
    result = audit_snapshot(Snapshot(snap))
    assert result['summary']['blocked_products'] == 1
    assert any(i['issue_code'] == 'EVIDENCE_INVALID' for i in result['issues'])
    assert not result['repairs']


def test_cli_all_commands_and_frozen_input_required(tmp_path, capsys):
    source = tmp_path / 'source'
    make_batch(source)
    snap, audit = tmp_path / 'snap', tmp_path / 'audit'
    assert main(['audit', '--input', str(source), '--out', str(audit)]) == 2
    assert main(['snapshot', '--input', str(source), '--out', str(snap), '--data-kind', 'SYNTHETIC_TEST']) == 0
    assert main(['inspect', '--input', str(snap), '--out', str(tmp_path / 'inspect')]) == 0
    assert main(['audit', '--input', str(snap), '--out', str(audit), '--expected-count', '1']) == 0
    assert main(['repair-plan', '--audit', str(audit), '--out', str(tmp_path / 'repair')]) == 0
    assert main(['prepare-translation', '--audit', str(audit), '--out', str(tmp_path / 'prep')]) == 0
    assert main(['report', '--audit', str(audit)]) == 0
    assert 'translation_api_requests' in capsys.readouterr().out


def test_input_manifest_mismatch_blocks_every_product(tmp_path):
    source, snap = tmp_path / 'source', tmp_path / 'snap'
    make_batch(source)
    plan = read_json(source / 'detail_plan_5500.json')
    plan['selection_sha256'] = '0' * 64
    (source / 'detail_plan_5500.json').write_bytes(canonical_bytes(plan))
    snapshot(source, snap)
    result = audit_snapshot(Snapshot(snap))
    assert not result['summary']['batch_integrity_passed']
    assert result['products'][0]['preparation_status'] == 'NOT_READY'


def test_frozen_input_and_legacy_cache_never_written(tmp_path):
    _, _, snap = run_case(tmp_path)
    audit, out = tmp_path / 'audit', tmp_path / 'prep'
    run_audit(snap, audit)
    cache = tmp_path / 'legacy.json'
    cache.write_text('{broken', encoding='utf8')
    before = cache.read_bytes()
    with pytest.raises(ValueError):
        prepare_translation(audit, out, cache=cache)
    assert cache.read_bytes() == before
    assert not list(tmp_path.glob('*.corrupt-*'))


def test_selected_variation_conflict_cannot_change_product_identity(tmp_path):
    result, _, _ = run_case(tmp_path, change=lambda r:r.update(selected_variation_raw='10 L'))
    assert any(i['issue_code'] == 'VARIANT_SELECTION_CONFLICT' for i in result['issues'])
    assert result['products'][0]['identity_passed']
    assert result['products'][0]['field_statuses']['selected_variation_raw'] == 'SOURCE_REVIEW_REQUIRED'


def test_breadcrumb_depth_and_parent_path_are_not_flattened(tmp_path):
    result, _, _ = run_case(tmp_path)
    prepared = prepare(result['products'], input_manifest_sha256='d' * 64)
    categories = [u for u in prepared['units'] if u['field_name'] == 'category_path']
    assert len(categories) == 4
    assert categories[-1]['category_parent_path'] == ['Hogar', 'Cocina', 'Botellas']
    assert categories[0]['translation_field_type'] == 'category'


def test_false_structured_category_path_is_held(tmp_path):
    result, _, _ = run_case(tmp_path, change=lambda r:r['category_evidence'].update(product_breadcrumb_category_path=['Inventado']))
    assert result['products'][0]['field_statuses']['category_path'] == 'SOURCE_REVIEW_REQUIRED'
    assert not any(u['field_name'] == 'category_path' for u in prepare(result['products'], input_manifest_sha256='d' * 64)['units'])


def test_generic_quantity_does_not_override_explicit_title_spec(tmp_path):
    html = HTML.replace('<span>Capacidad</span>', '<span>Número de productos</span>').replace('<span>500 ml</span>', '<span>1</span>')
    result, _, _ = run_case(tmp_path, html=html)
    p = result['products'][0]
    assert p['translation_source']['specification_es'] == '500 ml'
    assert p['specification_derivation']['source_field'] == 'title_es_raw'


def test_structural_position_is_section_local_and_duplicates_block(tmp_path):
    html = HTML.replace('</div></body>', '<div id="prodDetails"><table><tr><th>Modelo</th><td>V16</td></tr></table></div></div></body>')
    result, _, _ = run_case(tmp_path, html=html)
    assert result['products'][0]['field_statuses']['attributes'] == 'READY'
    assert not any(i['issue_code'] == 'DETAIL_POSITION_DUPLICATE' for i in result['issues'])


@pytest.mark.parametrize('attribute_change', [
    {'position': {'bad': 1}}, {'source': None}, {'label_raw': None}, {'value_raw': ''},
    {'label_raw': 'Peso', 'value_raw': '500 ml'},
])
def test_malformed_attributes_hold_only_the_structure(tmp_path, attribute_change):
    def change(r):
        r['attributes'][0].update(attribute_change)
        r['ordered_detail_evidence'] = copy.deepcopy(r['attributes'])
    result, _, _ = run_case(tmp_path, change=change)
    p = result['products'][0]
    assert p['field_statuses']['attributes'] == 'SOURCE_REVIEW_REQUIRED'
    assert p['field_statuses']['title_es_raw'] == 'READY'
    units = prepare([p], input_manifest_sha256='d' * 64)['units']
    assert any(u['field_name'] == 'title_es_raw' for u in units)
    assert not any(u['field_name'] == 'attributes' for u in units)


def test_repair_copy_is_independent_and_not_implicitly_ready(tmp_path):
    from amazon_es_bestseller.source_audit.repair import repair_copy
    _, source, snap = run_case(tmp_path, change=lambda r:r.update(attributes='collapsed'))
    audit, repaired = tmp_path / 'audit', tmp_path / 'repaired'
    run_audit(snap, audit)
    original = file_sha(source / 'tasks/detail_0/checkpoint.json')
    summary = repair_copy(audit, repaired)
    assert summary['repaired_fields_in_new_copy'] > 0
    row = json.loads((repaired / 'source_repaired_products.jsonl').read_text(encoding='utf8'))
    assert row['source_record']['attributes'] == 'collapsed'
    assert isinstance(row['derived_record']['attributes'], list)
    assert row['preparation_status'] == 'NOT_REAUDITED'
    assert original == file_sha(source / 'tasks/detail_0/checkpoint.json')


def test_contract_validator_detects_pairing_and_source_tamper(tmp_path):
    from amazon_es_bestseller.source_audit.translation_prep import validate_preparation
    result, _, _ = run_case(tmp_path)
    prepared = prepare(result['products'], input_manifest_sha256='d' * 64)
    assert validate_preparation(prepared)['status'] == 'PASS'
    bad = copy.deepcopy(prepared)
    bad['units'][0]['source_text_es'] += 'changed'
    with pytest.raises(ValueError, match='HASH_INVALID'):
        validate_preparation(bad)
    bad = copy.deepcopy(prepared)
    attr = next(u for u in bad['units'] if u['attribute_role'] == 'value')
    attr['attribute_position'] = 999
    with pytest.raises(ValueError, match='PAIRING_INVALID'):
        validate_preparation(bad)


def test_output_conflicts_never_overwrite_prior_delivery(tmp_path):
    _, _, snap = run_case(tmp_path)
    audit = tmp_path / 'audit'
    run_audit(snap, audit)
    original = file_sha(audit / 'manifest.json')
    with pytest.raises(ValueError, match='IMMUTABLE_OUTPUT_CONFLICT'):
        run_audit(snap, audit, expected_count=2)
    assert file_sha(audit / 'manifest.json') == original


def test_real_cached_detail_state_with_exact_saved_metadata(tmp_path):
    source, snap = tmp_path / 'source', tmp_path / 'snap'
    task, record, outcome = make_batch(source)
    mp = source / 'tasks/detail_0/response_metadata.json'
    immutable_json(mp, dict(requested_url=URL, final_url=URL, status_code=200, access_state='NORMAL'))
    outcome.update(status='REUSED_VALID_CACHE', cache_metadata_path=str(mp.resolve()), cache_metadata_sha256=file_sha(mp))
    (source / 'tasks/detail_0/checkpoint.json').write_bytes(canonical_bytes(sealed(outcome)))
    snapshot(source, snap, data_kind='SYNTHETIC_TEST')
    result = audit_snapshot(Snapshot(snap))
    assert result['summary']['cache_reused_count'] == 1
    assert result['summary']['identity_passed_count'] == 1


def test_http_challenge_in_saved_html_is_not_retried(tmp_path):
    source, snap = tmp_path / 'source', tmp_path / 'snap'
    _, _, outcome = make_batch(source)
    hp = Path(outcome['html_path'])
    hp.write_text('<html><body>Robot Check CAPTCHA</body></html>', encoding='utf8')
    outcome['html_sha256'] = file_sha(hp)
    rp = Path(outcome['receipt_path'])
    receipt = read_json(rp)['payload']; receipt['html_sha256'] = file_sha(hp)
    rp.write_bytes(canonical_bytes(sealed(receipt)))
    outcome['receipt_sha256'] = file_sha(rp)
    (source / 'tasks/detail_0/checkpoint.json').write_bytes(canonical_bytes(sealed(outcome)))
    snapshot(source, snap, data_kind='SYNTHETIC_TEST')
    result = audit_snapshot(Snapshot(snap))
    assert result['products'][0]['preparation_status'] == 'SOURCE_BLOCKED'
    assert result['summary']['amazon_requests'] == 0


@pytest.mark.parametrize('fault', ['hash', 'final_url', 'status_code'])
def test_response_metadata_is_bound_not_merely_copied(tmp_path, fault):
    source, snap = tmp_path / 'source', tmp_path / 'snap'
    _, _, outcome = make_batch(source)
    metadata = dict(requested_url=URL, final_url=URL, status_code=200, access_state='NORMAL')
    if fault == 'final_url':
        metadata['final_url'] = 'https://www.amazon.es/dp/B000000001'
    if fault == 'status_code':
        metadata['status_code'] = 403
    path = source / 'tasks/detail_0/response_metadata.json'
    immutable_json(path, metadata)
    outcome.update(response_metadata_path=str(path.resolve()), response_metadata_sha256='0' * 64 if fault == 'hash' else file_sha(path))
    (source / 'tasks/detail_0/checkpoint.json').write_bytes(canonical_bytes(sealed(outcome)))
    snapshot(source, snap, data_kind='SYNTHETIC_TEST')
    result = audit_snapshot(Snapshot(snap))
    assert not result['products'][0]['identity_passed']
    assert any(i['issue_code'] == 'EVIDENCE_INVALID' for i in result['issues'])


@pytest.mark.parametrize('command', ['prepare-translation', 'repair-plan', 'repair-copy'])
def test_no_derived_output_may_be_published_inside_frozen_input(tmp_path, command):
    _, _, snap = run_case(tmp_path)
    audit = tmp_path / 'audit'
    run_audit(snap, audit)
    destination = snap / 'must_not_create'
    assert main([command, '--audit', str(audit), '--out', str(destination)]) == 2
    assert not destination.exists()


def test_final_acceptance_requires_exact_checkpoint_bindings(tmp_path):
    source, snap = tmp_path / 'source', tmp_path / 'snap'
    make_batch(source)
    plan = read_json(source / 'detail_plan_5500.json')
    proof = dict(passed=True, plan_sha256=digest(plan), success_count=1, stage='FINAL',
                 checkpoint_sha256={'detail_0': '0' * 64})
    immutable_json(source / 'FINAL_acceptance.json', sealed(proof))
    immutable_json(source / 'detail_run_manifest.json', dict(plan_sha256=digest(plan),
        final_acceptance_sha256=file_sha(source / 'FINAL_acceptance.json'), progress={'success': 1}))
    snapshot(source, snap, data_kind='SYNTHETIC_TEST')
    result = audit_snapshot(Snapshot(snap))
    assert not result['summary']['producer_final_acceptance_verified']
    assert not result['summary']['batch_integrity_passed']


def test_jsonl_outcome_can_freeze_external_saved_evidence_without_checkpoint(tmp_path):
    source, snap = tmp_path / 'source', tmp_path / 'snap'
    _, _, outcome = make_batch(source)
    external = tmp_path / 'external_saved'
    external.mkdir()
    hp = external / 'original.html'
    hp.write_bytes(Path(outcome['html_path']).read_bytes())
    rp = external / 'receipt.json'
    rp.write_bytes(Path(outcome['receipt_path']).read_bytes())
    outcome.update(html_path=str(hp.resolve()), receipt_path=str(rp.resolve()))
    (source / 'tasks/detail_0/checkpoint.json').unlink()
    (source / 'details.jsonl').write_bytes(canonical_bytes(outcome) + b'\n')
    snapshot(source, snap, data_kind='SYNTHETIC_TEST')
    frozen = Snapshot(snap)
    assert frozen.reference(str(hp.resolve())).is_file()
    assert frozen.reference(str(rp.resolve())).is_file()
    assert audit_snapshot(frozen)['products'][0]['identity_passed']


def test_old_audit_rule_requires_reaudit_not_implicit_approval(tmp_path):
    _, _, snap = run_case(tmp_path)
    audit = tmp_path / 'audit'
    run_audit(snap, audit)
    path = audit / 'manifest.json'
    manifest = read_json(path); manifest['rule_version'] = 'source-audit-v1.0'
    path.write_bytes(canonical_bytes(manifest))
    assert main(['prepare-translation', '--audit', str(audit), '--out', str(tmp_path / 'prep')]) == 2
    assert not (tmp_path / 'prep').exists()


def test_valid_small_final_acceptance_is_not_claimed_formal_5500(tmp_path):
    source, snap = tmp_path / 'source', tmp_path / 'snap'
    make_batch(source)
    plan = read_json(source / 'detail_plan_5500.json')
    proof = dict(passed=True, plan_sha256=digest(plan), run_id=plan['run_id'], stage='FINAL',
                 success_count=1, checkpoint_sha256={'detail_0':file_sha(source / 'tasks/detail_0/checkpoint.json')})
    immutable_json(source / 'FINAL_acceptance.json', sealed(proof))
    immutable_json(source / 'detail_run_manifest.json', dict(plan_sha256=digest(plan), run_id=plan['run_id'],
        final_acceptance_sha256=file_sha(source / 'FINAL_acceptance.json'), progress={'success':1}))
    snapshot(source, snap, data_kind='SYNTHETIC_TEST')
    result = audit_snapshot(Snapshot(snap))
    assert result['summary']['producer_final_acceptance_verified']
    assert not result['summary']['formal_5500_fully_audited']
