"""Small deterministic evidence contracts shared by the offline commands."""
from __future__ import annotations

import csv
import io
import json
import subprocess
from pathlib import Path

from ..multisource.storage import canonical_bytes, digest, immutable_bytes
from . import CONTRACT_VERSION, RULE_VERSION

SUCCESS = {"SUCCESS", "REUSED_VALID_CACHE"}
PROTECTED_FIELDS = (
    "asin", "candidate_id", "selection_id", "parent_asin", "product_url",
    "request_url", "final_url", "image_url", "image_urls", "current_price",
    "current_price_raw", "reference_price", "reference_price_raw", "unit_price",
    "discount_rate", "rating_value", "rating_count", "monthly_bought_raw",
    "monthly_bought_min", "brand", "brand_raw", "model", "model_number", "备注",
)
TEXT_FIELDS = (
    "title_es_raw", "selected_variation_raw", "specification_es",
    "category_path", "feature_bullets_raw", "product_description_raw", "attributes",
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def file_sha(path):
    # Streaming keeps full 5,500-product HTML batches bounded in memory.
    import hashlib
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def child(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts or ":" in str(relative):
        raise ValueError("UNSAFE_EVIDENCE_PATH")
    result = (root / relative).resolve()
    if not result.is_relative_to(root) or result == root:
        raise ValueError("EVIDENCE_PATH_ESCAPE")
    return result


def separate(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("INPUT_OUTPUT_OVERLAP")


def jsonl(rows):
    return b"".join(canonical_bytes(row) + b"\n" for row in rows)


def csv_bytes(rows, fields):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: (json.dumps(value, ensure_ascii=False, sort_keys=True)
                              if isinstance(value, (list, dict)) else value)
                         for key, value in row.items()})
    return buffer.getvalue().encode("utf-8-sig")


def code_sha():
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3],
        text=True, encoding="utf8").strip()


def code_evidence():
    root = Path(__file__).resolve().parents[3]
    # Code SHA alone is insufficient when the development tree is dirty.
    paths = sorted((root / "src/amazon_es_bestseller/source_audit").glob("*.py"))
    paths += [root / "src/amazon_es_bestseller" / p for p in (
        "collection/detail.py", "collection/detail_v2.py", "collection/detail_bridge5500.py",
        "quality/detail_identity.py", "quality/detail_structure.py", "quality/category_provenance.py",
        "translation/field_contract.py", "translation/dictionary_service.py", "normalization/specification.py")]
    return {p.relative_to(root).as_posix(): file_sha(p) for p in paths}


def publish(out, artifacts, *, kind, inputs):
    """Exclusive immutable publication, manifest last; identical reruns are safe."""
    out = Path(out)
    payloads = {name: value if isinstance(value, bytes) else canonical_bytes(value)
                for name, value in artifacts.items()}
    manifest = {
        "kind": kind, "rule_version": RULE_VERSION, "contract_version": CONTRACT_VERSION,
        "code_sha": code_sha(), "code_evidence_sha256": code_evidence(),
        "inputs": inputs, "files": {name: digest(data) for name, data in payloads.items()},
        "amazon_requests": 0, "translation_api_requests": 0,
        "original_evidence_writes": 0, "production_apply": False,
    }
    manifest_name = {"translation_preparation": "translation_preparation_manifest.json",
                     "snapshot": "source_snapshot_manifest.json"}.get(kind, "manifest.json")
    # Detect all conflicts before publishing any new artifact.
    payloads[manifest_name] = canonical_bytes(manifest)
    for name, data in payloads.items():
        target = child(out, name)
        if target.exists() and target.read_bytes() != data:
            raise ValueError("IMMUTABLE_OUTPUT_CONFLICT: " + name)
    for name, data in payloads.items():
        immutable_bytes(child(out, name), data)
    return manifest


def verify_bundle(root, manifest_name="manifest.json", *, kind=None):
    root = Path(root)
    manifest_path = child(root, manifest_name)
    manifest = read_json(manifest_path)
    if kind and manifest.get("kind") != kind:
        raise ValueError("UNEXPECTED_BUNDLE_KIND")
    if not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        raise ValueError("EMPTY_OR_INVALID_INPUT_MANIFEST")
    for name, expected in manifest["files"].items():
        if file_sha(child(root, name)) != expected:
            raise ValueError("INPUT_SHA_MISMATCH: " + name)
    return manifest, file_sha(manifest_path)
