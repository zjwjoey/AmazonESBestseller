"""Versioned offline normalization views; never rewrite a captured snapshot."""
from collections import Counter
from pathlib import Path
import json
import uuid

from ..multisource.storage import digest, immutable_json, sealed
from .links import NORMALIZER_VERSION, candidate_id, normalize_product_url


def renormalize_run(store, run_id, output_root):
    """Recompute from preserved hrefs, retaining every original observation.

    A derivation is deliberately not a new capture or a daily snapshot. Original
    observation IDs, capture times, HTML references and the parent run remain
    explicit, while candidate fields belong to the new normalization version.
    """
    original = store.get_run(run_id)
    rows, candidates, counts, raw_seen = [], {}, Counter(), set()
    for captured in store.iter_observations(run_id):
        value = normalize_product_url(captured.get("raw_product_url"),
            base_url=captured.get("source_url") or "https://www.amazon.es")
        cid = candidate_id(value["normalized_product_url"]) if value["is_valid"] else None
        pair = (cid, captured.get("raw_product_url"))
        status = "INVALID_PRODUCT_LINK"
        if cid:
            status = ("EXACT_DUPLICATE" if pair in raw_seen else "NORMALIZED_DUPLICATE") if cid in candidates else "UNIQUE_LINK_CANDIDATE"
            raw_seen.add(pair)
            first = candidates.setdefault(cid, {"candidate_id": cid,
                "normalized_product_url": value["normalized_product_url"],
                "possible_duplicate_key": value["possible_duplicate_key"],
                "first_seen": captured.get("observed_at"), "observation_count": 0,
                "url_identity_status": "IDENTITY_UNCONFIRMED", "final_product_url": None,
                "source_types": [], "research_categories": []})
            first["observation_count"] += 1
            for field, raw_field in (("source_types", "source_type"), ("research_categories", "research_category")):
                label = captured.get(raw_field)
                if label is not None and label not in first[field]:
                    first[field].append(label)
        row = {**captured, **value, "candidate_id": cid, "dedup_status": status,
            "parent_run_id": run_id, "parent_observation_id": captured["observation_id"],
            "normalizer_version": NORMALIZER_VERSION, "is_derivation": True}
        rows.append(row)
        counts[status] += 1
    groups = {}
    for cid, candidate in candidates.items():
        key = candidate["possible_duplicate_key"] or candidate["normalized_product_url"]
        groups.setdefault(key, []).append(cid)
    for key, members in groups.items():
        representative = min(members)
        for cid in members:
            candidates[cid].update(target_group_key=key, target_eligible=cid == representative,
                dedup_status="POSSIBLE_DUPLICATE" if len(members) > 1 else "UNIQUE_LINK_CANDIDATE")
    directory = Path(output_root) / ("renormalized_" + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, values in (("observations", rows), ("candidates", list(candidates.values()))):
        path = directory / (name + ".jsonl")
        with path.open("x", encoding="utf-8") as handle:
            for row in values:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        files[name] = {"path": str(path.resolve()), "sha256": digest(path.read_bytes())}
    manifest = {"schema_version": 2, "is_derivation": True, "origin": "OFFLINE_RENORMALIZATION",
        "parent_run_id": run_id, "parent_started_at": original["started_at"],
        "parent_plan_sha256": digest(original["plan"]), "normalizer_version": NORMALIZER_VERSION,
        "raw_observations": len(rows), "normalized_unique_urls": len(candidates),
        "unique_valid_links": len(groups), "strict_independent_buyable_products": 0,
        "dedup_counts": dict(counts), "artifacts": files,
        "identity_reviews_applied": False,
        "capture_times_preserved": True, "live_requests": 0}
    # Budget-stopped journals remain separate raw evidence. They must not be
    # silently promoted into the accepted candidate target by this operation.
    pending = store.summary(run_id).get("pending_page_journals", [])
    if pending:
        from ..multisource.storage import read_sealed
        preserved=[]
        for reference in pending:
            journal=read_sealed(reference["journal_path"])
            preserved.extend({**row,"unindexed_raw_evidence":True,
                "parent_run_id":run_id,"target_eligible":False,
                "journal_path":reference["journal_path"]} for row in journal["observations"])
        path=directory/"unindexed_preserved_observations.jsonl"
        with path.open("x",encoding="utf-8") as handle:
            for row in preserved:
                handle.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n")
        manifest.update(pending_page_journals=pending,unindexed_preserved_raw_observations=len(preserved))
        manifest["artifacts"]["unindexed_preserved_observations"]={"path":str(path.resolve()),"sha256":digest(path.read_bytes())}
    immutable_json(directory / "manifest.json", sealed(manifest))
    return {"directory": str(directory.resolve()), **manifest}
