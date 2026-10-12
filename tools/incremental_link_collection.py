"""Audited opt-in serial switch from a paused link-first run to detail reuse.

Copies immutable evidence to a NEW run; keeps the original frozen. The current
detail page still confirms identity and volatile values. No ranking ASIN parse,
translation, parallel scraping, or access-policy change is introduced.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amazon_es_bestseller.monitoring.link_first_reuse import reconcile_current_detail  # noqa: E402
from amazon_es_bestseller.orchestration.checkpoint import read_json, write_json_atomic  # noqa: E402
from amazon_es_bestseller.orchestration.link_first import (  # noqa: E402
    LinkFirstRunner, canonical_json_sha256, file_hash, immutable_json, utc,
)
from amazon_es_bestseller.runtime_state import VersionedCheckpointStore  # noqa: E402


def copy_evidence(origin, output, relative, hashes):
    source, target = (origin / relative).resolve(), (output / relative).resolve()
    source.relative_to(origin)
    target.relative_to(output)
    expected = file_hash(source)
    if target.exists():
        if file_hash(target) != expected:
            raise ValueError("MIGRATED_EVIDENCE_DRIFT: " + relative)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    hashes[relative] = expected


def freeze_switch(origin, output, baseline, scope, plan):
    if origin == output or origin in output.parents or output in origin.parents:
        raise ValueError("SWITCH_REQUIRES_INDEPENDENT_NEW_RUN")
    existing = output / "incremental_inputs.json"
    if existing.exists():
        value = read_json(existing, None)
        if (value["origin"] != str(origin) or value["baseline"] != str(baseline)
                or value["scope"] != str(scope) or value["plan"] != str(plan)):
            raise ValueError("SWITCH_INPUT_PATH_DRIFT")
        for name, expected in value["input_hashes"].items():
            if file_hash(Path(name)) != expected:
                raise ValueError("SWITCH_INPUT_DRIFT: " + name)
        return value
    state = VersionedCheckpointStore(origin / "checkpoints", "link_first_state").load()
    if state["status"] not in {"RUNNING", "PREPARED", "SAMPLE_COMPLETE"} or state["phase"] != "FULL":
        raise ValueError("SWITCH_REQUIRES_PASSED_SAMPLE_AND_NO_ACCESS_HOLD")
    if canonical_json_sha256(read_json(plan, None)) != read_json(origin / "run_metadata.json", {})["reviewed_plan_sha256"]:
        raise ValueError("SWITCH_REVIEWED_PLAN_DRIFT")
    paths = [baseline, scope, plan, origin / "run_metadata.json",
             origin / "checkpoints" / "link_first_state.latest.json"]
    hashes, segments, links = {}, {}, []
    for segment, expected in state["completed_segments"].items():
        relative = "ranking_snapshots/" + segment + "/manifest.json"
        manifest = read_json(origin / relative, None)
        if canonical_json_sha256(manifest) != expected or manifest.get("asin_extraction") is not False:
            raise ValueError("SWITCH_SNAPSHOT_DRIFT")
        for page in manifest["pages"]:
            if file_hash(origin / page["html_file"]) != page["html_sha256"]:
                raise ValueError("SWITCH_RANKING_HTML_DRIFT")
        segments[segment] = expected
        links.extend(manifest["records"])
    by_id = {r["snapshot_item_id"]: r for r in links}
    details = dict(state["detail_results"])
    for binding in details.values():
        if file_hash(origin / binding["result_file"]) != binding["sha256"]:
            raise ValueError("SWITCH_RESULT_DRIFT")
    # Include checkpoint-orphan results only when independently bound to a frozen link.
    for path in sorted((origin / "detail_evidence").glob("*/result.json")):
        result = read_json(path, None)
        item_id = path.parent.name
        if (item_id not in by_id or result.get("snapshot_item_id") != item_id
                or result.get("requested_url") != by_id[item_id]["product_link"]):
            raise ValueError("SWITCH_UNBOUND_RESULT")
        if result.get("html_file") and file_hash(origin / result["html_file"]) != result["html_sha256"]:
            raise ValueError("SWITCH_DETAIL_HTML_DRIFT")
        details.setdefault(item_id, {"result_file": str(path.relative_to(origin)),
                                    "sha256": file_hash(path), "status": result["status"]})
    for directory in ("ranking_snapshots", "detail_evidence"):
        for path in sorted((origin / directory).rglob("*")):
            if path.is_file():
                copy_evidence(origin, output, str(path.relative_to(origin)), hashes)
    if (origin / "sample_gate.json").is_file():
        copy_evidence(origin, output, "sample_gate.json", hashes)
    # An interrupted capture already on disk is parsed offline, not requested again.
    feeds = {}
    for path in sorted((output / "detail_evidence").glob("*/*.meta.json")):
        item_id = path.parent.name
        meta = read_json(path, {})
        if item_id in details or item_id not in by_id:
            continue
        if (meta.get("requested_url") == by_id[item_id]["product_link"]
                and meta.get("access_state") == "NORMAL" and not meta.get("error")
                and (output / meta.get("html_file", "")).is_file()):
            if file_hash(output / meta["html_file"]) != meta["html_sha256"]:
                raise ValueError("SWITCH_PENDING_HTML_DRIFT")
            feeds[item_id] = meta
    value = {"contract": "link-first-incremental-switch-v1", "created_at": utc(),
             "origin": str(origin), "baseline": str(baseline), "scope": str(scope), "plan": str(plan),
             "input_hashes": {str(p): file_hash(p) for p in paths}, "copied_file_hashes": hashes,
             "completed_segments": segments, "detail_results": details, "saved_capture_feeds": feeds,
             "snapshot_link_count": len(links), "origin_state": state,
             "authorization": "User approved pause full detail and switch to incremental reuse",
             "snapshot_asin_extraction": False, "translation": "disabled", "scheduler": "serial"}
    immutable_json(existing, value)
    return value


class IncrementalRunner(LinkFirstRunner):
    def __init__(self, plan, output, root, *, origin, baseline, scope, resume=False, **kwargs):
        self.origin = origin.resolve()
        self.switch = freeze_switch(self.origin, output.resolve(), baseline.resolve(), scope.resolve(), plan.resolve())
        self.entry_hash = file_hash(Path(__file__))
        self.switch_hash = file_hash(output / "incremental_inputs.json")
        rows = read_json(baseline, None)
        allowed = set(read_json(scope, {})["binding_scope"]["asins"])
        if len(rows) != len({r["asin"] for r in rows}) or {r["asin"] for r in rows} != allowed:
            raise ValueError("BASELINE_ASIN_SCOPE_MISMATCH")
        self.baseline = {r["asin"]: r for r in rows}
        self.feed_by_id = self.switch["saved_capture_feeds"]
        prior_metadata = read_json(self.origin / "run_metadata.json", {})
        super().__init__(plan, output, root, sample_size=prior_metadata["sample_size"],
                         continue_after_sample=True, resume=resume, **kwargs)
        for name in ("completed_segments", "detail_results"):
            for key, value in self.switch[name].items():
                self.state[name].setdefault(key, value)
        self.links, self.records, self.selected = [], {}, {}
        self._recover()
        if not resume:
            self.state.update(phase="FULL", active_url="", error="", hold_token="",
                              last_category=self.switch["origin_state"]["last_category"])
            self.save("PREPARED")

    def runtime_metadata(self):
        return {"previous_details_reuse": True, "detail_reuse_policy": "exact-reparsed-stable-fields",
                "incremental_inputs_sha256": self.switch_hash, "incremental_entry_sha256": self.entry_hash,
                "identity_navigation_still_required": True}

    def guards(self):
        super().guards()
        if file_hash(Path(__file__)) != self.entry_hash or file_hash(self.output / "incremental_inputs.json") != self.switch_hash:
            raise ValueError("INCREMENTAL_CODE_OR_INPUT_DRIFT")
        for name, expected in self.switch["input_hashes"].items():
            if file_hash(Path(name)) != expected:
                raise ValueError("SWITCH_INPUT_DRIFT: " + name)

    def _snapshot(self, session, category, role):
        # Preserve original segment IDs/observation times instead of taking another snapshot.
        for segment in self.switch["completed_segments"]:
            manifest = read_json(self.output / "ranking_snapshots" / segment / "manifest.json", {})
            if manifest["research_category"] == category["research_category"] and manifest["role"] == role:
                return
        return super()._snapshot(session, category, role)

    def _capture(self, session, url, destination, **kwargs):
        meta = self.feed_by_id.get(destination.name)
        if meta:
            self.guards()
            if meta["requested_url"] != url or file_hash(self.output / meta["html_file"]) != meta["html_sha256"]:
                raise ValueError("SAVED_CAPTURE_BINDING_DRIFT")
            return (self.output / meta["html_file"]).read_text(encoding="utf-8"), dict(meta)
        return super()._capture(session, url, destination, **kwargs)

    def parse_detail_evidence(self, html, identity, link, meta):
        return reconcile_current_detail(html, identity, meta, self.baseline.get(identity.get("asin")))

    def save(self, status, **extra):
        super().save(status, **extra)
        counts = Counter(r.get("detail_reuse_provenance", {}).get("action", "PRESERVED_CURRENT_FULL_DETAIL")
                         for r in self.records.values())
        write_json_atomic(self.output / "incremental_progress.json", {
            "status": status, "updated_at": utc(), "confirmed_unique": len(self.records),
            "selected_unique": len(self.selected), "actions": dict(counts),
            "baseline_unique": len(self.baseline), "preserved_origin_results": len(self.switch["detail_results"]),
            "identity_navigation_still_required": True, "translation": "disabled"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "out-dir", "origin", "baseline", "scope"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    # Lock BOTH parent and new run. A stale full-run restart must fail while this is active.
    from contextlib import ExitStack
    with ExitStack() as stack:
        for directory in (args.origin, args.out_dir):
            handle = stack.enter_context((directory / "process.lock").open("a+b"))
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0, 2)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run = IncrementalRunner(args.plan, args.out_dir, ROOT, origin=args.origin,
                                baseline=args.baseline, scope=args.scope, resume=args.resume)
        if args.prepare_only:
            run.guards()
            print("INCREMENTAL_PREPARED; no browser/network started", flush=True)
            return
        try:
            print(run.run(profile_dir=args.profile_dir), flush=True)
        except Exception as exc:
            run.save("ERROR", error=str(exc))
            raise


if __name__ == "__main__":
    main()
