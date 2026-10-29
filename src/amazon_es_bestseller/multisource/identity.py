"""Snapshot-only identity governance; production ASIN keys are untouched.

Raw observations and the review journal are immutable. The checkpoint is a
rebuildable view, never the authority for an identity or a manual review.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status
from ..monitoring.ranking_identity.urls import asin_from_product_url, is_valid_asin, normalize_asin
from .contracts import timestamp
from .storage import atomic_json, digest, file_lock, immutable_json, read_sealed, sealed

_NAMESPACE = uuid.UUID("0b53c3c7-9666-4e1d-b559-f0c19d3145ad")
_TRACKING = {"ref", "ref_", "tag", "ascsubtag", "linkcode", "linkid", "creative", "camp"}


def normalize_product_url(value: str | None) -> str | None:
    """Remove only known tracking parameters; preserve variant/identity queries."""
    if not value:
        return None
    parts = urlsplit(value)
    path = re.sub(r"/ref=[^/]*$", "", parts.path)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if k.lower() not in _TRACKING])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, parts.fragment))


def _amazon_url(value) -> bool:
    try:
        parts = urlsplit(str(value or ""))
        return parts.scheme == "https" and parts.hostname in {"amazon.es", "www.amazon.es"} and not parts.username
    except ValueError:
        return False


def _asin(value) -> str | None:
    return normalize_asin(value) if is_valid_asin(value) else None


def identity_fingerprint(observation: dict) -> str:
    """Identity-bearing evidence fingerprint, independent of title and tracking.

    A slot ID locates a raw observation; it cannot authorize carrying a reviewed
    entity over to a parser result that extracts a different child or variant.
    """
    raw = observation.get("raw_product_url") or observation.get("product_url_raw")
    fields = {
        "product_url": normalize_product_url(raw),
        "asin_from_raw_url": _asin(observation.get("asin_from_raw_url")) or asin_from_product_url(raw) or None,
        "asin_from_card": _asin(observation.get("asin_from_card")),
        "asin_observed": _asin(observation.get("asin_observed") or observation.get("observed_asin")),
        "requested_product_url": normalize_product_url(observation.get("requested_product_url")),
        "final_product_url": normalize_product_url(observation.get("final_product_url")),
        "asin_from_final_url": _asin(observation.get("asin_from_final_url")) or asin_from_product_url(observation.get("final_product_url")) or None,
        "canonical_asin_observed": _asin(observation.get("canonical_asin_observed")),
        "parent_asin_observed": _asin(observation.get("parent_asin_observed")),
    }
    for key in ("selected_variant", "selected_variation", "selected_variant_asin", "variation_child_asin", "variation_family_asins"):
        fields[key] = observation.get(key)
    return digest(fields)


class IdentityEvidenceResolver:
    """Require independent page evidence, rather than counting URL rewrites."""

    @staticmethod
    def _detail(proof: dict) -> tuple[dict | None, str | None]:
        required = ("evidence_path", "evidence_sha256", "requested_url", "final_url", "observed_at", "access_status")
        if any(not proof.get(key) for key in required):
            return None, "DETAIL_PROOF_METADATA_MISSING"
        if proof["access_status"] != "NORMAL" or not all(_amazon_url(proof[key]) for key in ("requested_url", "final_url")):
            return None, "DETAIL_PROOF_ACCESS_OR_URL_INVALID"
        try:
            observed = timestamp(proof["observed_at"])
            data = Path(proof["evidence_path"]).read_bytes()
            if digest(data) != proof["evidence_sha256"]:
                return None, "DETAIL_PROOF_CHECKSUM_MISMATCH"
            html = data.decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            return None, "DETAIL_PROOF_INVALID"
        if detect_access_status(proof.get("http_status", 200), html).value != "NORMAL":
            return None, "DETAIL_PROOF_ACCESS_RESTRICTED"
        soup = BeautifulSoup(html, "lxml")
        asins = set()
        # Deliberately avoid recommendation widgets and arbitrary data-asin.
        for element in soup.select("input#ASIN, #buybox input[name=ASIN], #desktop_buybox input[name=ASIN]"):
            if candidate := _asin(element.get("value")):
                asins.add(candidate)
        for row in soup.select("#productDetails_detailBullets_sections1 tr, #productDetails_techSpec_section_1 tr, #detailBullets_feature_div li"):
            text = row.get_text(" ", strip=True)
            match = re.match(r"^ASIN\s*[:：]?\s*([A-Z0-9]{10})(?:\s|$)", text, re.I)
            if match:
                asins.add(match.group(1).upper())
        if len(asins) != 1:
            return None, "DETAIL_PRIMARY_ASIN_MISSING_OR_CONFLICTED"
        actual = next(iter(asins))
        claimed = _asin(proof.get("asin") or proof.get("asin_observed"))
        if claimed and claimed != actual:
            return None, "DETAIL_PROOF_ASIN_NOT_IN_SAVED_PAGE"
        selected_child = _asin(proof.get("selected_variant_asin") or proof.get("variation_child_asin"))
        if selected_child and selected_child != actual:
            return None, "DETAIL_SELECTED_CHILD_PRIMARY_ASIN_CONFLICT"
        requested = asin_from_product_url(proof["requested_url"])
        final = asin_from_product_url(proof["final_url"])
        if not requested or not final or requested != actual or final != actual:
            return None, "DETAIL_REQUEST_FINAL_PAGE_ASIN_CONFLICT"
        canonical = soup.select_one('link[rel="canonical"]')
        canonical_asin = asin_from_product_url(canonical.get("href", "")) if canonical else ""
        if canonical_asin and canonical_asin != actual:
            return None, "DETAIL_CANONICAL_PAGE_ASIN_CONFLICT"
        return {**deepcopy(proof), **observed, "asin": actual, "kind": "DETAIL_PAGE",
                "evidence_group": "DETAIL_PAGE:" + proof["evidence_sha256"], "verified": True}, None

    def resolve(self, observation: dict) -> dict:
        row = deepcopy(observation)
        raw_url = row.get("raw_product_url") or row.get("product_url_raw")
        row["raw_product_url"] = raw_url
        row["normalized_product_url"] = normalize_product_url(raw_url)
        raw_asin = asin_from_product_url(raw_url) or None
        card = _asin(row.get("asin_from_card"))
        observed = _asin(row.get("asin_observed") or row.get("observed_asin"))
        final = asin_from_product_url(row.get("final_product_url")) or _asin(row.get("asin_from_final_url"))
        canonical = _asin(row.get("canonical_asin_observed"))
        parent = _asin(row.get("parent_asin_observed"))
        row.update(asin_from_raw_url=raw_asin, asin_from_card=card, asin_observed=observed,
                   asin_from_final_url=final, parent_asin_observed=parent)
        reasons = []
        evidence = []
        if raw_url and urlsplit(raw_url).netloc and not _amazon_url(raw_url):
            reasons.append("RAW_PRODUCT_URL_OUTSIDE_AMAZON_ES")
        values = {v for v in (raw_asin, card, observed, final, canonical) if v}
        for key, value, group in (("RAW_URL", raw_asin, "RANKING_URL"), ("CARD", card, "RANKING_CARD"),
                                  ("OBSERVED", observed, "RANKING_CARD"), ("FINAL_URL", final, "URL_HINT"),
                                  ("CANONICAL_HINT", canonical, "RANKING_URL")):
            if value:
                evidence.append({"kind": key, "asin": value, "evidence_group": group, "verified": False})
        if len(values) > 1:
            reasons.append("CARD_URL_OBSERVED_ASIN_CONFLICT")
        claimed_raw = _asin(observation.get("asin_from_raw_url"))
        if claimed_raw and claimed_raw != raw_asin:
            reasons.append("CLAIMED_RAW_URL_ASIN_CONFLICT")
        details = []
        for proof in row.get("identity_evidence") or []:
            if not isinstance(proof, dict):
                reasons.append("INVALID_IDENTITY_EVIDENCE")
                continue
            kind = proof.get("kind") or proof.get("type") or proof.get("source")
            if kind == "DETAIL_PAGE":
                verified, error = self._detail(proof)
                if verified and (proof.get("evidence_sha256") == row.get("evidence_sha256") or
                    row.get("evidence_path") and Path(proof["evidence_path"]).resolve() == Path(row["evidence_path"]).resolve()):
                    verified, error = None, "DETAIL_PROOF_NOT_INDEPENDENT_OF_RANKING_PAGE"
                if error:
                    reasons.append(error)
                    evidence.append({**deepcopy(proof), "verified": False, "validation_error": error})
                else:
                    evidence.append(verified)
                    details.append(verified["asin"])
            elif kind not in {"RAW_URL", "CARD", "OBSERVED", "FINAL_URL", "CANONICAL_HINT"}:
                evidence.append({**deepcopy(proof), "verified": False})
        if len(set(details)) > 1 or details and values and set(details) != values:
            reasons.append("RANKING_DETAIL_ASIN_CONFLICT")
        # Parent is a family hint and never a purchasable-child identity anchor.
        if parent and parent in values and row.get("variation_child_asin") and _asin(row["variation_child_asin"]) != parent:
            reasons.append("PARENT_CHILD_IDENTITY_CONFLATION")
        confirmed = bool(not reasons and len(values) == 1 and details and set(details) == values and raw_asin)
        status = "CONFLICT" if reasons else "CONFIRMED" if confirmed else "UNCONFIRMED"
        row.update(product_id=None, identity_status=status, identity_evidence=evidence,
                   identity_conflict_reason=sorted(set(reasons)), review_status="PENDING",
                   confirmed_asin=raw_asin if confirmed else None)
        return row


class ProductIdentityRegistry:
    """Durable review journal and stable internal entities across restarts."""

    def __init__(self, directory: str | Path, *, create=True):
        self.directory = Path(directory)
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)
        self.resolver = IdentityEvidenceResolver()
        self.checkpoint = self.directory / "checkpoint.json"

    def _load(self):
        events = []
        previous = None
        for index, path in enumerate(sorted((self.directory / "events").glob("*.json")), 1):
            event = json.loads(path.read_text(encoding="utf-8"))
            payload = {k: v for k, v in event.items() if k != "event_sha256"}
            if event.get("schema_version") != 1 or event.get("sequence") != index or event.get("previous_sha256") != previous or digest(payload) != event.get("event_sha256"):
                raise ValueError("IDENTITY_JOURNAL_CORRUPT: " + str(path))
            previous = event["event_sha256"]
            events.append(event)
        # A checkpoint can be rebuilt after corruption; a missing committed
        # journal tail cannot be silently treated as an earlier valid history.
        if self.checkpoint.exists():
            try:
                checkpoint = read_sealed(self.checkpoint, recover_backup=True)
            except ValueError:
                checkpoint = None
            if checkpoint and checkpoint.get("event_count", 0) > len(events):
                raise ValueError("IDENTITY_JOURNAL_TAIL_MISSING")
            if checkpoint and checkpoint.get("event_count", 0):
                head = events[checkpoint["event_count"] - 1]["event_sha256"]
                if head != checkpoint.get("head_sha256"):
                    raise ValueError("IDENTITY_JOURNAL_CHECKPOINT_HEAD_CONFLICT")
        observations = {}
        for event in events:
            if event["action"] == "INGEST":
                for item in event["observations"]:
                    path = self.directory / "observations" / (digest(item["observation_id"]) + ".json")
                    try:
                        data = path.read_bytes()
                        if digest(data) != item["sha256"]:
                            raise ValueError("IDENTITY_OBSERVATION_CHECKSUM_MISMATCH")
                        row = json.loads(data)
                        if row.get("observation_id") != item["observation_id"]:
                            raise ValueError("IDENTITY_OBSERVATION_ID_MISMATCH")
                    except (OSError, ValueError) as exc:
                        raise ValueError("IDENTITY_OBSERVATION_CORRUPT: " + str(path)) from exc
                    observations[item["observation_id"]] = row
        return observations, events

    def _append(self, events, payload):
        event = {"schema_version": 1, "sequence": len(events) + 1,
                 "previous_sha256": events[-1]["event_sha256"] if events else None,
                 "event_id": payload.pop("event_id", None) or "review_" + uuid.uuid4().hex,
                 "recorded_at": timestamp()["observed_at"], **payload}
        event["event_sha256"] = digest(event)
        immutable_json(self.directory / "events" / f'{event["sequence"]:09d}.json', event)
        events.append(event)
        return event

    @staticmethod
    def _product(key):
        return "prod_" + uuid.uuid5(_NAMESPACE, key).hex

    @staticmethod
    def _candidate_asin(row):
        values = {_asin(row.get(key)) for key in ("asin_from_raw_url", "asin_from_card", "asin_observed",
                  "asin_from_final_url", "canonical_asin_observed")}
        values.update(_asin(proof.get("asin")) for proof in row.get("identity_evidence", [])
                      if isinstance(proof, dict) and proof.get("kind") == "DETAIL_PAGE" and proof.get("verified"))
        values.discard(None)
        return next(iter(values)) if len(values) == 1 else None

    @staticmethod
    def _review_revision(row):
        return digest({"evidence": row.get("identity_evidence", []),
                       "conflicts": row.get("identity_conflict_reason", [])})

    def _materialize(self, observations, events):
        """Replay decisions in journal order; later evidence cannot reuse review.

        Entity allocation is persisted in new review events. Older journals use
        the same allocation at their historical event position, before later
        proof attachments, instead of reallocating from today's evidence.
        """
        undone = {e["target_event_id"] for e in events if e["action"] == "undo"}
        with_evidence, rows = {}, {}

        def assign_verified(row, preferred=None):
            if row["identity_status"] != "CONFIRMED":
                return
            asin = row["confirmed_asin"]
            anchors = {existing["product_id"] for key, existing in rows.items()
                       if key != row["observation_id"] and existing.get("product_id")
                       and existing["identity_status"] == "CONFIRMED" and self._candidate_asin(existing) == asin}
            if preferred and row.get("identity_separation_event"):
                row["product_id"] = preferred
            elif len(anchors) > 1:
                row.update(product_id=None, identity_status="CONFLICT",
                           identity_conflict_reason=["ASIN_MATCHES_DIFFERENT_CONFIRMED_ENTITIES"])
            else:
                row["product_id"] = preferred or next(iter(anchors), None) or self._product("verified-amazon-child:" + asin)

        for event in events:
            action = event["action"]
            if action == "undo" or event["event_id"] in undone:
                continue
            if action == "INGEST":
                for item in event["observations"]:
                    key = item["observation_id"]
                    with_evidence[key] = deepcopy(observations[key])
                    row = self.resolver.resolve(with_evidence[key])
                    assign_verified(row)
                    rows[key] = row
                continue
            if action == "EVIDENCE":
                for key in event["observation_ids"]:
                    previous = rows[key]
                    proofs = with_evidence[key].setdefault("identity_evidence", [])
                    # Reattaching the identical proof adds no contradictory fact.
                    known_proofs = {digest(proof) for proof in proofs}
                    additions = [deepcopy(proof) for proof in event["identity_evidence"] if digest(proof) not in known_proofs]
                    proofs.extend(additions)
                    row = self.resolver.resolve(with_evidence[key])
                    for field in ("review_events", "original_identity_conflict_reason", "variation_family_id", "identity_separation_event"):
                        if field in previous:
                            row[field] = deepcopy(previous[field])
                    if previous.get("product_id") and previous["identity_status"] == "CONFIRMED":
                        if row["identity_status"] == "CONFLICT" and additions:
                            row.update(identity_previous_product_id=previous["product_id"], review_status="STALE_REVIEW")
                        elif row["identity_status"] != "CONFLICT":
                            row["review_status"] = previous["review_status"]
                            assign_verified(row, preferred=previous["product_id"])
                        else:
                            row.update(product_id=previous["product_id"], identity_status="CONFIRMED",
                                       identity_conflict_reason=[], review_status=previous["review_status"])
                    else:
                        if previous.get("identity_previous_product_id"):
                            row["identity_previous_product_id"] = previous["identity_previous_product_id"]
                        assign_verified(row)
                    rows[key] = row
                continue
            selected = [rows[key] for key in event["observation_ids"]]
            revisions = event.get("identity_revisions", {})
            stale = [row for row in selected if row["observation_id"] in revisions and
                     revisions[row["observation_id"]] != self._review_revision(row)]
            if stale:
                for row in stale:
                    previous_pid = row.get("product_id")
                    row.update(product_id=None, identity_status="CONFLICT", review_status="STALE_REVIEW",
                               identity_conflict_reason=sorted(set(row["identity_conflict_reason"] + ["REVIEW_EVIDENCE_CHANGED"])))
                    if previous_pid:
                        row["identity_previous_product_id"] = previous_pid
                continue
            if action == "same":
                prior_pids = {row["product_id"] for row in selected if row["product_id"]}
                pid = event.get("product_id") or (min(prior_pids) if prior_pids else self._product("manual-anchor:" + min(event["observation_ids"])))
                # Merge entire currently confirmed entities, not just the two
                # chosen observation rows. Replaying without this event undoes it.
                affected = [row for row in rows.values() if row in selected or row.get("product_id") in prior_pids]
                for row in affected:
                    row["original_identity_conflict_reason"] = row["identity_conflict_reason"]
                    row.update(product_id=pid, identity_status="CONFIRMED", identity_conflict_reason=[], review_status="CONFIRMED_SAME")
                    row.pop("identity_separation_event", None)
            elif action == "different":
                for row in selected:
                    row["original_identity_conflict_reason"] = row["identity_conflict_reason"]
                    pid = event.get("product_ids", {}).get(row["observation_id"]) or self._product("manual-separated:" + row["observation_id"])
                    row.update(product_id=pid, identity_status="CONFIRMED", identity_conflict_reason=[],
                               review_status="CONFIRMED_DIFFERENT", identity_separation_event=event["event_id"])
            elif action == "pending":
                for row in selected:
                    row.update(product_id=None, identity_status="UNCONFIRMED", review_status="PENDING")
            elif action == "family":
                family_id = "family_" + uuid.uuid5(_NAMESPACE, "|".join(sorted(event["observation_ids"]))).hex
                for row in selected:
                    row.update(variation_family_id=family_id, review_status="CONFIRMED_FAMILY")
            for row in selected:
                row.setdefault("review_events", []).append(event["event_id"])
        return [rows[key] for key in sorted(rows)]

    def _write_checkpoint(self, observations, events):
        rows = self._materialize(observations, events)
        atomic_json(self.checkpoint, sealed({"event_count": len(events),
                    "head_sha256": events[-1]["event_sha256"] if events else None,
                    "observation_count": sum(not row.get("view_only") for row in rows),
                    "identity_view_count": sum(bool(row.get("view_only")) for row in rows),
                    "mapping_sha256": digest(rows)}), backup=True)
        return rows

    def ingest(self, observations: list[dict] | dict) -> list[dict]:
        observations = [observations] if isinstance(observations, dict) else list(observations)
        with file_lock(self.directory / ".lock"):
            known, events = self._load()
            items = []
            requested = []
            for original in observations:
                row = deepcopy(original)
                key = row.get("observation_id")
                if not key or not isinstance(key, str):
                    raise ValueError("OBSERVATION_ID_REQUIRED")
                timestamp(row.get("observed_at")) if row.get("observed_at") else None
                requested.append(key)
                path = self.directory / "observations" / (digest(key) + ".json")
                immutable_json(path, row)
                if key not in known:
                    items.append({"observation_id": key, "sha256": digest(row)})
                    known[key] = row
            if items:
                self._append(events, {"action": "INGEST", "observations": items})
            mapped = {row["observation_id"]: row for row in self._write_checkpoint(known, events)}
            return [mapped[key] for key in requested]

    def materialize(self, observations: list[dict] | None = None, *, include_views=False) -> list[dict]:
        with file_lock(self.directory / ".lock"):
            known, events = self._load()
            rows = self._write_checkpoint(known, events)
            if observations is None:
                return rows if include_views else [row for row in rows if not row.get("view_only")]
            ids = {row["observation_id"] for row in observations}
            if ids - known.keys():
                raise ValueError("OBSERVATION_NOT_INGESTED")
            return [row for row in rows if row["observation_id"] in ids]

    def materialize_views(self, observations: list[dict], parse_version: str, *, persist=True) -> list[dict]:
        """Project reviewed identity onto immutable parse-version observations.

        Base observation IDs stay fixed. Changed identity-bearing evidence gets
        a distinct internal identity_view_id and never inherits the old review.
        Review/attach/undo accept that ID. Internal views are excluded from the
        original observation counts and exposed separately in summary().
        """
        if not parse_version or not isinstance(parse_version, str):
            raise ValueError("PARSE_VERSION_REQUIRED")
        requested = list(observations)
        with file_lock(self.directory / ".lock") if persist else nullcontext():
            known, events = self._load()
            items = []
            projections = []
            for original in requested:
                row = deepcopy(original)
                base_id = row.get("base_observation_id") or row.get("observation_id")
                if not base_id or not isinstance(base_id, str):
                    raise ValueError("OBSERVATION_ID_REQUIRED")
                fingerprint = identity_fingerprint(row)
                base = known.get(base_id)
                changed = bool(base and identity_fingerprint(base) != fingerprint)
                view_id = "identity_view_" + digest([base_id, fingerprint]) if changed else base_id
                if view_id not in known:
                    stored = {**row, "observation_id": view_id}
                    if changed:
                        stored.update(base_observation_id=base_id, view_only=True,
                                      identity_fingerprint=fingerprint, parse_version=parse_version)
                    path = self.directory / "observations" / (digest(view_id) + ".json")
                    if persist:
                        immutable_json(path, stored)
                    items.append({"observation_id": view_id, "sha256": digest(stored)})
                    known[view_id] = stored
                projections.append((row, base_id, view_id, fingerprint, changed))
            if items and persist:
                self._append(events, {"action": "INGEST", "observations": items})
            mapped = {row["observation_id"]: row for row in
                      (self._write_checkpoint(known, events) if persist else self._materialize(known, events))}
            result = []
            keys = ("product_id", "identity_status", "review_status", "identity_evidence",
                    "identity_conflict_reason", "original_identity_conflict_reason", "confirmed_asin",
                    "variation_family_id", "review_events")
            for row, base_id, view_id, fingerprint, changed in projections:
                identity = mapped[view_id]
                for key in keys:
                    if key in identity:
                        row[key] = deepcopy(identity[key])
                    else:
                        row.pop(key, None)
                row.update(observation_id=base_id, identity_view_id=view_id,
                           identity_fingerprint=fingerprint, identity_view_changed=changed,
                           identity_parse_version=parse_version)
                row.pop("view_only", None)
                result.append(row)
            return result

    def resolve(self, observation: dict | str, *, evidence=None, actor="offline-evidence-verifier", event_id=None, reason=None) -> dict:
        """Resolve hints, or durably attach a saved detail proof after capture.

        Attaching evidence is a journal event, preserving immutable ranking
        observation bytes. Invalid/conflicting proofs are retained and quarantined.
        """
        if isinstance(observation, dict) and evidence is None:
            return self.resolver.resolve(observation)
        key = observation if isinstance(observation, str) else observation["observation_id"]
        with file_lock(self.directory / ".lock"):
            observations, events = self._load()
            if key not in observations:
                raise ValueError("OBSERVATION_NOT_INGESTED")
            if evidence is not None:
                proofs = [evidence] if isinstance(evidence, dict) else list(evidence)
                if not proofs or not actor or not str(actor).strip() or any(not isinstance(p, dict) for p in proofs):
                    raise ValueError("IDENTITY_EVIDENCE_AND_ACTOR_REQUIRED")
                payload = {"action": "EVIDENCE", "observation_ids": [key], "actor": actor,
                           "identity_evidence": deepcopy(proofs)}
                if reason is not None:
                    payload["reason"] = reason
                existing = next((e for e in events if event_id and e["event_id"] == event_id), None)
                if existing:
                    if any(existing.get(k) != v for k, v in payload.items()):
                        raise ValueError("REVIEW_IDEMPOTENCY_CONFLICT")
                else:
                    if event_id:
                        payload["event_id"] = event_id
                    self._append(events, payload)
            return next(row for row in self._write_checkpoint(observations, events) if row["observation_id"] == key)

    def attach(self, observation_id: str, evidence: list[dict], *, actor: str, reason: str, event_id=None) -> dict:
        """CLI-friendly post-capture proof attachment; undo accepts event_id."""
        if not reason or not str(reason).strip() or not isinstance(evidence, list):
            raise ValueError("ATTACH_REASON_AND_EVIDENCE_LIST_REQUIRED")
        attachment_id = event_id or "proof_" + uuid.uuid4().hex
        row = self.resolve(observation_id, evidence=evidence, actor=actor, reason=reason, event_id=attachment_id)
        return {"event_id": attachment_id, "observation": row}

    def review(self, action: str, observation_ids, *, actor: str, evidence, event_id=None) -> dict:
        if action not in {"same", "different", "family", "pending"}:
            raise ValueError("INVALID_IDENTITY_REVIEW_ACTION")
        ids = sorted(set(observation_ids))
        if not actor or not str(actor).strip() or not evidence or not ids:
            raise ValueError("REVIEW_ACTOR_EVIDENCE_AND_OBSERVATIONS_REQUIRED")
        if action in {"different", "family"} and len(ids) < 2:
            raise ValueError("REVIEW_REQUIRES_TWO_OBSERVATIONS")
        with file_lock(self.directory / ".lock"):
            observations, events = self._load()
            if set(ids) - observations.keys():
                raise ValueError("OBSERVATION_NOT_INGESTED")
            payload = {"action": action, "observation_ids": ids, "actor": actor, "evidence": deepcopy(evidence)}
            if event_id:
                existing = next((e for e in events if e["event_id"] == event_id), None)
                if existing:
                    if any(existing.get(k) != v for k, v in payload.items()):
                        raise ValueError("REVIEW_IDEMPOTENCY_CONFLICT")
                    return existing
                payload["event_id"] = event_id
            current = {row["observation_id"]: row for row in self._materialize(observations, events)}
            payload["identity_revisions"] = {key: self._review_revision(current[key]) for key in ids}
            if action == "same":
                prior = {current[key].get("product_id") or current[key].get("identity_previous_product_id") for key in ids}
                prior.discard(None)
                if not prior:
                    candidates = {self._candidate_asin(current[key]) for key in ids}
                    candidates.discard(None)
                    aliases = {row["product_id"] for row in current.values() if row.get("product_id")
                               and row["identity_status"] == "CONFIRMED" and self._candidate_asin(row) in candidates}
                    if len(aliases) == 1:
                        prior = aliases
                payload["product_id"] = min(prior) if prior else self._product("manual-anchor:" + min(ids))
            elif action == "different":
                payload["product_ids"] = {key: self._product("manual-separated:" + key) for key in ids}
            result = self._append(events, payload)
            self._write_checkpoint(observations, events)
            return result

    def undo(self, event_id: str, *, actor: str, evidence) -> dict:
        if not actor or not str(actor).strip() or not evidence:
            raise ValueError("REVIEW_ACTOR_AND_EVIDENCE_REQUIRED")
        with file_lock(self.directory / ".lock"):
            observations, events = self._load()
            target = next((e for e in events if e["event_id"] == event_id), None)
            if not target or target["action"] in {"INGEST", "undo"}:
                raise ValueError("REVIEW_EVENT_NOT_UNDOABLE")
            existing = next((e for e in events if e["action"] == "undo" and e["target_event_id"] == event_id), None)
            if existing:
                return existing
            result = self._append(events, {"action": "undo", "target_event_id": event_id, "actor": actor, "evidence": deepcopy(evidence)})
            self._write_checkpoint(observations, events)
            return result

    def history(self) -> list[dict]:
        with file_lock(self.directory / ".lock"):
            return deepcopy(self._load()[1])

    def summary(self) -> dict:
        all_rows = self.materialize(include_views=True)
        rows = [row for row in all_rows if not row.get("view_only")]
        views = [row for row in all_rows if row.get("view_only")]
        confirmed = [r for r in rows if r["identity_status"] == "CONFIRMED" and r["product_id"]]
        candidates = {r.get("asin_from_raw_url") or r.get("asin_from_card") or r["observation_id"] for r in rows}
        return {"schema_version": 1, "observations": len(rows), "unique_candidates": len(candidates),
                "confirmed_observations": len(confirmed), "confirmed_unique_products": len({r["product_id"] for r in confirmed}),
                "identity_conflicts": sum(r["identity_status"] == "CONFLICT" for r in rows),
                "unconfirmed_observations": sum(r["product_id"] is None for r in rows),
                "identity_view_count": len(views),
                "confirmed_identity_views": sum(r["identity_status"] == "CONFIRMED" and bool(r["product_id"]) for r in views),
                "identity_view_conflicts": sum(r["identity_status"] == "CONFLICT" for r in views),
                "duplicate_confirmed_observations": len(confirmed) - len({r["product_id"] for r in confirmed})}

    def import_legacy(self, records: list[dict], *, evidence_by_asin: dict | None = None) -> list[dict]:
        observations = []
        for record in records:
            asin = _asin(record.get("asin") or record.get("ASIN"))
            if not asin:
                raise ValueError("LEGACY_ASIN_INVALID")
            observations.append({"schema_version": 1, "observation_id": "legacy_" + digest(record),
                "asin_observed": asin, "raw_product_url": record.get("product_url") or f"https://www.amazon.es/dp/{asin}",
                "legacy_record": deepcopy(record), "origin": "HISTORICAL_REPLAY",
                "legacy_identity_status": "HISTORICAL_UNCONFIRMED",
                "identity_evidence": deepcopy((evidence_by_asin or {}).get(asin, []))})
        return self.ingest(observations)

    def legacy_mapping(self) -> dict:
        """Only one-to-one confirmed ASIN/entity mappings may reach old caches."""
        by_asin, by_product = {}, {}
        for row in self.materialize(include_views=True):
            asin = row.get("confirmed_asin")
            if row["identity_status"] != "CONFIRMED" or not asin or not row["product_id"]:
                continue
            by_asin.setdefault(asin, set()).add(row["product_id"])
            by_product.setdefault(row["product_id"], set()).add(asin)
        return {asin: next(iter(pids)) for asin, pids in sorted(by_asin.items())
                if len(pids) == 1 and len(by_product[next(iter(pids))]) == 1}
