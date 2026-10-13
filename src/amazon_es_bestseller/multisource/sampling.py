"""Confirmed-entity quota feasibility without counting a product twice."""
from __future__ import annotations

from collections import defaultdict, deque
from itertools import combinations
import math

from . import SCHEMA_VERSION
from .contracts import PRODUCTION_CATEGORY_QUOTAS, PRODUCTION_SOURCE_QUOTAS, SOURCE_BUDGETS, RESEARCH_CATEGORIES
from .quality import confirmed, coverage_matrix
from .sources import source_type_from_url, same_source_category


class _Flow:
    """Small integral max-flow: source -> entity -> category -> sink."""
    def __init__(self):
        self.edges = defaultdict(list)

    def add(self, left, right, capacity):
        forward = [right, int(capacity), len(self.edges[right]), int(capacity)]
        reverse = [left, 0, len(self.edges[left]), 0]
        self.edges[left].append(forward)
        self.edges[right].append(reverse)

    def maximum(self, start, end):
        total = 0
        while True:
            levels = {start: 0}
            queue = deque([start])
            while queue:
                node = queue.popleft()
                for target, remaining, _, _ in self.edges[node]:
                    if remaining and target not in levels:
                        levels[target] = levels[node] + 1
                        queue.append(target)
            if end not in levels:
                return total
            positions = defaultdict(int)

            def send(node, available):
                if node == end:
                    return available
                while positions[node] < len(self.edges[node]):
                    edge = self.edges[node][positions[node]]
                    target, remaining, inverse, _ = edge
                    if remaining and levels.get(target) == levels[node] + 1:
                        sent = send(target, min(available, remaining))
                        if sent:
                            edge[1] -= sent
                            self.edges[target][inverse][1] += sent
                            return sent
                    positions[node] += 1
                return 0

            while sent := send(start, 5500):
                total += sent


class SamplingPlanner:
    def replacement_plan(self, *, missing_source_type, missing_research_category, gap,
                         sources, completed_by_source, max_replacement_pages,
                         max_replacement_requests, actor, reason, review_evidence,
                         source_budgets=None, global_completed=None,
                         allow_cross_source=False, excluded_source_ids=()):
        """Plan reviewed replacements; never fetch, invent URLs or mutate quotas.

        Capacity is a reviewed planning ceiling, not proof of page completeness.
        Runtime request reservations and source validation remain mandatory.
        """
        if not actor or not reason or not review_evidence:
            raise ValueError("REPLACEMENT_REVIEW_REQUIRED")
        if not isinstance(allow_cross_source, bool) or set(completed_by_source) - set(SOURCE_BUDGETS):
            raise ValueError("INVALID_REPLACEMENT_REVIEW_SCOPE")
        if missing_source_type not in SOURCE_BUDGETS or missing_research_category not in RESEARCH_CATEGORIES:
            raise ValueError("INVALID_REPLACEMENT_SCOPE")
        numbers = (gap, max_replacement_pages, max_replacement_requests)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in numbers):
            raise ValueError("INVALID_REPLACEMENT_BUDGET")
        budgets = dict(source_budgets if source_budgets is not None else SOURCE_BUDGETS)
        if (set(budgets) != set(SOURCE_BUDGETS) or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in budgets.values())
                or sum(budgets.values()) > 30000):
            raise ValueError("INVALID_SOURCE_BUDGETS")
        completed = {source: completed_by_source.get(source, 0) for source in budgets}
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in completed.values()):
            raise ValueError("INVALID_COMPLETED_COUNTS")
        if any(completed[source] > budgets[source] for source in budgets):
            raise ValueError("BUDGET_BELOW_ALREADY_COMPLETED")
        actual_global = sum(completed.values()) if global_completed is None else global_completed
        if not isinstance(actual_global, int) or isinstance(actual_global, bool) or actual_global < sum(completed.values()) or actual_global > 30000:
            raise ValueError("INVALID_GLOBAL_COMPLETED")
        source_remaining = {source: budgets[source] - completed[source] for source in budgets}
        excluded, candidates, seen, skipped = set(excluded_source_ids), [], set(), []
        for source in sources:
            identifier, kind, category = source.get("source_id"), source.get("source_type"), source.get("research_category")
            urls = source.get("reviewed_page_urls", [])
            if not identifier or identifier in seen or identifier in excluded:
                continue
            seen.add(identifier)
            if (source.get("source_status") != "AVAILABLE" or source.get("enabled") is not True
                    or not source.get("verification_evidence") or category not in RESEARCH_CATEGORIES or kind not in budgets
                    or not urls or not source.get("actual_source_url")
                    or any(source_type_from_url(url) != kind or not same_source_category(url, source["actual_source_url"]) for url in urls)):
                skipped.append({"source_id": identifier, "reason": "SOURCE_NOT_VERIFIED_OR_REVIEWED"})
                continue
            capacity, request_cost = source.get("page_capacity"), source.get("snapshot_request_cost_per_page")
            if (not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0
                    or not isinstance(request_cost, int) or isinstance(request_cost, bool) or request_cost <= 0):
                skipped.append({"source_id": identifier, "reason": "REVIEWED_CAPACITY_MISSING"})
                continue
            tier = 1 if kind == missing_source_type and category == missing_research_category else 2 if kind == missing_source_type else 3
            if tier == 3 and not allow_cross_source:
                skipped.append({"source_id": identifier, "reason": "CROSS_SOURCE_APPROVAL_REQUIRED"})
                continue
            candidates.append((tier, identifier, source, capacity, request_cost))
        remaining = min(gap, 30000 - actual_global)
        pages_left, requests_left, plan = max_replacement_pages, max_replacement_requests, []
        for tier, identifier, source, capacity, request_cost in sorted(candidates, key=lambda item: (item[0], item[1])):
            kind = source["source_type"]
            usable_pages = min(len(set(source["reviewed_page_urls"])), pages_left, requests_left // request_cost)
            quantity = min(remaining, source_remaining[kind], usable_pages * capacity)
            if quantity <= 0:
                continue
            page_count = math.ceil(quantity / capacity)
            page_urls = list(dict.fromkeys(source["reviewed_page_urls"]))[:page_count]
            requests = page_count * request_cost
            plan.append({"source_id": identifier, "source_type": kind,
                         "research_category": source["research_category"], "tier": tier,
                         "page_urls": page_urls, "page_capacity": capacity,
                         "snapshot_request_cost_per_page": request_cost, "page_count": page_count,
                         "request_budget": requests, "replacement_quantity": quantity,
                         "reason": reason, "actor": actor, "review_evidence": review_evidence,
                         "verification_evidence": source["verification_evidence"],
                         "cross_source_approved": allow_cross_source if tier == 3 else False})
            remaining -= quantity
            source_remaining[kind] -= quantity
            pages_left -= page_count
            requests_left -= requests
        planned = sum(item["replacement_quantity"] for item in plan)
        return {"schema_version": SCHEMA_VERSION, "status": "PLANNED" if planned == gap else "REPLACEMENT_SHORTFALL",
                "original_budget": budgets, "actual_completed": completed, "global_completed": actual_global,
                "missing_source_type": missing_source_type, "missing_research_category": missing_research_category,
                "gap": gap, "replacement_sources": plan, "replacement_quantity": planned,
                "remaining_gap": gap - planned, "remaining_source_budgets": source_remaining,
                "remaining_global_budget": 30000 - actual_global - planned,
                "actor": actor, "reason": reason, "review_evidence": review_evidence,
                "cross_source_approved": allow_cross_source, "skipped_sources": skipped,
                "notes": ["Order: same list/category, then same list/other category, then explicitly reviewed cross-list sources.",
                          "Reviewed capacity is a ceiling, not a fabricated expected page count.",
                          "This plan does not authorize live capture or change source/production quotas."]}

    def feasibility(self, observations, coverage=None, prior_product_ids=None):
        rows = list(observations)
        entities = defaultdict(list)
        for row in rows:
            if confirmed(row):
                entities[row["product_id"]].append(row)
        source_sets = {source: set() for source in PRODUCTION_SOURCE_QUOTAS}
        category_sets = {category: set() for category in PRODUCTION_CATEGORY_QUOTAS}
        ambiguities, eligible = {}, {}
        for product_id, sightings in sorted(entities.items()):
            sources = {row.get("source_type") for row in sightings} & source_sets.keys()
            categories = {row.get("research_category") for row in sightings} & category_sets.keys()
            for source in sources:
                source_sets[source].add(product_id)
            for category in categories:
                category_sets[category].add(product_id)
            primaries = {row["primary_research_category"] for row in sightings if row.get("primary_research_category")}
            primary_evidence_missing = any(row.get("primary_research_category") and not row.get("primary_research_category_evidence")
                                           for row in sightings)
            if len(primaries) == 1 and next(iter(primaries)) in categories and not primary_evidence_missing:
                category = next(iter(primaries))
            elif len(categories) == 1 and not primaries:
                category = next(iter(categories))
            else:
                ambiguities[product_id] = {"research_categories": sorted(categories), "primary_categories": sorted(primaries),
                                          "primary_mapping_evidence_missing": primary_evidence_missing}
                continue
            # Use only source/category pairs actually evidenced for this entity.
            applicable_sources = {row["source_type"] for row in sightings
                                  if row.get("research_category") == category and row.get("source_type") in sources}
            if applicable_sources:
                eligible[product_id] = (category, applicable_sources)
        network = _Flow()
        for source, quota in PRODUCTION_SOURCE_QUOTAS.items():
            network.add("START", ("source", source), quota)
        for category, quota in PRODUCTION_CATEGORY_QUOTAS.items():
            network.add(("category", category), "END", quota)
        for product_id, (category, sources) in eligible.items():
            # An entity's single outbound capacity enforces global uniqueness.
            network.add(("product", product_id), ("category", category), 1)
            for source in sorted(sources):
                network.add(("source", source), ("product", product_id), 1)
        allocated_count = network.maximum("START", "END")
        allocations = []
        for source in PRODUCTION_SOURCE_QUOTAS:
            for edge in network.edges[("source", source)]:
                target, remaining, _, initial = edge
                if isinstance(target, tuple) and target[0] == "product" and initial == 1 and remaining == 0:
                    product_id = target[1]
                    allocations.append({"product_id": product_id, "source_type": source,
                                        "research_category": eligible[product_id][0]})
        allocated_sources = defaultdict(int)
        allocated_categories = defaultdict(int)
        for row in allocations:
            allocated_sources[row["source_type"]] += 1
            allocated_categories[row["research_category"]] += 1
        overlaps = [{"source_types": [left, right], "confirmed_unique_products": len(source_sets[left] & source_sets[right])}
                    for left, right in combinations(PRODUCTION_SOURCE_QUOTAS, 2)]
        prior = set(prior_product_ids) if prior_product_ids is not None else None
        historical_existing = len(set(entities) & prior) if prior is not None else None
        historical_new = len(set(entities) - prior) if prior is not None else None
        allocated_ids = {row["product_id"] for row in allocations}
        uncertain_buckets = set()
        for row in rows:
            if not confirmed(row):
                asin = row.get("asin_from_card") or row.get("asin_observed") or row.get("observed_asin") or row.get("asin_from_raw_url")
                url = row.get("raw_product_url") or row.get("product_url_raw")
                uncertain_buckets.add(("ASIN_CLUE", asin) if asin else ("URL_CLUE", url) if url else ("OBSERVATION", row.get("observation_id")))
        matrix = coverage_matrix(coverage)
        return {"schema_version": SCHEMA_VERSION, "target": 5500,
                "status": "FEASIBLE" if allocated_count == 5500 else "QUOTA_UNIQUE_SHORTFALL",
                "confirmed_unique_candidates": len(entities), "eligible_unique_candidates": len(eligible),
                "allocated_unique_products": allocated_count, "global_shortfall": 5500 - allocated_count,
                "insufficient_identity_observations": sum(not confirmed(row) for row in rows),
                "unconfirmed_candidate_buckets": len(uncertain_buckets),
                "excluded_category_ambiguous_products": len(ambiguities), "category_ambiguities": ambiguities,
                "source_quotas": {source: {"target": quota, "confirmed_unique_candidates": len(source_sets[source]),
                    "allocated_unique_products": allocated_sources[source], "shortfall": quota - allocated_sources[source],
                    "unallocated_candidates": len(source_sets[source] - allocated_ids)}
                                  for source, quota in PRODUCTION_SOURCE_QUOTAS.items()},
                "category_quotas": {category: {"target": quota, "confirmed_unique_candidates": len(category_sets[category]),
                    "allocated_unique_products": allocated_categories[category], "shortfall": quota - allocated_categories[category],
                    "unallocated_candidates": len(category_sets[category] - allocated_ids)}
                                    for category, quota in PRODUCTION_CATEGORY_QUOTAS.items()},
                "cross_source_overlaps": overlaps,
                "cross_source_products": sum(sum(product_id in ids for ids in source_sets.values()) > 1 for product_id in entities),
                "historical_existing_products": historical_existing, "historical_first_discovered_products": historical_new,
                "historical_existing_ratio": historical_existing / len(entities) if prior is not None and entities else None,
                "dynamic_reserve_unique_candidates": len(set(eligible) - allocated_ids),
                "allocations": allocations, "source_coverage": matrix,
                "notes": ["Formal quotas count confirmed internal Product IDs only; each allocation occupies one source and one category quota.",
                          "Source/category availability counts overlap and must not be summed as global unique products.",
                          "Ambiguous category mappings are excluded until an evidenced primary category is reviewed.",
                          "A shortfall reports evidence currently available, not proof that Amazon lacks enough products.",
                          "Unconfirmed candidate buckets group raw clues for review and are not unique confirmed products.",
                          "Historic new/existing metrics are unknown unless the prior confirmed product set is supplied."]}
