"""Re-audit actual page bytes and URL pools, not success labels."""
from collections import Counter
from pathlib import Path

from ..multisource.contracts import SOURCE_ROOTS, RESEARCH_CATEGORIES
from ..multisource.storage import digest, read_sealed
from .adapters import parse_page
from .links import candidate_id, normalize_product_url


def audit_run(store, run_id):
    run = store.get_run(run_id)
    pages = store.pages(run_id)
    findings, cached = [], {}
    live = run.get("origin") == "LIVE_CAPTURE"
    sources = {s["source_id"]: s for s in run.get("sources", [])}
    types, categories = set(), set()
    normalized, groups = set(), set()
    receipts = {}
    if live:
        for indexed in store.iter_responses(run_id):
            try:
                receipt = read_sealed(indexed["evidence_path"])
                if receipt.get("run_id") != run_id or receipt.get("origin") != "LIVE_CAPTURE":
                    raise ValueError("LIVE_RECEIPT_RUN_OR_ORIGIN_CONFLICT")
                receipts.setdefault(receipt["html_path"], []).append(receipt)
            except (OSError, ValueError, KeyError) as exc:
                findings.append({"code": "LIVE_RECEIPT_INVALID", "severity": "ERROR", "reason": str(exc)})

    def fail(code, *, severity="ERROR", **context):
        findings.append({"code": code, "severity": severity, **context})

    def body(path, expected=None):
        if not path:
            raise ValueError("EVIDENCE_PATH_MISSING")
        if path not in cached:
            data = Path(path).read_bytes()
            cached[path] = (data, digest(data))
        data, hashed = cached[path]
        if expected and hashed != expected:
            raise ValueError("EVIDENCE_HASH_MISMATCH")
        return data.decode("utf-8")

    keys = ("raw_product_url", "ranking_position", "rank_context", "product_title_raw", "source_id", "source_type")
    def signature(row):
        return tuple(str(row.get(key)) for key in keys)

    for page in pages:
        try:
            journal = read_sealed(page["journal_path"])
            raw = journal["observations"]
            if page.get("seed_provenance_path"):
                from .continuation import validate_seed_provenance
                derived = validate_seed_provenance(page,run["plan"],store=store)
                parent_rows = derived["parent_rows"]
                if Counter(map(signature,raw)) != Counter(signature(r) for _,r in parent_rows):
                    raise ValueError("CONTINUATION_IMPORTED_RAW_ROWS_CHANGED")
                parents={b["parent_observation_id"]:r for b,r in parent_rows}
                for row in raw:
                    ids=row.get("parent_observation_ids",[])
                    if (len(ids)!=1 or ids[0] not in parents or row.get("origin")!="HISTORICAL_REPLAY"
                            or row.get("is_derivation") is not True or row.get("parent_run_id")!=run.get("parent_run_id")
                            or row.get("observed_at")!=parents[ids[0]].get("observed_at")
                            or row.get("evidence_path")!=parents[ids[0]].get("evidence_path")):
                        raise ValueError("CONTINUATION_ROW_PROVENANCE_CHANGED")
                if page.get("status")!="COMPLETE":
                    fail("PAGE_COMPLETENESS_NOT_PROVEN",page_url=page["page_url"],status=page.get("status"))
                if page.get("source_type_verified") and page.get("source_context_valid"):
                    if any(normalize_product_url(r.get("raw_product_url"))["is_valid"] for r in raw):
                        types.add(page["source_type"])
                        if page.get("research_category") in RESEARCH_CATEGORIES: categories.add(page["research_category"])
                else:
                    fail("PAGE_SOURCE_OR_ACCESS_INVALID",page_url=page["page_url"])
                continue
            html = body(page.get("evidence_path"), page.get("evidence_sha256"))
            source = sources.get(page["source_id"]) if live else page.get("source") or sources.get(page["source_id"])
            if not source and not live:
                source = {"source_id": page["source_id"], "source_type": page["source_type"],
                    "actual_source_url": page["page_url"], "research_category": page.get("research_category")}
            if not source:
                raise ValueError("SOURCE_NOT_IN_REVIEWED_PLAN")
            http_status = page.get("http_status", 200)
            if live:
                proof = [r for r in receipts.get(page["evidence_path"], [])
                    if r.get("kind") == "PAGE" and r.get("request_url") == page["page_url"]
                    and r.get("status_code") == 200 and r.get("access_state") == "NORMAL"]
                if not proof or page["page_url"] not in source.get("page_urls", []):
                    raise ValueError("LIVE_PAGE_WITHOUT_EXACT_NORMAL_REQUEST_PROOF")
                http_status = proof[-1]["status_code"]
                body(page["evidence_path"], proof[-1]["sha256"])
            fragments, seen = [], set()
            for row in raw:
                path = row.get("evidence_path")
                if str(row.get("representation", "")).startswith("ACP") and path not in seen:
                    seen.add(path)
                    if live:
                        proof = [r for r in receipts.get(path, []) if r.get("kind") == "ACP"
                            and r.get("status_code") == 200 and r.get("access_state") == "NORMAL"
                            and r.get("page_key") == page["page_key"]]
                        if not proof:
                            raise ValueError("LIVE_ACP_WITHOUT_NORMAL_REQUEST_PROOF")
                        body(path, proof[-1]["sha256"])
                    fragments.append({"body": body(path, row.get("evidence_sha256")),
                        "status_code": 200, "evidence_path": path})
            parsed = parse_page(html, source=source, run_id=run_id,
                snapshot_id=run.get("snapshot_id", run_id), page_url=page["page_url"],
                page_number=page.get("page_number"), observed_at=page["observed_at"],
                evidence_path=page["evidence_path"], http_status=http_status,
                origin=run.get("origin", "OFFLINE_FIXTURE"), acp_responses=fragments)
            if Counter(map(signature, raw)) != Counter(map(signature, parsed["observations"])):
                fail("RAW_OBSERVATIONS_NOT_BOUND_TO_REPARSED_CARDS", page_url=page["page_url"])
            actual = parsed["page"]
            if actual.get("access_status") != "NORMAL" or not actual.get("source_context_valid") or not actual.get("source_type_verified"):
                fail("PAGE_SOURCE_OR_ACCESS_INVALID", page_url=page["page_url"])
            if actual.get("status") != "COMPLETE":
                fail("PAGE_COMPLETENESS_NOT_PROVEN", severity="ERROR" if live else "WARNING",
                    page_url=page["page_url"], status=actual.get("status"))
            if page.get("actual_count") != actual.get("actual_count"):
                fail("PAGE_COUNT_CONTRADICTS_RAW_EVIDENCE", page_url=page["page_url"])
            if any(normalize_product_url(r.get("raw_product_url"), base_url=page["page_url"])["is_valid"] for r in raw):
                types.add(page["source_type"])
                if page.get("research_category") in RESEARCH_CATEGORIES:
                    categories.add(page["research_category"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            fail("PAGE_EVIDENCE_AUDIT_FAILED", page_url=page.get("page_url"), reason=str(exc))

    for row in store.iter_observations(run_id):
        try:
            body(row.get("evidence_path"), row.get("evidence_sha256"))
            value = normalize_product_url(row.get("raw_product_url"), base_url=row.get("source_url") or "https://www.amazon.es")
            if value["is_valid"]:
                url = value["normalized_product_url"]
                if row.get("source_evidence_eligible") is not False:
                    normalized.add(url)
                    groups.add(value.get("possible_duplicate_key") or url)
                else:
                    fail("UNCONFIRMED_SOURCE_OBSERVATION_EXCLUDED_FROM_TARGET", observation_id=row["observation_id"])
                if row.get("candidate_id") != candidate_id(url) or row.get("normalized_product_url") != url:
                    fail("CANDIDATE_KEY_NOT_LINK_DERIVED", observation_id=row["observation_id"])
            elif row.get("candidate_id"):
                fail("INVALID_LINK_COUNTED", observation_id=row["observation_id"])
        except (OSError, ValueError, KeyError) as exc:
            fail("OBSERVATION_EVIDENCE_INVALID", observation_id=row.get("observation_id"), reason=str(exc))
    if len(normalized) != store.normalized_candidate_count(run_id):
        fail("NORMALIZED_URL_POOL_COUNT_MISMATCH")
    try:
        views=list(store.iter_candidates(run_id))
        reviewed=any(c.get("review_id") for c in views)
        if reviewed:
            groups={c["target_group_key"] for c in views}
        if len(groups) != store.candidate_count(run_id):
            fail("ELIGIBLE_LINK_POOL_COUNT_MISMATCH")
    except (OSError, ValueError, KeyError) as exc:
        views=[]
        fail("IDENTITY_REVIEW_EVIDENCE_INVALID",reason=str(exc))
    if store.summary(run_id).get("pending_page_journals"):
        fail("RAW_PAGE_JOURNALS_NOT_YET_INDEXED")
    requests = store.requests(run_id)
    unresolved = [r["request_id"] for r in requests if r["status"] == "RESERVED"]
    if unresolved:
        fail("AMBIGUOUS_UNFINISHED_REQUESTS", request_ids=unresolved)
    if live:
        if store.get_gate().get("state") != "NORMAL":
            fail("ACCESS_GATE_NOT_NORMAL")
        if types != set(SOURCE_ROOTS):
            fail("FIVE_LIST_CARD_COVERAGE_NOT_PROVEN", missing=sorted(set(SOURCE_ROOTS)-types))
        if run["stage"] == "T3" and categories != set(RESEARCH_CATEGORIES):
            fail("FIFTEEN_CATEGORY_COVERAGE_NOT_PROVEN", missing=sorted(set(RESEARCH_CATEGORIES)-categories))
    if not pages:
        fail("NO_PAGE_EVIDENCE")
    passed = not any(f["severity"] == "ERROR" for f in findings)
    return {"schema_version": 2, "run_id": run_id, "stage": run["stage"], "origin": run.get("origin"),
        "gate_passed": passed, "quality_gate_passed": passed, "findings": findings,
        "unique_valid_links": len(groups), "normalized_unique_urls": len(normalized),
        "strict_independent_buyable_products": sum(c.get("strict_buyable_confirmed",False) for c in views), "source_types_with_valid_cards": sorted(types),
        "research_categories_with_valid_cards": sorted(categories), "page_count": len(pages)}
