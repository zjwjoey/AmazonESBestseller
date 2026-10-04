from __future__ import annotations

from dataclasses import dataclass


PRODUCT_BREADCRUMB = "PRODUCT_BREADCRUMB"
BESTSELLER_PLACEMENT = "BESTSELLER_PLACEMENT"
BSR_NODE = "BSR_NODE"
SEARCH_NODE = "SEARCH_NODE"
SOURCE_MISSING = "SOURCE_MISSING"


@dataclass(frozen=True)
class CategoryEvidence:
    product_breadcrumb_category_path: tuple[str, ...] = ()
    ranking_category_placement_id: str | None = None
    ranking_category_path: tuple[str, ...] = ()
    bsr_category_id: str | None = None
    bsr_category_path: tuple[str, ...] = ()
    category_evidence_source: str = SOURCE_MISSING

    def to_dict(self) -> dict:
        return {
            "product_breadcrumb_category_path": list(self.product_breadcrumb_category_path),
            "ranking_category_placement_id": self.ranking_category_placement_id,
            "ranking_category_path": list(self.ranking_category_path),
            "bsr_category_id": self.bsr_category_id,
            "bsr_category_path": list(self.bsr_category_path),
            "category_evidence_source": self.category_evidence_source,
        }


def category_evidence_from_detail(detail: dict, ranking_context: dict | None = None) -> CategoryEvidence:
    breadcrumb = tuple(str(x).strip() for x in (detail.get("detail_category_trail") or []) if str(x).strip())
    ranking_context = ranking_context or {}
    ranking_path = tuple(str(x).strip() for x in (ranking_context.get("ranking_category_path") or []) if str(x).strip())
    bsr_path = tuple(str(x).strip() for x in (
        detail.get("bsr_category_path") or ranking_context.get("bsr_category_path") or []
    ) if str(x).strip())
    search_path = tuple(str(x).strip() for x in (
        detail.get("search_category_path") or ranking_context.get("search_category_path") or []
    ) if str(x).strip())
    source = (PRODUCT_BREADCRUMB if breadcrumb else
              BESTSELLER_PLACEMENT if ranking_path or ranking_context.get("ranking_category_placement_id") else
              BSR_NODE if bsr_path or detail.get("bsr_category_id") else
              SEARCH_NODE if search_path else SOURCE_MISSING)
    return CategoryEvidence(
        product_breadcrumb_category_path=breadcrumb,
        ranking_category_placement_id=ranking_context.get("ranking_category_placement_id"),
        ranking_category_path=ranking_path,
        bsr_category_id=detail.get("bsr_category_id") or ranking_context.get("bsr_category_id"),
        bsr_category_path=bsr_path,
        category_evidence_source=source,
    )
