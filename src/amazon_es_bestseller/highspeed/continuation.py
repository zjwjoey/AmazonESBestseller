"""Evidence-bound offline derivation into an explicitly approved successor run."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from bs4 import BeautifulSoup

from ..multisource.storage import digest, immutable_json, read_sealed, sealed
from .adapters import parse_page, _parse_cards, _cards, _raw_href
from . import adapters
from .budgets import validate_plan

MODE = "USER_AUTHORIZED_30000_CONTINUATION"
KEYS = ("raw_product_url", "ranking_position", "rank_context", "source_id", "source_type")


def _signature(row):
    return tuple(str(row.get(k)) for k in KEYS)


def _ref(path):
    return {"path":str(path), "sha256":digest(Path(path).read_bytes())}


def _read(ref):
    if digest(Path(ref["path"]).read_bytes()) != ref["sha256"]:
        raise ValueError("CONTINUATION_EVIDENCE_HASH_MISMATCH")
    return read_sealed(ref["path"])


def _source(plan, page):
    source = next((s for s in plan["sources"] if s["source_id"] == page["source_id"]), None)
    if (not source or page["page_url"] not in source["page_urls"]
            or source["source_type"] != page["source_type"]
            or source.get("research_category") != page.get("research_category")):
        raise ValueError("CONTINUATION_SOURCE_OUTSIDE_SUCCESSOR_SCOPE")
    return source


def _indexed(store, run_id):
    # Read the original SQLite index, without applying mutable candidate views.
    return {row[0]:{"page_key":row[1], "row":json.loads(row[2])} for row in store.db.execute(
        "SELECT observation_id,page_key,data_json FROM observations WHERE run_id=?",(run_id,))}


def _parent_eligible(index_row, original_page):
    return (original_page.get("access_status") == "NORMAL"
        and original_page.get("source_type_verified") is True
        and original_page.get("source_context_valid") is True
        and index_row.get("source_evidence_eligible") is not False)


def _verify_original_titles(raw_rows):
    """Reproduce the declared original title parser, never rewrite its output."""
    cache={}
    for row in raw_rows:
        path=row["evidence_path"];fragment=str(row.get("representation","")).startswith("ACP_")
        key=(path,fragment)
        if key not in cache:cache[key]=_cards(BeautifulSoup(Path(path).read_text(encoding="utf-8"),"lxml"),fragment)
        slot=row.get("representation_slot")
        if type(slot) is not int or slot<0 or slot>=len(cache[key]):
            raise ValueError("CONTINUATION_ORIGINAL_TITLE_SLOT_CONFLICT")
        card=cache[key][slot]
        if _raw_href(card)!=row.get("raw_product_url"):
            raise ValueError("CONTINUATION_ORIGINAL_TITLE_URL_CONFLICT")
        if row.get("parser_version")=="link_first_adapters_v2.1":
            node=card.select_one("[data-product-title], .p13n-sc-truncate-desktop-type2, .p13n-sc-truncate, .a-size-base-plus, .a-link-normal .a-size-base")
            title=node.get_text(" ",strip=True) if node else None
            image=card.select_one("img[alt]")
            if not title and image:title=image.get("alt") or None
        elif row.get("parser_version")==adapters.ADAPTER_VERSION:
            title,_=adapters._title_from_card(card)
        else:
            raise ValueError("CONTINUATION_ORIGINAL_TITLE_PARSER_VERSION_UNSUPPORTED")
        if title!=row.get("product_title_raw"):
            raise ValueError("CONTINUATION_ORIGINAL_TITLE_NOT_BOUND_TO_DECLARED_PARSER")


def validate_seed_provenance(page, plan, *, store):
    """Return original receipts and imported rows after full historical re-audit."""
    if (plan.get("stage") != "T1" or plan.get("continuation_mode") != MODE
            or page.get("origin") != "HISTORICAL_REPLAY" or page.get("is_derivation") is not True):
        raise ValueError("CONTINUATION_NOT_A_FORMAL_LIVE_STAGE_PROOF")
    proof = _read({"path":page["seed_provenance_path"], "sha256":page["seed_provenance_sha256"]})
    if (proof["successor_plan_sha256"] != digest(plan) or proof["successor_run_id"] != plan["run_id"]
            or proof["parent_run_id"] != plan.get("parent_run_id")
            or proof["store_root"] != str(store.root) or proof["page_key"] != page["page_key"]):
        raise ValueError("CONTINUATION_PARENT_OR_PLAN_CONFLICT")
    source = _source(plan, page)
    parent = store.get_run(proof["parent_run_id"])
    old_source = _source(parent["plan"], page)
    if digest(old_source) != proof["parent_source_sha256"] or digest(source) != proof["successor_source_sha256"]:
        raise ValueError("CONTINUATION_SOURCE_BINDING_CONFLICT")
    receipts = [_read(ref) for ref in proof["receipts"]]
    for receipt in receipts:
        if (receipt.get("run_id") != proof["parent_run_id"] or receipt.get("origin") != "LIVE_CAPTURE"
                or receipt.get("status_code") != 200 or receipt.get("access_state") != "NORMAL"
                or receipt.get("source_id") != page["source_id"]
                or receipt.get("source_type") != page["source_type"]
                or digest(Path(receipt["html_path"]).read_bytes()) != receipt["sha256"]):
            raise ValueError("CONTINUATION_ORIGINAL_RECEIPT_INVALID")
    journals = {}; raw_by_journal = {}
    for ref in proof["journals"]:
        journal = _read(ref); original = journal["page"]
        if journal["run_id"] != proof["parent_run_id"] or journal["page_key"] != proof["parent_page_key"]:
            raise ValueError("CONTINUATION_JOURNAL_PARENT_CONFLICT")
        _source(plan, original)
        matching = [r for r in receipts if r["kind"] == "PAGE"
            and r["request_url"] == original["page_url"] and r["html_path"] == original["evidence_path"]
            and r.get("observed_at") == original["observed_at"]]
        if not matching:
            raise ValueError("CONTINUATION_PAGE_WITHOUT_ORIGINAL_RECEIPT")
        fragments = []
        seen = set()
        for row in journal["observations"]:
            path = row.get("evidence_path")
            if str(row.get("representation", "")).startswith("ACP") and path not in seen:
                seen.add(path)
                matches = [r for r in receipts if r["kind"] == "ACP" and r["html_path"] == path
                    and r.get("page_key") == proof["parent_page_key"]]
                if not matches:
                    raise ValueError("CONTINUATION_ACP_WITHOUT_ORIGINAL_RECEIPT")
                fragments.append({"body":Path(path).read_text(encoding="utf-8"),
                    "status_code":matches[-1]["status_code"],"evidence_path":path})
        parsed = parse_page(Path(original["evidence_path"]).read_text(encoding="utf-8"), source=old_source,
            run_id=proof["parent_run_id"],snapshot_id=parent.get("snapshot_id"),page_url=original["page_url"],
            page_number=original.get("page_number"),observed_at=original["observed_at"],
            evidence_path=original["evidence_path"],http_status=matching[-1]["status_code"],
            origin="LIVE_CAPTURE",acp_responses=fragments)
        bound_rows = parsed["observations"]
        if not bound_rows and journal["observations"] and not (parsed["page"].get("source_type_verified") and parsed["page"].get("source_context_valid")):
            # Preserve older unconfirmed-source raw cards without promoting
            # their source binding or counting them toward the target.
            bound_rows = _parse_cards(BeautifulSoup(Path(original["evidence_path"]).read_text(encoding="utf-8"),"lxml"),
                source=old_source,run_id=proof["parent_run_id"],snapshot_id=parent.get("snapshot_id"),
                page_url=original["page_url"],page_number=original.get("page_number"),observed_at=original["observed_at"],
                evidence_path=original["evidence_path"],sha=matching[-1]["sha256"],representation="SERVER")
        if Counter(map(_signature,journal["observations"])) != Counter(map(_signature,bound_rows)):
            raise ValueError("CONTINUATION_RAW_ROWS_NOT_BOUND_TO_CARDS")
        _verify_original_titles(journal["observations"])
        if original.get("status") == "COMPLETE" and parsed["page"].get("status") != "COMPLETE":
            raise ValueError("CONTINUATION_COMPLETENESS_NOT_PROVEN")
        for field in ("access_status","source_type_verified","source_context_valid","actual_count"):
            if original.get(field) != parsed["page"].get(field):
                raise ValueError("CONTINUATION_PARENT_PAGE_SOURCE_OR_COUNT_CONFLICT")
        journals[ref["path"]] = journal
        raw_by_journal[ref["path"]] = {digest(raw):raw for raw in journal["observations"]}
    latest = journals[proof["latest_journal_path"]]["page"]
    for field in ("source_id","source_type","research_category","page_url","observed_at","evidence_path",
                  "evidence_sha256","status","actual_count","terminal_for_snapshot"):
        if page.get(field) != latest.get(field):
            raise ValueError("CONTINUATION_IMPORTED_PAGE_CHANGED")
    if page.get("source_navigation_only") != latest.get("source_navigation_only"):
        from .runner import source_navigation_only
        if not (page.get("source_navigation_only") is True and source_navigation_only(source,page["page_url"],{"page":page})):
            raise ValueError("CONTINUATION_NAVIGATION_DECISION_UNPROVEN")
    indexed = _indexed(store,proof["parent_run_id"])
    raw_rows = []
    for binding in proof["row_bindings"]:
        old = indexed.get(binding["parent_observation_id"])
        raw = raw_by_journal[binding["journal_path"]].get(binding["raw_sha256"])
        if (not old or old["page_key"] != proof["parent_page_key"] or not raw
                or old["row"]["raw_sha256"] != binding["raw_sha256"]):
            raise ValueError("CONTINUATION_PARENT_INDEX_ROW_CONFLICT")
        if binding.get("parent_source_evidence_eligible") != _parent_eligible(old["row"],journals[binding["journal_path"]]["page"]):
            raise ValueError("CONTINUATION_PARENT_SOURCE_ELIGIBILITY_CONFLICT")
        raw_rows.append((binding,raw))
    if page.get("journal_path"):
        imported=read_sealed(page["journal_path"])
        expected={b["parent_observation_id"]:b["parent_source_evidence_eligible"] for b,_ in raw_rows}
        if imported.get("run_id") != plan["run_id"] or imported.get("page_key") != page["page_key"]:
            raise ValueError("CONTINUATION_IMPORTED_JOURNAL_CONFLICT")
        for raw in imported["observations"]:
            parents=raw.get("parent_observation_ids",[])
            if (len(parents)!=1 or parents[0] not in expected
                    or raw.get("source_evidence_eligible") is not expected[parents[0]]):
                raise ValueError("CONTINUATION_IMPORTED_SOURCE_ELIGIBILITY_CHANGED")
    return {"provenance":proof,"receipts":receipts,"parent_rows":raw_rows,"latest_page":latest}


def seed_successor(store, parent_run_id, plan):
    """Seed offline only; never reserve requests, duplicate receipts, or clear gates."""
    validate_plan(plan)
    if (plan.get("parent_run_id") != parent_run_id or plan["run_id"] == parent_run_id
            or plan.get("continuation_mode") != MODE or plan.get("stage") != "T1"
            or plan.get("origin") != "LIVE_CAPTURE"):
        raise ValueError("EXPLICIT_CONTINUATION_PLAN_REQUIRED")
    parent = store.get_run(parent_run_id)
    if parent.get("origin") != "LIVE_CAPTURE":
        raise ValueError("CONTINUATION_REQUIRES_REAL_PARENT_CAPTURE")
    indexed = _indexed(store,parent_run_id)
    receipts = list(store.iter_responses(parent_run_id))
    journals = []
    for path in (store.root/"runs"/parent_run_id/"pages").glob("*.json"):
        value = read_sealed(path)
        journals.append((path,value))
    staged=[]
    for parent_page in store.pages(parent_run_id):
        source = _source(plan,parent_page)
        old_source = _source(parent["plan"],parent_page)
        page_key = hashlib.sha256((source["source_id"]+"\0"+parent_page["page_url"]).encode()).hexdigest()
        page_journals=[(path,j) for path,j in journals if j["page_key"] == parent_page["page_key"]]
        bindings=[]
        for oid,index in indexed.items():
            if index["page_key"] != parent_page["page_key"]: continue
            hashed=index["row"]["raw_sha256"]
            matching=[(path,raw) for path,j in page_journals for raw in j["observations"] if digest(raw)==hashed]
            if not matching: raise ValueError("CONTINUATION_INDEX_WITHOUT_RAW_JOURNAL")
            journal_path=str(matching[-1][0])
            original_page=next(j["page"] for path,j in page_journals if str(path)==journal_path)
            bindings.append({"parent_observation_id":oid,"raw_sha256":hashed,"journal_path":journal_path,
                "parent_source_evidence_eligible":_parent_eligible(index["row"],original_page)})
        needed={str(parent_page["journal_path"])}|{b["journal_path"] for b in bindings}
        selected=[(path,j) for path,j in page_journals if str(path) in needed]
        html_paths={j["page"]["evidence_path"] for _,j in selected}|{r.get("evidence_path") for _,j in selected for r in j["observations"]}
        selected_receipts=[r for r in receipts if r.get("html_path") in html_paths
            and r.get("source_id")==source["source_id"] and r.get("access_state")=="NORMAL"]
        proof={"schema_version":1,"parent_run_id":parent_run_id,"successor_run_id":plan["run_id"],
            "successor_plan_sha256":digest(plan),"store_root":str(store.root),"page_key":page_key,
            "parent_page_key":parent_page["page_key"],"parent_source_sha256":digest(old_source),
            "successor_source_sha256":digest(source),"latest_journal_path":str(parent_page["journal_path"]),
            "journals":[_ref(path) for path,_ in selected],"receipts":[_ref(r["evidence_path"]) for r in selected_receipts],
            "row_bindings":bindings,"parent_quality_gate_passed":parent.get("quality_gate_passed",False),
            "original_observed_at":parent_page["observed_at"],"is_derivation":True,"origin":"HISTORICAL_REPLAY"}
        proof_path=store.root/"runs"/plan["run_id"]/("seed_"+digest(proof)[:24]+".json")
        immutable_json(proof_path,sealed(proof))
        page={**parent_page,"source":source,"page_key":page_key,"origin":"HISTORICAL_REPLAY","is_derivation":True,
            "parent_run_id":parent_run_id,"seed_provenance_path":str(proof_path),
            "seed_provenance_sha256":digest(proof_path.read_bytes())}
        page.pop("journal_path",None)
        validated=validate_seed_provenance(page,plan,store=store)
        rows=[]
        for binding,raw in validated["parent_rows"]:
            oid="derived_"+digest([plan["run_id"],binding["parent_observation_id"]])[:32]
            rows.append({**raw,"observation_id":oid,"run_id":plan["run_id"],"origin":"HISTORICAL_REPLAY",
                "is_derivation":True,"parent_run_id":parent_run_id,
                "source_evidence_eligible":binding["parent_source_evidence_eligible"],
                "parent_observation_ids":[binding["parent_observation_id"]],"snapshot_id":plan.get("snapshot_id",plan["run_id"]),
                "seed_provenance_path":str(proof_path),"seed_provenance_sha256":page["seed_provenance_sha256"]})
        staged.append((page_key,page,rows))
    if sum(len(rows) for _,_,rows in staged) != len(indexed):
        raise ValueError("CONTINUATION_RAW_INDEX_COVERAGE_GAP")
    with store.run_lock(plan["run_id"]):
        store.begin_run(plan,started_at=plan.get("task_started_at"))
        for key,page,rows in staged: store.commit_page(plan["run_id"],key,page,rows)
        store.update_run(plan["run_id"],parent_run_id=parent_run_id,is_continuation=True,
            imported_historical_observations=len(indexed),parent_quality_gate_passed=parent.get("quality_gate_passed",False))
        store.append_event(plan["run_id"],{"kind":"HISTORICAL_CONTINUATION_SEED","parent_run_id":parent_run_id,
            "origin":"HISTORICAL_REPLAY","is_derivation":True,"imported_raw_observations":len(indexed),
            "seed_unique_links":store.candidate_count(plan["run_id"]),"requests_charged":0})
    return store.summary(plan["run_id"])
