"""Offline, exact-evidence reuse after CURRENT detail-child identity confirmation.

No identity is derived from ranking URLs. A current page must still be visited;
cache reuse never claims to eliminate those identity/volatile-field requests.
All current raw fields survive even when historical evidence is unusable.
"""
from copy import deepcopy
from pathlib import Path
import re

from ..collection.detail import parse_detail_page
from ..normalization.spanish_candidate import spanish_candidate_fields
from ..orchestration.link_first import canonical_json_sha256, file_hash
from ..quality.source_fields import audit_source_fields
from ..quality.staged_fields import audit_detail_fields, resolve_selected_identity

STABLE_FIELDS = (
    "title_es_raw", "brand_raw", "selected_variation_raw", "attributes",
    "feature_bullets_raw", "detail_bullets_raw", "product_description_raw",
    "detail_category_trail", "date_first_available_raw", "parent_asin",
)

_VOLATILE_LABEL = re.compile(
    r"^(?:clasificaci[oó]n en los m[aá]s vendidos|best sellers rank|"
    r"valoraci[oó]n|opiniones|customer reviews|precio|price|disponibilidad)", re.I)


def stable_value(name, value):
    if name == "attributes":
        return [a for a in (value or []) if not _VOLATILE_LABEL.search(str(a.get("label_raw", "")))]
    if name == "detail_bullets_raw":
        return [v for v in (value or []) if not _VOLATILE_LABEL.search(str(v))]
    return value


def reconcile_current_detail(html, identity, meta, historical=None):
    """Fail closed for reuse, not collection. Reparse both HTMLs, never trust old JSON."""
    current = parse_detail_page(html, identity["asin"])
    decision = {"action": "FULL_CURRENT_DETAIL", "reason": "NEW_ASIN",
                "current_identity": deepcopy(identity),
                "current_html_sha256": meta["html_sha256"],
                "volatile_fields_observed_at": meta["observed_at"]}
    if identity["status"] != "CONFIRMED":
        decision["reason"] = "CURRENT_IDENTITY_UNCONFIRMED"
    elif historical:
        decision["historical_record_sha256"] = canonical_json_sha256(historical)
        evidence = historical.get("offline_replay_evidence") or {}
        path = Path(evidence.get("html_path") or "")
        expected = evidence.get("html_sha256")
        if historical.get("asin") != identity["asin"]:
            decision["reason"] = "HISTORICAL_IDENTITY_CONFLICT"
        elif not expected or not path.is_file():
            decision["reason"] = "HISTORICAL_HTML_MISSING"
        elif file_hash(path) != expected:
            # Tampered evidence is a hard stop, not silently a cache miss.
            raise ValueError("HISTORICAL_HTML_DRIFT: " + str(path))
        else:
            old_html = path.read_text(encoding="utf-8")
            old_identity = resolve_selected_identity(
                old_html, historical.get("requested_url") or historical.get("product_url") or "",
                historical.get("final_url") or historical.get("product_url") or "")
            old = parse_detail_page(old_html, identity["asin"])
            old.update(spanish_candidate_fields(old))
            staged = audit_detail_fields(old_html, old, old_identity)
            source = audit_source_fields([old])
            risky = [issue for issue in source["issues"]
                     if issue.get("severity") in {"P0", "P1"}
                     or issue.get("issue_code") in {
                         "PARENT_DETAIL_SCOPE_REVIEW", "UNIT_SEMANTICS_AMBIGUOUS",
                         "SOURCE_SEMANTIC_CONFLICT", "SPEC_UNIT_TYPE_MISMATCH", "MISPLACED"}]
            decision.update(historical_html_file=str(path), historical_html_sha256=expected,
                            historical_audit_codes=sorted({i["issue_code"] for i in risky}),
                            historical_identity=old_identity,
                            historical_observed_at=historical.get("observed_at"))
            if old_identity["status"] != "CONFIRMED" or old_identity["asin"] != identity["asin"]:
                decision["reason"] = "HISTORICAL_IDENTITY_CONFLICT"
            elif staged["status"] == "BLOCK" or risky:
                decision["reason"] = "HISTORICAL_FIELD_REVIEW_REQUIRED"
            else:
                changed = [name for name in STABLE_FIELDS
                           if stable_value(name, old.get(name)) != stable_value(name, current.get(name))]
                decision["changed_stable_fields"] = changed
                if changed:
                    decision["reason"] = "CURRENT_STABLE_CONTENT_CHANGED"
                else:
                    # Exact structural/value comparison guards even unknown attributes,
                    # duplicate labels and bullets. No fields are dropped to gain reuse.
                    for name in STABLE_FIELDS:
                        if name in old and name not in {"attributes", "detail_bullets_raw"}:
                            current[name] = deepcopy(old[name])
                    # Current arrays retain fresh BSR/reviews and ALL current entries.
                    # Stable entries already match the offline-reparsed source exactly.
                    decision.update(action="REUSE_VALIDATED_STABLE_DETAIL",
                                    reason="OFFLINE_REPARSED_EXACT_CURRENT_MATCH",
                                    stable_fields=list(STABLE_FIELDS))
        for name in ("备注", "notes", "human_notes"):
            if name in historical:
                current[name] = deepcopy(historical[name])
    current["detail_reuse_provenance"] = decision
    return current
