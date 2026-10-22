"""Coverage-aware rank and appearance analysis, with no sales inference."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from . import SCHEMA_VERSION
from .contracts import ORIGINS, timestamp
from .quality import audit_snapshot, confirmed
from .sources import normalize_source_url


def _date(bundle):
    manifest = bundle.get("manifest", bundle)
    value = manifest.get("local_date")
    instant = manifest.get("observed_at", manifest.get("captured_at"))
    if instant:
        derived = timestamp(instant)["local_date"]
        if value and value != derived:
            raise ValueError("SNAPSHOT_DATE_TIMESTAMP_MISMATCH")
        value = derived
    if not value:
        dates = {timestamp(row["observed_at"])["local_date"] for row in bundle.get("observations", []) if row.get("observed_at")}
        value = next(iter(dates)) if len(dates) == 1 else None
    if value:
        date.fromisoformat(value)
    return value


def _context(row):
    return (row.get("source_id"), row.get("source_type"), row.get("research_category"), row.get("rank_semantics"))


def _selection(bundle, *, source_type=None, research_category=None, source_id=None):
    manifest = dict(bundle.get("manifest", bundle))
    rows = [row for row in bundle.get("observations", []) if
            (source_type is None or row.get("source_type") == source_type) and
            (research_category is None or row.get("research_category") == research_category) and
            (source_id is None or row.get("source_id") == source_id)]
    allowed_sources = {row.get("source_id") for row in rows}
    pages = [page for page in bundle.get("pages", []) if
             (source_type is None or page.get("source_type") == source_type) and
             (source_id is None or page.get("source_id") == source_id) and
             (research_category is None or page.get("research_category") == research_category or page.get("source_id") in allowed_sources)]
    if source_type or research_category or source_id:
        # Planned pages from unrelated sources should not fail a filtered audit.
        planned = manifest.get("planned_pages")
        if isinstance(planned, list):
            selected_ids = {page.get("source_id") for page in pages}
            selected_ids.update(source.get("source_id") for source in manifest.get("sources", [])
                                if (source_type is None or source.get("source_type") == source_type)
                                and (research_category is None or source.get("research_category") == research_category)
                                and (source_id is None or source.get("source_id") == source_id))
            if source_id:
                selected_ids.add(source_id)
            # Keep missing pages belonging to the selected source: filtering
            # cannot erase an incomplete source and make its gate pass.
            manifest["planned_pages"] = [page for page in planned if page.get("source_id") in selected_ids]
        elif isinstance(planned, int):
            if planned == len(bundle.get("pages", [])):
                manifest["planned_pages"] = len(pages)
    quality = audit_snapshot(manifest, pages, rows)
    coverage = tuple(sorted((str(page.get("source_id")), str(page.get("source_type")),
                             str(page.get("research_category") or next((row.get("research_category") for row in rows if row.get("source_id") == page.get("source_id")), "")),
                             str(page.get("browse_node_id") or ""), normalize_source_url(page.get("page_url") or ""),
                             str(page.get("page_number")), str(page.get("expected_rank_start")), str(page.get("expected_rank_end")))
                            for page in pages))
    return {"manifest": manifest, "observations": rows, "pages": pages, "quality": quality,
            "coverage": coverage, "local_date": _date(bundle), "origin": manifest.get("origin", bundle.get("origin"))}


def _not_comparable(reasons, left=None, right=None):
    return {"schema_version": SCHEMA_VERSION, "status": "NOT_COMPARABLE", "reasons": sorted(set(reasons)),
            "left_date": left, "right_date": right, "new_products": None, "exited_products": None,
            "rank_changes": [], "notes": ["Incomplete or different source coverage cannot establish market entry, exit or sales change."]}


class SnapshotComparator:
    def compare(self, left, right, *, source_type=None, research_category=None, source_id=None):
        try:
            before = _selection(left, source_type=source_type, research_category=research_category, source_id=source_id)
            after = _selection(right, source_type=source_type, research_category=research_category, source_id=source_id)
        except (ValueError, TypeError) as exc:
            return _not_comparable([str(exc)])
        reasons = []
        if before["origin"] not in ORIGINS or before["origin"] != after["origin"]:
            reasons.append("ORIGIN_MISMATCH")
        if not before["local_date"] or not after["local_date"] or before["local_date"] >= after["local_date"]:
            reasons.append("INSUFFICIENT_OBSERVATION_TIME")
        if before["coverage"] != after["coverage"] or not before["coverage"]:
            reasons.append("SOURCE_COVERAGE_MISMATCH")
        if not before["quality"]["gate_passed"] or not after["quality"]["gate_passed"]:
            reasons.append("SNAPSHOT_QA_INCOMPLETE")
        if reasons:
            return _not_comparable(reasons, before["local_date"], after["local_date"])
        before_rows = [row for row in before["observations"] if confirmed(row)]
        after_rows = [row for row in after["observations"] if confirmed(row)]
        before_ids = {row["product_id"] for row in before_rows}
        after_ids = {row["product_id"] for row in after_rows}
        # Unknown identities cannot support negative appearance claims.
        identity_complete = len(before_rows) == len(before["observations"]) and len(after_rows) == len(after["observations"])
        rank_map = []
        for rows in (before_rows, after_rows):
            grouped = defaultdict(list)
            for row in rows:
                if isinstance(row.get("rank_value"), int) and not isinstance(row.get("rank_value"), bool) and row["rank_value"] > 0:
                    grouped[(row["product_id"], _context(row))].append(row["rank_value"])
            rank_map.append({key: min(values) for key, values in grouped.items()})
        changes = []
        for product_id, context in sorted(set(rank_map[0]) & set(rank_map[1])):
            prior_rank, current_rank = rank_map[0][(product_id, context)], rank_map[1][(product_id, context)]
            delta = prior_rank - current_rank
            changes.append({"product_id": product_id, "source_id": context[0], "source_type": context[1],
                            "research_category": context[2], "rank_semantics": context[3],
                            "previous_rank": prior_rank, "current_rank": current_rank,
                            "rank_change": delta, "direction": "UP" if delta > 0 else "DOWN" if delta < 0 else "UNCHANGED"})
        source_membership = defaultdict(set)
        for row in after_rows:
            source_membership[row["product_id"]].add(row["source_type"])
        return {"schema_version": SCHEMA_VERSION, "status": "COMPARABLE" if identity_complete else "PARTIALLY_COMPARABLE",
                "origin": before["origin"], "left_date": before["local_date"], "right_date": after["local_date"],
                "scope": {"source_type": source_type, "research_category": research_category, "source_id": source_id},
                "identity_complete": identity_complete,
                "confirmed_left_products": len(before_ids), "confirmed_right_products": len(after_ids),
                "new_products": sorted(after_ids - before_ids) if identity_complete else None,
                "exited_products": sorted(before_ids - after_ids) if identity_complete else None,
                "new_confirmed_candidates": sorted(after_ids - before_ids),
                "shared_products": sorted(before_ids & after_ids), "rank_changes": changes,
                "cross_source_products": {pid: sorted(sources) for pid, sources in source_membership.items() if len(sources) > 1},
                "notes": ["New/exit means appearance in the same complete sampled sources, not market launch or discontinued sales.",
                          "Positive rank_change means a lower visible list position number; it is not sales growth.",
                          "Unconfirmed identities suppress definitive new/exit claims."]}

    def compare_dates(self, snapshots, left_date, right_date, **filters):
        selected, _ = self._daily(snapshots, **filters)
        left = [item for item in selected if _date(item) == left_date]
        right = [item for item in selected if _date(item) == right_date]
        if len(left) != 1 or len(right) != 1:
            return _not_comparable(["DATE_SNAPSHOT_AMBIGUOUS_OR_MISSING"], left_date, right_date)
        return self.compare(left[0], right[0], **filters)

    @staticmethod
    def _daily(snapshots, **filters):
        grouped = defaultdict(list)
        for bundle in snapshots:
            manifest = bundle.get("manifest", bundle)
            try:
                view = _selection(bundle, **filters)
            except (ValueError, TypeError):
                continue
            if not view["coverage"]:
                continue
            key = (view["origin"], view["local_date"], view["coverage"])
            grouped[key].append(bundle)
        selected = []
        for values in grouped.values():
            values.sort(key=lambda item: (str(item.get("manifest", item).get("observed_at", item.get("manifest", item).get("captured_at", ""))),
                                          str(item.get("manifest", item).get("snapshot_id", ""))))
            selected.append(values[-1])
        return selected, sum(len(values) - 1 for values in grouped.values())

    def compare_periods(self, snapshots, left_start, left_end, right_start, right_end, **filters):
        for value in (left_start, left_end, right_start, right_end):
            date.fromisoformat(value)
        if not left_start <= left_end < right_start <= right_end:
            return _not_comparable(["PERIODS_OVERLAP_OR_INVALID"])
        daily, collapsed = self._daily(snapshots, **filters)
        left = [item for item in daily if _date(item) and left_start <= _date(item) <= left_end]
        right = [item for item in daily if _date(item) and right_start <= _date(item) <= right_end]
        if not left or not right:
            return _not_comparable(["PERIOD_SNAPSHOTS_MISSING"])
        views = [_selection(item, **filters) for item in left + right]
        origins = {view["origin"] for view in views}
        coverages = {view["coverage"] for view in views}
        if len(origins) != 1 or len(coverages) != 1 or not all(view["quality"]["gate_passed"] for view in views):
            return _not_comparable(["PERIOD_ORIGIN_OR_COVERAGE_INCOMPLETE"])
        expected_left = (date.fromisoformat(left_end) - date.fromisoformat(left_start)).days + 1
        expected_right = (date.fromisoformat(right_end) - date.fromisoformat(right_start)).days + 1
        if len({_date(item) for item in left}) != expected_left or len({_date(item) for item in right}) != expected_right:
            return _not_comparable(["PERIOD_DATE_GAP"])
        identity_complete = all(confirmed(row) for view in views for row in view["observations"])
        split = len(left)
        sets = [{row["product_id"] for view in selection for row in view["observations"] if confirmed(row)}
                for selection in (views[:split], views[split:])]
        return {"schema_version": SCHEMA_VERSION, "status": "COMPARABLE" if identity_complete else "PARTIALLY_COMPARABLE",
                "origin": next(iter(origins)), "periods": [[left_start, left_end], [right_start, right_end]],
                "collapsed_same_date_snapshots": collapsed, "identity_complete": identity_complete,
                "confirmed_left_products": len(sets[0]), "confirmed_right_products": len(sets[1]),
                "new_products": sorted(sets[1] - sets[0]) if identity_complete else None,
                "exited_products": sorted(sets[0] - sets[1]) if identity_complete else None,
                "shared_products": sorted(sets[0] & sets[1]),
                "metric": "CONFIRMED_ENTITY_PRESENCE_IN_COMPLETE_SAMPLED_PERIODS",
                "notes": ["Daily coverage must be complete and identical throughout both periods; no sales inference."]}

    def history(self, snapshots, **filters):
        daily, collapsed = self._daily(list(snapshots), **filters)
        views = []
        exceptions = []
        for bundle in daily:
            try:
                view = _selection(bundle, **filters)
            except (ValueError, TypeError) as exc:
                exceptions.append({"code": str(exc)})
                continue
            if not view["quality"]["gate_passed"] or not view["local_date"]:
                exceptions.append({"code": "SNAPSHOT_NOT_COMPARABLE", "snapshot_id": view["manifest"].get("snapshot_id")})
                continue
            views.append(view)
        groups = defaultdict(list)
        for view in views:
            groups[view["origin"]].append(view)
        reports = {}
        for origin, selected in sorted(groups.items()):
            sightings = defaultdict(list)
            context_dates = defaultdict(dict)
            for view in sorted(selected, key=lambda item: item["local_date"]):
                contextual = defaultdict(list)
                for page in view["pages"]:
                    contextual[(page.get("source_id"), page.get("source_type"), page.get("research_category"),
                                page.get("rank_semantics"))]  # A proven empty source can establish absence.
                for row in view["observations"]:
                    contextual[_context(row)].append(row)
                    if confirmed(row):
                        sightings[row["product_id"]].append((view["local_date"], row))
                for context, rows in contextual.items():
                    # Unknown entities prevent reliable absence/reappearance claims.
                    context_dates[context][view["local_date"]] = {row["product_id"] for row in rows} if all(confirmed(row) for row in rows) else None
            entities = []
            for product_id, rows in sorted(sightings.items()):
                dates = sorted({day for day, _ in rows})
                contexts = sorted({_context(row) for _, row in rows})
                context_reports = []
                for context in contexts:
                    seen = sorted({day for day, row in rows if _context(row) == context})
                    streak = best_streak = 0
                    previous = None
                    for day in seen:
                        current = date.fromisoformat(day)
                        streak = streak + 1 if previous is not None and current == previous + timedelta(days=1) else 1
                        best_streak = max(best_streak, streak)
                        previous = current
                    timeline = sorted(context_dates[context])
                    reappeared = []
                    ever_seen, absent = False, False
                    previous_timeline = None
                    for day in timeline:
                        if previous_timeline is not None and date.fromisoformat(day) != previous_timeline + timedelta(days=1):
                            absent = False
                        previous_timeline = date.fromisoformat(day)
                        products = context_dates[context][day]
                        if products is None:
                            absent = False
                            continue
                        if product_id in products:
                            if ever_seen and absent:
                                reappeared.append(day)
                            ever_seen, absent = True, False
                        elif ever_seen:
                            absent = True
                    ranks = [row["rank_value"] for _, row in rows if _context(row) == context and isinstance(row.get("rank_value"), int) and row["rank_value"] > 0]
                    daily_ranks = defaultdict(list)
                    for day, row in rows:
                        rank = row.get("rank_value")
                        if _context(row) == context and isinstance(rank, int) and not isinstance(rank, bool) and rank > 0:
                            daily_ranks[day].append(rank)
                    rank_history = [{"local_date": day, "rank_value": min(values)} for day, values in sorted(daily_ranks.items())]
                    rank_change = rank_history[-2]["rank_value"] - rank_history[-1]["rank_value"] if len(rank_history) > 1 else None
                    context_reports.append({"source_id": context[0], "source_type": context[1], "research_category": context[2],
                                            "rank_semantics": context[3], "distinct_dates": len(seen),
                                            "maximum_daily_streak": best_streak,
                                            "latest_daily_streak": streak if timeline and seen[-1] == timeline[-1] else 0,
                                            "historical_best_rank": min(ranks) if ranks else None,
                                            "rank_history": rank_history, "latest_rank_change": rank_change,
                                            "heat_proxy": "VISIBLE_LIST_POSITION_ONLY",
                                            "reappeared_dates": reappeared})
                entities.append({"product_id": product_id, "first_discovered_date": dates[0], "last_discovered_date": dates[-1],
                                 "distinct_dates": len(dates), "observation_count": len({row["observation_id"] for _, row in rows}),
                                 "source_types": sorted({row["source_type"] for _, row in rows}), "contexts": context_reports})
            reports[origin] = {"origin": origin, "observed_dates": sorted({view["local_date"] for view in selected}),
                               "confirmed_unique_products": len(entities), "entities": entities,
                               "real_live_days": len({view["local_date"] for view in selected}) if origin == "LIVE_CAPTURE" else 0}
        return {"schema_version": SCHEMA_VERSION, "status": "ANALYZED" if reports else "NOT_COMPARABLE",
                "collapsed_same_date_snapshots": collapsed, "origins": reports, "exceptions": exceptions,
                "entities": next(iter(reports.values()))["entities"] if len(reports) == 1 else [],
                "notes": ["Origins are analyzed separately. Fixture/replay dates do not count as real live observation days.",
                          "First/last discovered dates refer to the supplied snapshots, not the product's marketplace launch.",
                          "For the same date and identical sampled coverage, the latest snapshot is selected once.",
                          "Streaks require consecutive Europe/Madrid calendar dates in the same source/category context.",
                          "Reappearance requires an intervening complete snapshot with confirmed absence.",
                          "Historical best ranks remain source-specific and never become sales estimates."]}
