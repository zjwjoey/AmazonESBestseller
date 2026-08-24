import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .browser_probe import probe_urls
from .category_discovery import discover_categories
from .config import Settings, load_settings
from .models import AccessState, ProbeEvent, RankingRecord
from .page_inspector import inspect_html, inspect_navigation
from .product_card_parser import build_products, parse_product_cards
from .reports import (
    build_field_availability,
    duplicate_summary,
    write_category_tree,
    write_field_availability_csv,
    write_products_csv,
    write_ranking_csv,
    write_report,
)
from .run_store import RunStore


@dataclass(frozen=True)
class ReconResult:
    visited_page_count: int
    decision: str
    run_dir: Path


def format_tested_pages(events: list[ProbeEvent]) -> str:
    return ", ".join(dict.fromkeys(event.requested_url for event in events))


def _default_settings() -> Settings:
    return Settings(
        root_urls={
            "home": "https://www.amazon.es/",
            "bestsellers": "https://www.amazon.es/gp/bestsellers",
            "kitchen": "https://www.amazon.es/gp/bestsellers/kitchen",
        },
        page_delay_seconds=3,
        max_categories=3,
        max_products_per_category=50,
        max_detail_samples=5,
        headless=False,
    )


def _live_probe(store, targets, delay_seconds, headless=False, start_index=1):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            return probe_urls(page, store, targets, delay_seconds, start_index=start_index)
        finally:
            browser.close()


def _call_probe(probe, store, targets, delay_seconds, start_index=1):
    try:
        return probe(store, targets, delay_seconds, start_index=start_index)
    except TypeError:
        return probe(store, targets, delay_seconds)


def _write_structured_data_report(store: RunStore, inspection) -> None:
    text = "# Structured data report\n\n"
    text += f"- Product card candidates: {inspection.product_card_candidate_count}\n"
    text += f"- Structured data kinds: {', '.join(inspection.structured_data_kinds) or 'none'}\n"
    text += f"- Candidate selector evidence: `{inspection.candidate_selector}`\n"
    text += "\nThis report describes saved HTML only; no private endpoint is called.\n"
    (store.root / "structured_data_report.md").write_text(text, encoding="utf-8")


def _summary_from_records(records: list[RankingRecord]) -> dict:
    duplicate = duplicate_summary(records)
    availability = {row["field"]: row for row in build_field_availability(records)}

    stable_fields = [name for name, row in availability.items() if row["availability_rate"] >= 0.95]
    unstable_fields = [name for name, row in availability.items() if 0 < row["availability_rate"] < 0.95]
    unavailable_fields = [name for name, row in availability.items() if row["availability_rate"] == 0]

    def rate(name: str) -> str:
        row = availability.get(name)
        return f"{row['availability_rate']:.1%}" if row else "0.0%"

    return {
        **duplicate,
        "asin_success_rate": rate("asin"),
        "rank_success_rate": rate("rank"),
        "price_success_rate": rate("price"),
        "rating_success_rate": rate("rating"),
        "review_success_rate": rate("review_count"),
        "monthly_bought_rate": rate("monthly_bought_text"),
        "stable_fields": ", ".join(stable_fields) or "无",
        "unstable_fields": ", ".join(unstable_fields) or "无",
        "unavailable_fields": ", ".join(unavailable_fields) or "无",
        "api_split": "页面保留榜单出现与可见字段；Creators API 后续补 Parent ASIN、品牌、Browse Node ancestry、Offers 与变体。",
    }


def choose_decision(
    root_events: list[ProbeEvent],
    category_events: list[ProbeEvent],
    detail_events: list[ProbeEvent],
    records: list[RankingRecord],
) -> str:
    """Choose a conclusion from every page stage, including optional detail samples."""
    if not root_events or any(event.access_state is not AccessState.NORMAL for event in root_events):
        return "NO-GO"
    if any(
        event.access_state is not AccessState.NORMAL
        for event in [*category_events, *detail_events]
    ):
        return "CONDITIONAL GO"
    if not records:
        return "CONDITIONAL GO"
    asin_rate = next(
        row["availability_rate"]
        for row in build_field_availability(records)
        if row["field"] == "asin"
    )
    return "GO" if asin_rate >= 0.95 and category_events else "CONDITIONAL GO"


def run_reconnaissance(
    base_dir: Path,
    probe=None,
    settings: Settings | None = None,
    run_id: str | None = None,
) -> ReconResult:
    settings = settings or _default_settings()
    run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    store = RunStore.create(base_dir, run_id)
    root_targets = [settings.root_urls[key] for key in ("home", "bestsellers", "kitchen")]
    probe_fn = probe or (
        lambda target_store, targets, delay_seconds, start_index=1: _live_probe(
            target_store, targets, delay_seconds, settings.headless, start_index
        )
    )
    root_events = _call_probe(probe_fn, store, root_targets, settings.page_delay_seconds)
    visited = len(root_events)
    records: list[RankingRecord] = []
    categories = []
    category_events: list[ProbeEvent] = []
    detail_events: list[ProbeEvent] = []
    summary = {
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "tested_pages": ", ".join(root_targets),
        "page_access_result": ", ".join(event.access_state.value for event in root_events),
        "access_restriction": "; ".join(
            f"{event.access_state.value}: {event.reason}" for event in root_events if event.reason
        ) or "未发现",
        "access_stability": "串行、低频、遇限制即停",
    }

    all_root_normal = len(root_events) == len(root_targets) and all(
        event.access_state is AccessState.NORMAL for event in root_events
    )
    kitchen_html_path = store.html_dir / "page_03.html"
    if all_root_normal and kitchen_html_path.exists():
        kitchen_html = kitchen_html_path.read_text(encoding="utf-8")
        inspection = inspect_html(kitchen_html)
        navigation = inspect_navigation(kitchen_html)
        _write_structured_data_report(store, inspection)
        categories = discover_categories(kitchen_html, settings.root_urls["kitchen"])
        categories = categories[: settings.max_categories]
        write_category_tree(categories, store.root / "category_tree.csv", store.root / "category_tree.json")
        category_targets = [node.category_url for node in categories]
        if category_targets:
            category_events = _call_probe(
                probe_fn,
                store,
                category_targets,
                settings.page_delay_seconds,
                start_index=4,
            )
            for index, node in enumerate(categories[: len(category_events)], start=4):
                category_path = store.html_dir / f"page_{index:02d}.html"
                if not category_path.exists():
                    continue
                category_html = category_path.read_text(encoding="utf-8")
                records.extend(
                    parse_product_cards(
                        category_html,
                        node.category_url,
                        {
                            "level2_category_es": node.category_name_es,
                            "browse_node_id": node.browse_node_id,
                        },
                    )[: settings.max_products_per_category]
                )
        if category_events and all(event.access_state is AccessState.NORMAL for event in category_events):
            detail_urls = list(dict.fromkeys(record.product_url for record in records if record.product_url))[: settings.max_detail_samples]
            if detail_urls:
                detail_events = _call_probe(
                    probe_fn,
                    store,
                    detail_urls,
                    settings.page_delay_seconds,
                    start_index=4 + len(category_events),
                )
        summary.update(
            {
                "card_structure": f"{inspection.product_card_candidate_count} 个候选卡片",
                "category_tree": f"{len(categories)} 个二级类目",
                "category_count": len(categories),
                "category_product_counts": ", ".join(
                    f"{node.category_name_es}: {sum(record.level2_category_es == node.category_name_es for record in records)}"
                    for node in categories
                ),
                "max_rank_depth": max((record.rank or 0 for record in records), default=0),
                "detail_fields": f"已保存 {len(detail_events)} 个详情页样本，待离线字段检查",
                "pagination": (
                    "存在 page=2 链接；本阶段未继续请求"
                    if navigation.page_two_url_present
                    else "未发现明确 page=2 链接"
                ),
                "lazy_loading": (
                    "页面包含懒加载/客户端列表标记；本阶段未主动触发"
                    if navigation.lazy_loading_present
                    else "未发现明确懒加载标记"
                ),
            }
        )
    else:
        write_category_tree([], store.root / "category_tree.csv", store.root / "category_tree.json")

    products = build_products(records)
    write_ranking_csv(records, store.root / "ranking_records.csv")
    write_products_csv(products, store.root / "products.csv")
    write_field_availability_csv(records, store.root / "field_availability.csv")
    summary.update(_summary_from_records(records))
    all_events = [*root_events, *category_events, *detail_events]
    summary["tested_pages"] = format_tested_pages(all_events)
    summary["page_access_result"] = ", ".join(event.access_state.value for event in all_events)
    restrictions = [
        f"{event.access_state.value}: {event.reason}"
        for event in all_events
        if event.access_state is not AccessState.NORMAL
    ]
    summary["access_restriction"] = "; ".join(restrictions) or "未发现"
    blocked_detail_count = sum(
        event.access_state is not AccessState.NORMAL for event in detail_events
    )
    summary["detail_fields"] = (
        f"已保存 {len(detail_events)} 个详情页样本；其中 {blocked_detail_count} 个受访问状态限制，"
        "仅将可验证的字段纳入结论"
    )
    decision = choose_decision(root_events, category_events, detail_events, records)
    summary["decision"] = decision
    write_report(store.root, summary)
    return ReconResult(visited_page_count=visited + len(category_events) + len(detail_events), decision=decision, run_dir=store.root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded Amazon.es Best Sellers reconnaissance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    result = run_reconnaissance(Path.cwd(), settings=settings)
    print(json.dumps({"decision": result.decision, "run_dir": str(result.run_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
