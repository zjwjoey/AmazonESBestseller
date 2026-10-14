"""Immutable offline title projection, bound to captured card evidence."""
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote
import uuid

from bs4 import BeautifulSoup
from ..multisource.storage import digest, immutable_json, sealed
from . import adapters

TITLE_REVIEW_VERSION = "source_title_review_v1"
_TITLE_NODES = "[data-product-title], .p13n-sc-truncate-desktop-type2, .p13n-sc-truncate, [class*='line-clamp']"


def _source_title_proof(card, raw_url, title, evidence):
    if not isinstance(title,str) or not title.strip():return None
    # A price/rating-only value is never a fallback product title.
    if re.fullmatch(r"\s*(?:[€$]\s*)?\d+[\d.,\s]*(?:[€$])?\s*",title) or re.fullmatch(r"[\d.,]+\s+de\s+5\s+estrellas",title,re.I):return None
    for anchor in card.select("a[href]"):
        if anchor.get("href")!=raw_url:continue
        for node in anchor.select(_TITLE_NODES):
            if node.get_text(" ",strip=True)==title:
                return {"kind":"PRODUCT_ANCHOR_TITLE_TEXT","node_sha256":digest(str(node).encode("utf-8"))}
        for image in anchor.select("img[alt]"):
            if image.get("alt")==title:
                return {"kind":"PRODUCT_ANCHOR_IMAGE_ALT","node_sha256":digest(str(image).encode("utf-8"))}
    # Explicit fixture/legacy title attributes are semantic source evidence,
    # provided the same card has an exactly matching product href.
    if any(a.get("href")==raw_url for a in card.select("a[href]")):
        for node in card.select("[data-product-title]"):
            if node.get_text(" ",strip=True)==title:
                return {"kind":"EXPLICIT_PRODUCT_TITLE_ATTRIBUTE","node_sha256":digest(str(node).encode("utf-8"))}
    selector=evidence.get("selector"); index=evidence.get("selector_match_index")
    if type(index) is int and index>=0 and selector:
        matches=card.select(selector)
        if index<len(matches):
            node=matches[index]
            text=node.get("alt") if evidence["kind"]=="SOURCE_IMAGE_ALT" else node.get_text(" ",strip=True)
            anchor=node.find_parent("a",href=True)
            if text==title and (anchor is None or anchor.get("href")==raw_url):
                return {"kind":evidence["kind"],"selector":selector,"selector_match_index":index,
                    "node_sha256":digest(str(node).encode("utf-8"))}
    return None


def project_title(captured, source, *, cache=None):
    """Fail closed unless URL, DOM slot, rank and original HTML SHA all agree."""
    value={"observation_id":captured.get("observation_id"),"run_id":captured.get("run_id"),
        "parent_run_id":captured.get("parent_run_id"),"parent_observation_ids":captured.get("parent_observation_ids"),
        "original_product_title_raw":captured.get("product_title_raw"),"corrected_source_title":None,
        "observed_at":captured.get("observed_at"),"raw_product_url":captured.get("raw_product_url"),
        "representation":captured.get("representation"),"representation_slot":captured.get("representation_slot"),
        "ranking_position":captured.get("ranking_position"),"rank_context":captured.get("rank_context"),
        "source_id":captured.get("source_id"),"source_type":captured.get("source_type"),
        "evidence_path":captured.get("evidence_path"),"source_html_sha256":captured.get("evidence_sha256"),
        "title_review_version":TITLE_REVIEW_VERSION,"title_parser_version":adapters.ADAPTER_VERSION,
        "is_derivation":True,"review_status":"UNVERIFIED","reason":None}
    try:
        if source.get("source_id")!=captured.get("source_id") or source.get("source_type")!=captured.get("source_type"):
            raise ValueError("SOURCE_BINDING_CONFLICT")
        representation=captured.get("representation")
        if representation!="SERVER" and not re.fullmatch(r"ACP_\d+",representation or ""):
            raise ValueError("UNKNOWN_REPRESENTATION")
        slot=captured.get("representation_slot")
        if type(slot) is not int or slot<0 or not captured.get("raw_product_url"):
            raise ValueError("EXACT_CARD_BINDING_REQUIRED")
        path=captured["evidence_path"]; expected=captured["evidence_sha256"]
        key=(path,expected)
        cache=cache if cache is not None else {}
        if key not in cache:
            data=Path(path).read_bytes()
            if digest(data)!=expected:raise ValueError("SOURCE_HTML_HASH_MISMATCH")
            documents=[k for k in cache if len(k)==2]
            if len(documents)>=32:
                expired=documents[0]
                for item in list(cache):
                    if (item==expired or (item[0]=="cards" and item[1:3]==expired)
                            or (item[0]=="title_node" and item[1][1:3]==expired)):
                        del cache[item]
            cache[key]=BeautifulSoup(data.decode("utf-8"),"lxml")
        soup=cache[key]
        page_url=captured.get("page_url") or captured.get("source_url")
        if not page_url:raise ValueError("SOURCE_PAGE_URL_REQUIRED")
        parsed_key=("cards",path,expected,digest(source),page_url,representation,captured["run_id"],
            captured.get("snapshot_id"),captured.get("observed_at"),captured.get("page_number"),adapters.ADAPTER_VERSION)
        if parsed_key not in cache:
            rows=adapters._parse_cards(soup,source=source,run_id=captured["run_id"],
                snapshot_id=captured.get("snapshot_id"),page_url=page_url,page_number=captured.get("page_number"),
                observed_at=captured.get("observed_at"),evidence_path=path,sha=expected,
                representation=representation,fragment=representation.startswith("ACP_"))
            cache[parsed_key]=(rows,adapters._cards(soup,representation.startswith("ACP_")))
        rows,cards=cache[parsed_key]
        matching=[r for r in rows if r["representation_slot"]==slot and r["raw_product_url"]==captured["raw_product_url"]
            and r["rank_context"]==captured.get("rank_context") and r["ranking_position"]==captured.get("ranking_position")]
        if len(matching)!=1:raise ValueError("CARD_URL_SLOT_RANK_CONTEXT_CONFLICT")
        if slot>=len(cards):raise ValueError("CARD_SLOT_MISSING")
        title=matching[0]["product_title_raw"]
        title_evidence=matching[0].get("product_title_evidence",{})
        if (title_evidence.get("kind") not in {"SOURCE_VISIBLE_PRODUCT_TITLE","SOURCE_IMAGE_ALT"}
                or title_evidence.get("text_raw")!=title or title_evidence.get("evidence_sha256")!=expected
                or title_evidence.get("evidence_path")!=path):
            raise ValueError("CURRENT_SOURCE_TITLE_EVIDENCE_REQUIRED")
        proof_key=("title_node",parsed_key,slot,captured["raw_product_url"],title)
        if proof_key not in cache:cache[proof_key]=_source_title_proof(cards[slot],captured["raw_product_url"],title,title_evidence)
        proof=cache[proof_key]
        if not proof:raise ValueError("SOURCE_PRODUCT_TITLE_NOT_PROVEN")
        value.update(corrected_source_title=title,title_source_proof=proof,
            product_title_evidence=title_evidence,
            review_status="UNCHANGED" if title==captured.get("product_title_raw") else "CORRECTED")
    except (ValueError,KeyError,OSError,UnicodeError,TypeError) as exc:
        value["reason"]=str(exc)
    return value


def review_titles(store, run_id, output_root):
    """Snapshot the index read-only, then write a new exclusive delivery artifact."""
    database=store.root/"index.sqlite3"
    with sqlite3.connect("file:"+quote(database.resolve().as_posix(),safe="/:")+"?mode=ro",uri=True) as connection:
        connection.execute("BEGIN")
        run=connection.execute("SELECT plan_json,started_at FROM runs WHERE run_id=?",(run_id,)).fetchone()
        if run is None:raise ValueError("RUN_NOT_FOUND")
        saved=connection.execute("SELECT data_json FROM observations WHERE run_id=? ORDER BY rowid",(run_id,)).fetchall()
        connection.commit()
    plan=json.loads(run[0]);sources={s["source_id"]:s for s in plan["sources"]}
    directory=Path(output_root)/("title_review_"+uuid.uuid4().hex)
    directory.mkdir(parents=True,exist_ok=False)
    output=directory/"title_corrections.jsonl";counts=Counter();cache={}
    with output.open("x",encoding="utf-8") as handle:
        for saved_row in saved:
            row=json.loads(saved_row[0]);projected=project_title(row,sources.get(row.get("source_id"),{}),cache=cache)
            counts[projected["review_status"]]+=1
            handle.write(json.dumps(projected,ensure_ascii=False,allow_nan=False)+"\n")
    manifest={"schema_version":1,"run_id":run_id,"parent_started_at":run[1],"source_plan_sha256":digest(plan),
        "is_derivation":True,"title_review_version":TITLE_REVIEW_VERSION,"title_parser_version":adapters.ADAPTER_VERSION,
        "indexed_observations":len(saved),"review_counts":dict(counts),"live_requests":0,
        "original_rows_modified":False,"identity_counts_modified":False,"snapshot_scope":"READ_ONLY_INDEX_TRANSACTION",
        "artifacts":{"title_corrections":{"path":str(output.resolve()),"sha256":digest(output.read_bytes())}}}
    immutable_json(directory/"manifest.json",sealed(manifest))
    return {"directory":str(directory.resolve()),**manifest}


def load_title_projection(manifest_path, store, run_id):
    """Validate every correction against current immutable source rows and DOM."""
    from ..multisource.storage import read_sealed
    manifest=read_sealed(manifest_path)
    run=store.get_run(run_id)
    if (manifest.get("run_id")!=run_id or manifest.get("source_plan_sha256")!=digest(run["plan"])
            or manifest.get("title_review_version")!=TITLE_REVIEW_VERSION
            or manifest.get("title_parser_version")!=adapters.ADAPTER_VERSION):
        raise ValueError("TITLE_REVIEW_RUN_PLAN_OR_VERSION_CONFLICT")
    reference=manifest["artifacts"]["title_corrections"]
    data=Path(reference["path"]).read_bytes()
    if digest(data)!=reference["sha256"]:raise ValueError("TITLE_REVIEW_ARTIFACT_HASH_MISMATCH")
    originals={r["observation_id"]:r for r in store.iter_observations(run_id)}
    sources={s["source_id"]:s for s in run["plan"]["sources"]}
    verified={};cache={};counts=Counter()
    for line in data.decode("utf-8").splitlines():
        row=json.loads(line);oid=row.get("observation_id")
        if oid in verified or oid not in originals:raise ValueError("TITLE_REVIEW_OBSERVATION_CONFLICT")
        current=project_title(originals[oid],sources.get(originals[oid].get("source_id"),{}),cache=cache)
        if row!=current:raise ValueError("TITLE_REVIEW_SOURCE_BINDING_OR_CORRECTION_CONFLICT")
        verified[oid]=row;counts[row["review_status"]]+=1
    if len(verified)!=manifest["indexed_observations"] or dict(counts)!=manifest["review_counts"]:
        raise ValueError("TITLE_REVIEW_COUNTS_CONFLICT")
    return verified


def supplement_title_review(base_manifest_path, store, run_id, output_root):
    """Create a new paused snapshot; reuse hashed base and review only new rows.

    The returned artifact still requires full load_title_projection validation.
    This function never rewrites or claims validation of the original base.
    """
    from ..multisource.storage import read_sealed
    base=read_sealed(base_manifest_path)
    run=store.get_run(run_id)
    if (base.get("run_id")!=run_id or base.get("source_plan_sha256")!=digest(run["plan"])
            or base.get("title_review_version")!=TITLE_REVIEW_VERSION
            or base.get("title_parser_version")!=adapters.ADAPTER_VERSION):
        raise ValueError("TITLE_REVIEW_BASE_RUN_OR_VERSION_CONFLICT")
    reference=base["artifacts"]["title_corrections"];data=Path(reference["path"]).read_bytes()
    if digest(data)!=reference["sha256"]:raise ValueError("TITLE_REVIEW_BASE_HASH_MISMATCH")
    original_map={r["observation_id"]:r for r in store.iter_observations(run_id)}
    old={};old_counts=Counter()
    for line in data.decode("utf-8").splitlines():
        row=json.loads(line);oid=row["observation_id"]
        if oid in old or oid not in original_map:raise ValueError("TITLE_REVIEW_BASE_OBSERVATION_CONFLICT")
        old[oid]=row;old_counts[row["review_status"]]+=1
    if len(old)!=base["indexed_observations"] or dict(old_counts)!=base["review_counts"]:
        raise ValueError("TITLE_REVIEW_BASE_COUNTS_CONFLICT")
    directory=Path(output_root)/("title_review_"+uuid.uuid4().hex);directory.mkdir(parents=True,exist_ok=False)
    output=directory/"title_corrections.jsonl";counts=Counter();cache={};additional=0
    sources={s["source_id"]:s for s in run["plan"]["sources"]}
    with output.open("x",encoding="utf-8") as handle:
        for oid,captured in original_map.items():
            row=old.get(oid)
            if row is None:
                row=project_title(captured,sources.get(captured.get("source_id"),{}),cache=cache);additional+=1
            counts[row["review_status"]]+=1
            handle.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n")
    manifest={**base,"indexed_observations":len(original_map),"review_counts":dict(counts),
        "snapshot_scope":"PAUSED_INDEX_SUPPLEMENT_REQUIRES_FULL_REVALIDATION",
        "base_review_manifest":{"path":str(Path(base_manifest_path).resolve()),"sha256":digest(Path(base_manifest_path).read_bytes())},
        "base_review_artifact":reference,"base_review_observations":len(old),"newly_reviewed_observations":additional,
        "full_revalidation_required":True,
        "artifacts":{"title_corrections":{"path":str(output.resolve()),"sha256":digest(output.read_bytes())}}}
    immutable_json(directory/"manifest.json",sealed(manifest))
    return {"directory":str(directory.resolve()),**manifest}
