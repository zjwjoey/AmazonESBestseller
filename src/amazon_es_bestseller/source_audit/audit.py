"""Candidate-bound offline source audit; all parsing delegates to DetailBridge5500."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from bs4 import BeautifulSoup

from ..collection.detail_bridge5500 import normalize, parse_candidate_detail, safe_url
from ..identity import asin_from_url
from ..multisource.storage import digest, read_sealed
from ..normalization.locale_numbers import parse_price_es
from ..normalization.specification import build_spec_es
from ..quality.category_provenance import audit_category_provenance
from ..quality.detail_identity import audit_detail_identity
from ..quality.detail_structure import audit_detail_structure
from .contracts import SUCCESS, TEXT_FIELDS, file_sha

REPLAY_FIELDS = (
    "title_es_raw", "brand_raw", "current_price_raw", "reference_price_raw",
    "selected_variation_raw", "attributes", "ordered_detail_evidence",
    "feature_bullets_raw", "product_description_raw", "detail_category_trail",
    "parent_asin", "rating_raw", "review_count_raw", "monthly_bought_raw",
    "category_evidence", "image_url",
)
REPLAY_SELECTORS = {
    "title_es_raw": "#productTitle", "selected_variation_raw": "#twister",
    "attributes": "#productOverview_feature_div, #productDetails_techSpec_section_1, #productDetails_detailBullets_sections1",
    "feature_bullets_raw": "#feature-bullets, #featurebullets_feature_div, #pqv-feature-bullets",
    "product_description_raw": "#productDescription, #productDescription_feature_div",
    "category_path": "#wayfinding-breadcrumbs_feature_div",
}


def _issue(task, field, code, value, reason, *, severity="P2", state="MANUAL_REVIEW_REQUIRED", evidence=None, repair=False):
    return {
        "candidate_id": task.get("candidate_id"), "selection_id": task.get("selection_id"),
        "asin": task.get("asin") or asin_from_url(task.get("request_url", "")),
        "field_name": field, "issue_code": code, "severity": severity, "audit_status": state,
        "current_value": value, "source_evidence_reference": evidence or {}, "reason": reason,
        "suggested_action": "OFFLINE_REPARSE_PROPOSAL" if repair else "KEEP_AND_VERIFY_SOURCE",
        "offline_repair_allowed": repair, "before": value, "after": None,
        "review_status": "SUGGESTED_NOT_APPLIED",
    }


def _category_path(record):
    evidence = record.get("category_evidence") or {}
    return (evidence.get("product_breadcrumb_category_path") if isinstance(evidence, dict) else None
            ) or record.get("detail_category_trail") or []


def _contents(record):
    return {**{field: record.get(field) for field in TEXT_FIELDS},
            "attributes": record.get("attributes") if record.get("attributes") not in (None, [])
            else record.get("ordered_detail_evidence"), "category_path": _category_path(record)}


def audit_product(snapshot, task, outcome):
    record = outcome.get("record") or {}
    record = record if isinstance(record, dict) else {}
    evidence = {"snapshot_manifest_sha256": snapshot.sha256, "html_sha256": outcome.get("html_sha256"),
                "html_reference": outcome.get("html_path"), "receipt_sha256": outcome.get("receipt_sha256")}
    issues, proposals = [], []
    product = {"candidate_id": task.get("candidate_id"), "selection_id": task.get("selection_id"),
               "asin": record.get("asin"), "task_id": task.get("task_id"),
               "collection_status": outcome.get("status", "UNVERIFIED"),
               "raw_record": record, "source_evidence_reference": evidence,
               "identity_passed": False, "field_statuses": {}, "preparation_status": "NOT_READY"}

    def add(field, code, reason, *, severity="P2", state="MANUAL_REVIEW_REQUIRED", repair=False, value=None):
        issue = _issue({**task, "asin": record.get("asin")}, field, code,
                       record.get(field) if value is None else value, reason, severity=severity,
                       state=state, evidence=evidence, repair=repair)
        issues.append(issue)
        return issue

    status = product["collection_status"]
    if status not in SUCCESS:
        block = ("IDENTITY_BLOCKED" if status == "IDENTITY_REVIEW" else
                 "SOURCE_BLOCKED" if status in {"SOURCE_BLOCKED", "ACCESS_STOP"} else "NOT_READY")
        product["preparation_status"] = block
        add("*", status, "任务尚非有效成功详情，不从榜单标题拼成详情。", severity="P0",
            state="IDENTITY_REVIEW" if status == "IDENTITY_REVIEW" else
            "SOURCE_BLOCKED" if block == "SOURCE_BLOCKED" else "SOURCE_MISSING")
        return product, issues, proposals
    try:
        hp = snapshot.reference(outcome["html_path"])
        if file_sha(hp) != outcome["html_sha256"]:
            raise ValueError("HTML_SHA_MISMATCH")
        evidence["snapshot_html_path"] = hp.relative_to(snapshot.root).as_posix()
        kind = "cache_metadata" if status == "REUSED_VALID_CACHE" else "receipt"
        rp = snapshot.reference(outcome[kind + "_path"])
        if file_sha(rp) != outcome[kind + "_sha256"]:
            raise ValueError("RECEIPT_OR_METADATA_SHA_MISMATCH")
        receipt = snapshot.json(rp.relative_to(snapshot.root).as_posix()) if kind == "cache_metadata" else read_sealed(rp)
        if "SYNTHETIC" in str(receipt.get("kind", "")) and snapshot.manifest.get("data_kind") == "DETAIL5500":
            raise ValueError("SYNTHETIC_RECEIPT_NOT_FORMAL_DETAIL_EVIDENCE")
        if kind == "cache_metadata" and (receipt.get("access_state") != "NORMAL" or receipt.get("recovered_from_challenge")):
            raise ValueError("CACHE_ACCESS_STATE_UNVERIFIED")
        if any(outcome.get(k) != task.get(k) for k in ("candidate_id", "selection_id", "task_id", "run_id")):
            raise ValueError("CHECKPOINT_TASK_BINDING_MISMATCH")
        if kind == "receipt":
            if (receipt.get("task_id") != task.get("task_id") or receipt.get("run_id") != task.get("run_id")
                    or receipt.get("state") != "NORMAL"):
                raise ValueError("RECEIPT_TASK_OR_STATE_MISMATCH")
            plan = snapshot.json("detail_plan_5500.json", {})
            if plan and receipt.get("plan_sha256") != digest(plan):
                raise ValueError("RECEIPT_PLAN_SHA_MISMATCH")
        requested = receipt.get("request_url") or receipt.get("requested_url")
        if normalize(requested or "") != normalize(task.get("request_url") or ""):
            raise ValueError("RECEIPT_REQUEST_URL_MISMATCH")
        if (kind == "receipt" or receipt.get("html_sha256")) and receipt.get("html_sha256") != outcome["html_sha256"]:
            raise ValueError("RECEIPT_HTML_MISMATCH")
        if outcome.get("response_metadata_path"):
            mp = snapshot.reference(outcome["response_metadata_path"])
            if file_sha(mp) != outcome.get("response_metadata_sha256"):
                raise ValueError("RESPONSE_METADATA_SHA_MISMATCH")
            metadata = snapshot.json(mp.relative_to(snapshot.root).as_posix())
            if not isinstance(metadata, dict) or any(metadata.get(k) != receipt.get(k) for k in ("final_url", "status_code")):
                raise ValueError("RESPONSE_METADATA_RECEIPT_MISMATCH")
            if metadata.get("access_state") != "NORMAL" or (metadata.get("requested_url") or metadata.get("request_url")) != requested:
                raise ValueError("RESPONSE_METADATA_REQUEST_OR_STATE_MISMATCH")
            evidence.update(response_metadata_sha256=file_sha(mp), snapshot_metadata_path=mp.relative_to(snapshot.root).as_posix())
        elif kind == "receipt" and snapshot.manifest.get("data_kind") == "DETAIL5500":
            raise ValueError("FORMAL_RESPONSE_METADATA_REQUIRED")
        if record.get("candidate_id") != task.get("candidate_id") or record.get("selection_id") != task.get("selection_id"):
            raise ValueError("RECORD_CANDIDATE_SELECTION_MISMATCH")
        # Same-ASIN but different selected URLs remain separate candidate tasks.
        if record.get("final_url") != receipt.get("final_url"):
            raise ValueError("RECORD_RECEIPT_FINAL_URL_MISMATCH")
        if record.get("request_url") != task.get("request_url"):
            raise ValueError("RECORD_TASK_REQUEST_URL_MISMATCH")
        if (record.get("source_snapshot_id") != task.get("source_snapshot_id") or
                record.get("source_memberships") != task.get("source_memberships")):
            raise ValueError("RECORD_TASK_SOURCE_PROVENANCE_MISMATCH")
        html = hp.read_text(encoding="utf8")
        meta = {"status_code": receipt.get("status_code"), "final_url": receipt.get("final_url"),
                "collected_at": record.get("collected_at")}
        replay = parse_candidate_detail(task, html, meta)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        add("*", "EVIDENCE_INVALID", str(exc), severity="P0", state="UNVERIFIED")
        return product, issues, proposals
    if replay.get("status") != "SUCCESS":
        product["preparation_status"] = "SOURCE_BLOCKED" if replay.get("status") == "ACCESS_STOP" else "IDENTITY_BLOCKED"
        add("*", "HTML_REPLAY_" + replay.get("status", "UNVERIFIED"), replay.get("reason", "源页面受限或不能证明当前商品"),
            severity="P0", state="SOURCE_BLOCKED" if replay.get("status") == "ACCESS_STOP" else "IDENTITY_REVIEW")
        return product, issues, proposals
    actual = replay["record"]
    if record.get("asin") != actual.get("asin") or record.get("resolved_asin") != actual.get("resolved_asin"):
        add("*", "ASIN_REPLAY_MISMATCH", "保存详情身份与活动商品HTML不一致。", severity="P0", state="IDENTITY_REVIEW")
        product["preparation_status"] = "IDENTITY_BLOCKED"
        return product, issues, proposals
    # Reuse existing audits, retaining their original diagnostics as evidence.
    # The legacy QA vocabulary predates EXACT_ACTIVE_PRODUCT_MATCH. Feed the
    # already proven active-ASIN result through its accepted equivalent; do
    # not treat an unrecognized status string as new contradictory evidence.
    identity_view = {**record, "identity_status": "IDENTITY_MATCH",
                     "final_url": asin_from_url(record.get("final_url") or "")}
    old_identity = audit_detail_identity([identity_view])
    if old_identity.status == "BLOCK":
        add("*", "RESOLVER_IDENTITY_BLOCK", "已有身份Resolver拒绝当前记录。", severity="P0", state="IDENTITY_REVIEW")
        product["preparation_status"] = "IDENTITY_BLOCKED"
        return product, issues, proposals
    product["identity_passed"] = True
    soup = BeautifulSoup(html, "lxml")
    complete_html = bool(soup.select_one("#productTitle")) and "</html>" in html.lower()
    contents = _contents(record)
    actual_contents = _contents(actual)
    # Parsing differences are proposals only. They never silently change input.
    for field in REPLAY_FIELDS:
        if record.get(field) != actual.get(field):
            target = ("attributes" if field == "ordered_detail_evidence" else
                      "category_path" if field in {"category_evidence", "detail_category_trail"} else field)
            finding = add(target, "HTML_RECORD_MISMATCH", "保存字段与同一HTML经现有解析器回放的结果不同。",
                          severity="P1", state="OFFLINE_REPARSE_AVAILABLE", repair=True, value=record.get(field))
            finding["source_field_name"] = field
            finding["after"] = actual.get(field)
            proposals.append({"candidate_id": task.get("candidate_id"), "selection_id": task.get("selection_id"),
                "asin": record.get("asin"), "field_name": field, "before": record.get(field),
                "after": actual.get(field), "source_evidence_reference": dict(evidence),
                "proof": "EXACT_HTML_CURRENT_EXISTING_PARSER", "application_status": "PROPOSAL_ONLY",
                "safe_copy_eligible": field not in {"parent_asin", "brand_raw", "selected_variation_raw"},
                "source_value_sha256": digest(record.get(field)), "repaired_value_sha256": digest(actual.get(field)),
                "requires_reaudit": True})
    for field, value in contents.items():
        if value in (None, "", []):
            selector = REPLAY_SELECTORS.get(field)
            absent = (complete_html and selector and not soup.select_one(selector)
                      and actual_contents.get(field) in (None, "", []))
            add(field, "SOURCE_NOT_DISPLAYED" if absent else "FIELD_MISSING",
                "完整已保存页面未显示此字段。" if absent else "字段为空；不能据此推断源未展示。",
                state="SOURCE_NOT_DISPLAYED" if absent else "FIELD_MISSING")
    if not contents.get("title_es_raw"):
        add("title_es_raw", "TITLE_REQUIRED", "缺少有效商品名称。", severity="P1", state="FIELD_MISSING")
    for field, value in contents.items():
        if field == "attributes":
            continue
        values = value if isinstance(value, list) else [value]
        for text in values:
            if text is None:
                continue
            if not isinstance(text, str):
                add(field, "TEXT_STRUCTURE_INVALID", "文本字段包含非文本值。", severity="P1", state="STRUCTURE_INVALID")
                continue
            if re.search(r"</?[A-Za-z][^>]*>", text):
                add(field, "HTML_RESIDUE", "源字段残留HTML。", severity="P1", state="STRUCTURE_INVALID")
            if re.search(r"[\u4e00-\u9fff]", text):
                add(field, "LANGUAGE_POLLUTION", "西语字段混入中文，保留源文待核验。", severity="P1", state="SOURCE_CONFLICT")
            if any(token in text for token in ("\ufffd", "Ã", "Â")):
                add(field, "MOJIBAKE", "疑似乱码，不自行反编码猜原文。", severity="P1", state="SOURCE_CONFLICT")
            if re.search(r"(?:clasificaci[oó]n.*m[aá]s vendidos|ver los 100|productos relacionados|a menudo se compran juntos)", text, re.I):
                add(field, "CONTENT_CONTAMINATION", "商品内容疑似混入排名或推荐区。", severity="P1", state="STRUCTURE_INVALID")
            if field == "title_es_raw" and text.strip().casefold() in {"amazon.es", "resultados", "productos recomendados", "inicio"}:
                add(field, "NAVIGATION_TITLE", "导航/通用标题不是商品名称。", severity="P1", state="STRUCTURE_INVALID")
    if contents.get("feature_bullets_raw") and not isinstance(contents["feature_bullets_raw"], list):
        add("feature_bullets_raw", "BULLETS_NOT_LIST", "卖点必须是有序独立文本列表。", severity="P1", state="STRUCTURE_INVALID")
    if isinstance(contents.get("feature_bullets_raw"), list):
        counts = Counter(x for x in contents["feature_bullets_raw"] if isinstance(x, str))
        if any(n > 1 for n in counts.values()):
            add("feature_bullets_raw", "DUPLICATE_BULLETS", "重复卖点保留位置，不静默删除。")
    structure_view = record
    if isinstance(contents.get("attributes"), list) and all(isinstance(a, dict) for a in contents["attributes"]):
        # Legacy parser positions are section-local. Preserve them in raw and
        # check that coordinate system here; legacy global checks see ordinals.
        attrs = contents["attributes"]
        coordinates = [digest([a.get("section"), a.get("source"), a.get("position")]) for a in attrs]
        if len(set(coordinates)) != len(coordinates):
            add("attributes", "DETAIL_POSITION_DUPLICATE", "同一section/source内position重复。", severity="P1", state="STRUCTURE_INVALID")
        if any(not isinstance(a.get("position"), int) or isinstance(a.get("position"), bool) for a in attrs):
            add("attributes", "DETAIL_POSITION_MISSING", "属性position必须为整数。", severity="P1", state="STRUCTURE_INVALID")
        if any(any(not isinstance(a.get(k), str) or not a[k].strip() for k in ("section", "source", "label_raw", "value_raw")) for a in attrs):
            add("attributes", "DETAIL_PROVENANCE_INCOMPLETE", "属性section/source/label/value须为非空文本。", severity="P1", state="STRUCTURE_INVALID")
        structure_view = {**record, "attributes": [{**a, "position": i} for i, a in enumerate(attrs)]}
    for old in audit_detail_structure([structure_view]).issues:
        field = "feature_bullets_raw" if old.issue_code == "FEATURE_BULLETS_NOT_LIST" else "attributes"
        add(field, old.issue_code, old.message, severity="P1" if old.status == "BLOCK" else old.severity,
            state="STRUCTURE_INVALID" if old.status == "BLOCK" else "MANUAL_REVIEW_REQUIRED")
    attrs = contents.get("attributes")
    attr_values = defaultdict(set)
    if isinstance(attrs, list):
        for attr in attrs:
            if isinstance(attr, dict):
                label, value = attr.get("label_raw"), attr.get("value_raw")
                if isinstance(label, str) and isinstance(value, str):
                    attr_values[(str(attr.get("section")), label.casefold())].add(value)
                    if re.search(r"</?[A-Za-z][^>]*>|[\u4e00-\u9fff]|\ufffd|Ã", label + value):
                        add("attributes", "ATTRIBUTE_TEXT_POLLUTION", "参数内容污染，保留原始属性位置。", severity="P1", state="STRUCTURE_INVALID", value=attr)
                    label_key = label.casefold()
                    # Spanish dimension axis l. means largo, not litres.
                    units = set(re.findall(r"\d\s*(mm|cm|ml|kg|mg|g|l(?!\.)|m)\b", value, re.I))
                    units = {u.lower() for u in units}
                    if (("peso" in label_key and units & {"ml", "l", "mm", "cm"}) or
                        ("capacidad" in label_key and units & {"kg", "mg", "g", "mm", "cm"}) or
                        ("dimensiones" in label_key and units and units <= {"ml", "l"})):
                        add("attributes", "UNIT_TYPE_CONFLICT", "数值单位类型与参数语义不匹配，不自动换算修复。", severity="P1", state="SOURCE_CONFLICT", value=attr)
        for (section, label), values in attr_values.items():
            if len(values) > 1:
                add("attributes", "DUPLICATE_LABEL_VALUES_REVIEW", "同一section中同名参数有多个值，可能合法；不得用普通字典合并。",
                    state="MANUAL_REVIEW_REQUIRED", value={"section": section, "label": label, "values": sorted(values)})
    brand = record.get("brand_raw") or record.get("brand")
    if brand and brand != actual.get("brand_raw"):
        add("brand_raw", "BRAND_EVIDENCE_CONFLICT", "品牌与当前HTML的byline/品牌属性不一致，禁止标题首词回填。", severity="P1", state="SOURCE_CONFLICT")
    if record.get("brand") and str(record["brand"]).strip().casefold() != str(actual.get("brand_raw") or "").strip().casefold():
        add("brand", "NORMALIZED_BRAND_UNVERIFIED", "规范化品牌没有与当前页面品牌证据一致，禁止以标题或卖家自动猜品牌。", severity="P1", state="SOURCE_CONFLICT")
    parent = record.get("parent_asin")
    if parent:
        proof = record.get("parent_asin_evidence") or {}
        if parent == record.get("asin") or not proof or proof.get("parent_asin") != parent or proof.get("current_asin") != record.get("asin"):
            add("parent_asin", "PARENT_RELATION_UNCONFIRMED", "未证实独立父子关系，不能按parent合并商品。", state="IDENTITY_REVIEW")
    if record.get("selected_variation_raw") != actual.get("selected_variation_raw"):
        add("selected_variation_raw", "VARIANT_SELECTION_CONFLICT", "当前选中规格与此ASIN的保存页面不同，不从同族商品替换。", severity="P1", state="SOURCE_CONFLICT")
    if record.get("delivery_is_spain") is not True:
        add("current_price_raw", "DELIVERY_REGION_UNVERIFIED", "保存页面配送地区未确认为西班牙；价格保持原文并提示地区作用域，不阻断独立文本翻译。", state="UNVERIFIED")
    source_paths = [m.get("category_path") or m.get("ranking_category_path") for m in task.get("source_memberships", []) if isinstance(m, dict)]
    if _category_path(record) and any(source_paths):
        for path in source_paths:
            if path:
                old = audit_category_provenance([{"asin": record.get("asin"), "ranking_category_path": path}], [record])
                if old.issues:
                    add("category_path", "CATEGORY_SOURCE_DIFFERENCE", "来源榜单类目与详情面包屑角色不同；各自保留，不自动覆盖；独立已验证的详情路径可以准备翻译。", severity="P3", state="MANUAL_REVIEW_REQUIRED")
    if not _category_path(record):
        add("category_path", "CATEGORY_PROVENANCE_MISSING", "未验证完整Amazon详情类目路径，不用研究类目冒充。", state="UNVERIFIED")
    elif not soup.select_one(REPLAY_SELECTORS["category_path"]):
        add("category_path", "CATEGORY_PROVENANCE_UNVERIFIED", "结构化类目存在但HTML缺少商品面包屑证据。", state="UNVERIFIED")
    for field in ("current_price_raw", "reference_price_raw", "unit_price_raw"):
        raw = record.get(field)
        if raw and (parse_price_es(raw) is None or not re.search(r"€|\bEUR\b", str(raw), re.I)):
            add(field, "PRICE_OR_CURRENCY_REVIEW", "价格/货币证据不能确定，不猜值。", state="SOURCE_CONFLICT")
    current, reference = parse_price_es(record.get("current_price_raw")), parse_price_es(record.get("reference_price_raw"))
    if current is not None and reference is not None and current > reference:
        add("current_price_raw", "CURRENT_ABOVE_REFERENCE", "当前价高于页面参考价，仅标记核验，不改价格或称历史原价。", state="SOURCE_CONFLICT")
    if record.get("discount_rate") is not None:
        rate = record["discount_rate"]
        valid = isinstance(rate, (int, float)) and not isinstance(rate, bool)
        if not valid or current is None or reference is None or reference <= 0 or abs(rate - (1 - current / reference)) > 0.005:
            add("discount_rate", "DISCOUNT_UNVERIFIED", "数值折扣需同时绑定有效当前价和明确参考价，约定0–1比例。", state="SOURCE_CONFLICT")
    if record.get("specification_es"):
        add("specification_es", "DERIVED_SPEC_UNVERIFIED", "核心规格为衍生字段；当前版本不把未绑定来源优先级的摘要直接交模型。", state="UNVERIFIED")
    for field, value in contents.items():
        relevant = [i for i in issues if i["field_name"] in {field, "*"}]
        if any(i["severity"] in {"P0", "P1"} or i["audit_status"] in {"SOURCE_CONFLICT", "UNVERIFIED", "STRUCTURE_INVALID"} for i in relevant):
            field_state = "SOURCE_REVIEW_REQUIRED"
        elif value in (None, "", []):
            field_state = "SOURCE_NOT_DISPLAYED" if any(i["audit_status"] == "SOURCE_NOT_DISPLAYED" for i in relevant) else "FIELD_MISSING"
        else:
            field_state = "READY"
        product["field_statuses"][field] = field_state
    fields = product["field_statuses"]
    # Reuse the existing Spanish-only specification builder in explicit
    # priority passes; never let a generic attribute quantity=1 win first.
    derived, derivation = "", None
    for source_field in ("selected_variation_raw", "title_es_raw", "attributes"):
        if fields.get(source_field) != "READY":
            continue
        value = contents.get(source_field)
        if source_field == "selected_variation_raw":
            if re.fullmatch(r"1(?:\s*(?:unidad|unidades|ud\.?))?", str(value or "").strip(), re.I):
                continue
            candidate = build_spec_es(variant=value)
        elif source_field == "title_es_raw":
            candidate = build_spec_es(title_es=value)
        else:
            candidate = build_spec_es(attributes=value)
        if candidate:
            derived, derivation = candidate, {"source_field": source_field,
                "source_value_sha256": digest(value), "method": "EXISTING_BUILD_SPEC_ES_PRIORITY_PASSES",
                "priority": ["selected_variation_raw", "title_es_raw", "explicit_attribute_evidence"],
                "derived_text_sha256": digest(candidate.encode("utf8"))}
            break
    if derived and (not record.get("specification_es") or record["specification_es"] == derived):
        contents["specification_es"] = derived
        fields["specification_es"] = "READY"
        product["specification_derivation"] = derivation
        issues[:] = [i for i in issues if not (i["field_name"] == "specification_es" and
                    i["issue_code"] in {"FIELD_MISSING", "DERIVED_SPEC_UNVERIFIED"})]
    if fields.get("title_es_raw") != "READY":
        product["preparation_status"] = "SOURCE_REVIEW_REQUIRED"
    elif any(s != "READY" for f, s in fields.items() if f != "specification_es"):
        product["preparation_status"] = "PARTIAL_READY"
    else:
        product["preparation_status"] = "READY"
    product["translation_source"] = contents
    return product, issues, proposals


def audit_snapshot(snapshot, *, expected_count=None):
    snapshot.verify()
    tasks, rows = snapshot.tasks(), snapshot.rows()
    if not all(isinstance(t, dict) for t in tasks) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("INVALID_TASK_OR_RECORD_SHAPE")
    selected = snapshot.json("selected_5500.json", [])
    plan = snapshot.json("detail_plan_5500.json", {})
    global_issues = []
    def batch_issue(code, reason):
        global_issues.append(_issue({}, "*", code, None, reason, severity="P0", state="UNVERIFIED",
                                    evidence={"snapshot_manifest_sha256": snapshot.sha256}))
    ids = [t.get("candidate_id") for t in tasks]
    selection_ids = [t.get("selection_id") for t in tasks]
    task_ids = [t.get("task_id") for t in tasks]
    if not tasks:
        batch_issue("EMPTY_INPUT", "没有商品任务，不能报告全量通过。")
    if any(not safe_url(t.get("request_url", "")) or not normalize(t.get("request_url", "")) or
           normalize(t.get("request_url", "")) != t.get("normalized_product_url") for t in tasks):
        batch_issue("TASK_URL_BINDING_INVALID", "必须保留实际Amazon商品URL及一致的规范化URL，不合成链接。")
    if None in ids or "" in ids or len(set(ids)) != len(ids):
        batch_issue("CANDIDATE_ID_MISSING_OR_DUPLICATE", "候选身份缺失或冲突，不能按ASIN折叠覆盖。")
    if None in selection_ids or "" in selection_ids or len(set(selection_ids)) != len(selection_ids):
        batch_issue("SELECTION_ID_MISSING_OR_DUPLICATE", "入选身份缺失或冲突。")
    if plan and (None in task_ids or "" in task_ids or len(set(task_ids)) != len(task_ids)):
        batch_issue("TASK_ID_MISSING_OR_DUPLICATE", "任务ID缺失或重复。")
    if expected_count is not None and len(tasks) != expected_count:
        batch_issue("TASK_COUNT_MISMATCH", "任务数不符合显式目标；不把部分数据称全量完成。")
    if plan.get("selection_sha256") and plan["selection_sha256"] != snapshot.files.get("selected_5500.json"):
        batch_issue("SELECTION_PLAN_SHA_MISMATCH", "计划绑定的入选清单缺失或哈希失效。")
    if "selected_5500.json" in snapshot.files:
        if not isinstance(selected, list) or not all(isinstance(r, dict) for r in selected):
            raise ValueError("INVALID_SELECTION_SHAPE")
        if {r.get("candidate_id") for r in selected} != set(ids) or len(selected) != len(tasks):
            batch_issue("SELECTION_PLAN_ID_MISMATCH", "入选清单与计划任务不一致。")
        if plan and plan.get("selection_sha256") != snapshot.files.get("selected_5500.json"):
            batch_issue("SELECTION_PLAN_SHA_MISMATCH", "计划没有绑定当前入选清单字节哈希。")
        selected_by = {r.get("candidate_id"): r for r in selected}
        for task in tasks:
            row = selected_by.get(task.get("candidate_id"), {})
            if row.get("selection_id") != task.get("selection_id") or (
                    row.get("absolute_product_url") and normalize(row["absolute_product_url"]) != normalize(task.get("request_url", ""))):
                batch_issue("SELECTION_TASK_BINDING_MISMATCH", "candidate/selection/实际URL绑定不一致。")
                break
    if plan.get("source_certificate_sha256"):
        certificate = snapshot.json("source_input_certificate.json", {})
        if digest(certificate) != plan["source_certificate_sha256"]:
            batch_issue("SOURCE_CERTIFICATE_MISMATCH", "来源证书缺失或哈希失效。")
    source_ref = plan.get("source_snapshot_reference") or {}
    if source_ref:
        if snapshot.files.get("source_origin/manifest.json") != source_ref.get("manifest_sha256"):
            batch_issue("SOURCE_SNAPSHOT_REFERENCE_INVALID", "榜单来源快照manifest缺失或哈希失效；保留原审计状态，不把旧失败状态改为通过。")
    final = snapshot.json("detail_run_manifest.json", {})
    final_accepted = False
    if final:
        try:
            acceptance = read_sealed(snapshot.path("FINAL_acceptance.json"))
            final_accepted = (final.get("plan_sha256") == digest(plan)
                              and final.get("final_acceptance_sha256") == snapshot.files.get("FINAL_acceptance.json")
                              and acceptance.get("passed") is True and acceptance.get("plan_sha256") == digest(plan)
                              and acceptance.get("stage") == "FINAL"
                              and bool(plan.get("run_id"))
                              and acceptance.get("run_id") == plan.get("run_id") == final.get("run_id")
                              and acceptance.get("success_count") == len(tasks)
                              and final.get("progress", {}).get("success") == len(tasks)
                              and acceptance.get("checkpoint_sha256") == {
                                  t["task_id"]: snapshot.files.get("tasks/" + t["task_id"] + "/checkpoint.json") for t in tasks}
                              and all("tasks/" + t["task_id"] + "/checkpoint.json" in snapshot.files for t in tasks))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if not final_accepted:
            batch_issue("FINAL_ACCEPTANCE_INVALID", "正式完成manifest未绑定有效最终验收，不能声称5500已完成。")
    by_candidate = {}
    for row in rows:
        cid = row.get("candidate_id")
        if cid not in ids:
            batch_issue("UNPLANNED_DETAIL_ROW", "详情行不属于当前计划，不能混入。")
        if cid in by_candidate:
            batch_issue("DUPLICATE_DETAIL_ROW", "相同candidate有重复详情，禁止最后一条覆盖。")
        by_candidate[cid] = row
    products, issues, proposals = [], list(global_issues), []
    for task in tasks:
        try:
            outcome = snapshot.outcome(task, by_candidate)
        except (OSError, ValueError, KeyError, TypeError):
            outcome = {"status": "PARSE_FAILED"}
        product, found, repairs = audit_product(snapshot, task, outcome)
        products.append(product); issues.extend(found); proposals.extend(repairs)
        row = by_candidate.get(task.get("candidate_id"))
        csv_input = any(n.endswith(".csv") and n.startswith("details") for n in snapshot.files) and not any(
            n in snapshot.files for n in ("details_5500_raw.jsonl", "details.jsonl", "details.json"))
        compare_row = row.get("record", row) if row is not None else None
        raw = product["raw_record"]
        matches = compare_row == raw
        if csv_input and isinstance(compare_row, dict) and set(compare_row) == set(raw):
            matches = all((compare_row[key] == value if isinstance(value, (list, dict))
                           else compare_row[key] == ("" if value is None else str(value))) for key, value in raw.items())
        if row is not None and not matches:
            issues.append(_issue(task, "*", "EXPORT_CHECKPOINT_MISMATCH", row,
                                 "导出详情与权威任务checkpoint不同。", severity="P0", state="SOURCE_CONFLICT"))
            product["preparation_status"] = "NOT_READY"
            product["field_statuses"] = {key: "SOURCE_REVIEW_REQUIRED" for key in product["field_statuses"]}
    if final_accepted and any(p["collection_status"] not in SUCCESS or not p["identity_passed"] for p in products):
        final_accepted = False
        batch_issue("FINAL_REPLAY_NOT_SUCCESSFUL", "最终验收声明与逐任务有效详情回放不一致。")
        issues.extend(global_issues[-1:])
    if global_issues:
        for product in products:
            product["preparation_status"] = "NOT_READY"
            product["field_statuses"] = {key: "SOURCE_REVIEW_REQUIRED" for key in product["field_statuses"]}
    snapshot.verify()
    status_counts = Counter(p["preparation_status"] for p in products)
    collection = Counter(p["collection_status"] for p in products)
    coverage = []
    for field in TEXT_FIELDS:
        counts = Counter(p["field_statuses"].get(field, "NOT_READY") for p in products)
        coverage.append({"field_name": field, "input_products": len(products), "valid": counts["READY"],
                         "missing": counts["FIELD_MISSING"], "source_not_displayed": counts["SOURCE_NOT_DISPLAYED"],
                         "blocked_or_review": sum(n for state, n in counts.items() if state not in {"READY", "FIELD_MISSING", "SOURCE_NOT_DISPLAYED"})})
    ready_states = {"READY", "PARTIAL_READY"}
    summary = {
        "snapshot_manifest_sha256": snapshot.sha256, "data_kind": snapshot.manifest.get("data_kind"),
        "input_task_count": len(tasks), "expected_task_count": expected_count,
        "detail_input_count": sum(bool(p["raw_record"]) for p in products), "exported_detail_rows": len(rows),
        "valid_detail_count": sum(p["identity_passed"] and p["preparation_status"] in ready_states for p in products),
        "identity_passed_count": sum(p["identity_passed"] for p in products),
        "collection_status_counts": dict(collection), "task_completed_count": sum(s not in {"SOURCE_MISSING", "PENDING", "RUNNING"} for s in (p["collection_status"] for p in products)),
        "success_count": collection["SUCCESS"], "cache_reused_count": collection["REUSED_VALID_CACHE"],
        "ready_products": status_counts["READY"], "partial_ready_products": status_counts["PARTIAL_READY"],
        "blocked_products": sum(n for state, n in status_counts.items() if state not in ready_states),
        "preparation_status_counts": dict(status_counts), "issue_count": len(issues),
        "manual_review_products": len({i["candidate_id"] for i in issues if i["candidate_id"] and i["audit_status"] not in {"SOURCE_NOT_DISPLAYED", "FIELD_MISSING"}}),
        "followup_collection_proposal_products": len({p["candidate_id"] for p in products if not p["identity_passed"] or any(s == "FIELD_MISSING" for f, s in p["field_statuses"].items() if f != "specification_es")}),
        "batch_integrity_passed": not global_issues,
        "producer_final_acceptance_verified": final_accepted,
        "formal_5500_fully_audited": snapshot.manifest.get("data_kind") == "DETAIL5500" and final_accepted and len(tasks) == 5500 and not global_issues and all(p["identity_passed"] for p in products),
        "production_apply": False, "amazon_requests": 0, "translation_api_requests": 0,
    }
    return {"summary": summary, "products": products, "issues": issues, "repairs": proposals, "coverage": coverage}
