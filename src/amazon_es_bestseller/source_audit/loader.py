"""Read only hash-verified snapshots, never audit an active collector in place."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from ..multisource.storage import digest, immutable_bytes, immutable_json, read_sealed
from . import RULE_VERSION
from .contracts import child, code_evidence, code_sha, file_sha, read_json, separate, verify_bundle

TOP_LEVEL = {
    "selected_5500.json", "detail_plan_5500.json", "preparation_manifest.json",
    "detail_run_manifest.json", "details_5500_raw.jsonl", "details_5500.csv",
    "failed_details.csv", "identity_review_queue.csv", "source_input_certificate.json",
    "details.jsonl", "details.json", "details.csv", "sample_provenance.json",
    "FINAL_acceptance.json", "T1_acceptance.json", "T2_acceptance.json",
}
REFERENCE_FIELDS = {"html_path", "receipt_path", "cache_metadata_path", "response_metadata_path"}


def inventory(source):
    paths = {p for p in source.iterdir() if p.is_file() and p.name in TOP_LEVEL}
    paths.update(p for p in (source / "tasks").glob("**/*") if p.is_file() and (
        p.name in {"checkpoint.json", "parsed_detail.json"}
        or p.name.startswith("response_metadata") and p.suffix == ".json"
        or p.suffix == ".html"))
    paths.update(p for p in (source / "receipts").glob("*.json") if p.is_file())
    return sorted(paths)


def _references(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in REFERENCE_FIELDS and isinstance(item, str) and item:
                yield item
            elif isinstance(item, (dict, list)):
                yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


def snapshot(source, out, *, data_kind="DETAIL5500"):
    """Double-inventory copy, with before/copy/after hashes. No source locks/writes.

    This creates a frozen *copy*, not proof of a globally frozen collector.
    A mutation aborts publication (manifest absent). Nothing is restarted.
    """
    source, out = Path(source).resolve(), Path(out).resolve()
    separate(source, out)
    original = inventory(source)
    if not original:
        raise ValueError("NO_SUPPORTED_DETAIL_INPUT")
    aliases, missing = {}, []
    for p in original:
        # Disallow symlinks leaving the selected source tree.
        child(source, p.relative_to(source))
        aliases[str(p.resolve())] = p.relative_to(source).as_posix()
    references = set()
    for p in original:
        if p.suffix == ".json" and (p.name == "checkpoint.json" or p.name.startswith("response_metadata")):
            references.update(_references(read_json(p)))
    for ref in sorted(references):
        path = Path(ref)
        path = (source / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file():
            missing.append(str(path))
            continue
        if path.suffix.lower() not in {".html", ".json"} or path.is_symlink():
            raise ValueError("UNSUPPORTED_EXTERNAL_EVIDENCE_REFERENCE")
        if str(path) not in aliases:
            aliases[str(path)] = "evidence/" + digest(str(path)) + path.suffix
    plan_path = source / "detail_plan_5500.json"
    if plan_path in original:
        ref = read_json(plan_path).get("source_snapshot_reference") or {}
        if ref.get("directory"):
            path = Path(ref["directory"]).resolve() / "manifest.json"
            if path.is_file():
                aliases[str(path)] = "source_origin/manifest.json"
            else:
                missing.append(str(path))
    before = {path: file_sha(path) for path in sorted(aliases)}
    # One file at a time; do not load a full 5,500-HTML batch in RAM.
    for path, relative in aliases.items():
        body = Path(path).read_bytes()
        if digest(body) != before[path]:
            raise ValueError("INPUT_CHANGED_DURING_SNAPSHOT")
        immutable_bytes(child(out, relative), body)
    if inventory(source) != original or any(file_sha(path) != sha for path, sha in before.items()):
        raise ValueError("INPUT_CHANGED_DURING_SNAPSHOT")
    manifest = {
        "kind": "snapshot", "rule_version": RULE_VERSION, "frozen_copy": True,
        "consistency": "CONSISTENT_HASH_VERIFIED_COPY_NONATOMIC",
        "source_globally_frozen_proven": False, "data_kind": data_kind,
        "source_root": str(source), "code_sha": code_sha(), "code_evidence_sha256": code_evidence(),
        "source_files": before, "aliases": aliases, "missing_references": missing,
        "files": {relative: before[path] for path, relative in aliases.items()},
        "amazon_requests": 0, "translation_api_requests": 0, "original_evidence_writes": 0,
    }
    immutable_json(out / "source_snapshot_manifest.json", manifest)
    verify_bundle(out, "source_snapshot_manifest.json", kind="snapshot")
    return manifest


class Snapshot:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest, self.sha256 = verify_bundle(self.root, "source_snapshot_manifest.json", kind="snapshot")
        if self.manifest.get("frozen_copy") is not True:
            raise ValueError("FROZEN_SNAPSHOT_REQUIRED")
        self.files = self.manifest["files"]

    def path(self, name):
        if name not in self.files:
            raise ValueError("UNDECLARED_SNAPSHOT_FILE: " + name)
        return child(self.root, name)

    def json(self, name, default=None):
        return read_json(self.path(name)) if name in self.files else default

    def reference(self, ref):
        # Original absolute references resolve only via the frozen alias map.
        name = self.manifest.get("aliases", {}).get(str(ref))
        if name is None:
            source = Path(self.manifest["source_root"])
            name = self.manifest.get("aliases", {}).get(str((source / str(ref)).resolve()))
        if name is None and str(ref) in self.files:
            name = str(ref)
        if name is None:
            raise ValueError("EVIDENCE_NOT_IN_FROZEN_SNAPSHOT: " + str(ref))
        return self.path(name)

    def verify(self):
        _, sha = verify_bundle(self.root, "source_snapshot_manifest.json", kind="snapshot")
        if sha != self.sha256:
            raise ValueError("SNAPSHOT_MANIFEST_CHANGED")

    def rows(self):
        for name in ("details_5500_raw.jsonl", "details.jsonl", "details.json", "details_5500.csv", "details.csv"):
            if name not in self.files:
                continue
            text = self.path(name).read_text(encoding="utf-8-sig")
            if name.endswith(".jsonl"):
                return [json.loads(line) for line in text.splitlines() if line.strip()]
            if name.endswith(".json"):
                value = json.loads(text)
                if not isinstance(value, list):
                    raise ValueError("DETAIL_ROWS_MUST_BE_LIST")
                return value
            result = list(csv.DictReader(io.StringIO(text)))
            # The existing exporter serializes nested values as JSON in cells.
            for row in result:
                for key, value in list(row.items()):
                    if value and value[0:1] in {"[", "{"}:
                        try:
                            row[key] = json.loads(value)
                        except ValueError:
                            pass
            return result
        return []

    def tasks(self):
        plan = self.json("detail_plan_5500.json", {})
        if plan:
            if not isinstance(plan.get("tasks"), list):
                raise ValueError("PLAN_TASKS_MUST_BE_LIST")
            return plan["tasks"]
        # Historical input must still supply candidate/selection/real URL.
        return self.json("selected_5500.json", None) or self.rows()

    def outcome(self, task, rows_by_candidate):
        name = "tasks/" + str(task.get("task_id") or "") + "/checkpoint.json"
        if name in self.files:
            return read_sealed(self.path(name))
        row = rows_by_candidate.get(task.get("candidate_id"))
        if row is not None:
            return {**row, "record": row.get("record", row), "status": row.get("status", "UNVERIFIED")}
        return {"status": task.get("status") or "SOURCE_MISSING"}
