import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .browser_probe import probe_urls
from .category_discovery import (
    browse_node_id_from_url,
    discover_categories,
    select_leaf_trial_nodes,
)
from .config import Settings, load_settings
from .models import AccessState, ProbeEvent, RankingRecord
from .detail_parser import ProductDetail, parse_detail_page
from .page_inspector import inspect_detail_fields, inspect_html, inspect_navigation
from .product_card_parser import build_products, parse_product_cards
from .reports import (
    build_field_availability,
    duplicate_summary,
    write_category_tree,
    write_field_availability_csv,
    write_detail_field_availability,
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


def select_trial_categories(categories, max_categories: int):
    """Keep the complete discovery result separate from the bounded trial set."""
    return categories, categories[:max_categories]


def select_detail_records(
    records: list[RankingRecord], max_detail_samples: int
) -> list[RankingRecord]:
    """Select one detail candidate per observed leaf before taking a second one."""
    queues: dict[str, list[RankingRecord]] = {}
    seen_asins: set[str] = set()
    for record in records:
        if not record.asin or not record.product_url or record.asin in seen_asins:
            continue
        seen_asins.add(record.asin)
        queues.setdefault(record.leaf_category or "未分类", []).append(record)
    selected: list[RankingRecord] = []
    while queues and len(selected) < max_detail_samples:
        for leaf in list(queues):
            if len(selected) >= max_detail_samples:
                break
            selected.append(queues[leaf].pop(0))
            if not queues[leaf]:
                del queues[leaf]
    return selected


def parse_discovery_page(
    html: str, node, root_category_es: str = "Hogar y cocina"
) -> tuple[list, list[RankingRecord]]:
    """Keep a parent page for discovery only when a deeper visible node exists."""
    children = discover_categories(
        html, node.category_url, parent_category=node.category_name_es, depth=3
    )
    if children:
        return children, []
    records = parse_product_cards(
        html,
        node.category_url,
        {
            "root_category_es": root_category_es,
            "level2_category_es": node.category_name_es,
            "category_l1": root_category_es,
            "category_l2": node.category_name_es,
            "leaf_category": node.category_name_es,
            "browse_node_id": node.browse_node_id,
            "ranking_source_url": node.category_url,
        },
    )
    return children, records


def parse_root_sample(html: str, source_url: str, max_products: int = 20) -> list[RankingRecord]:
    return parse_product_cards(
        html,
        source_url,
        {"root_category_es": "Hogar y cocina"},
    )[:max_products]


def _default_settings() -> Settings:
    return Settings(
        root_urls={
            "home": "https://www.amazon.es/",
            "bestsellers": "https://www.amazon.es/gp/bestsellers",
            "kitchen": "https://www.amazon.es/gp/bestsellers/kitchen",
            "diy": "https://www.amazon.es/gp/bestsellers/tools",
        },
        page_delay_seconds=3,
        max_categories=3,
        max_products_per_category=50,
        max_detail_samples=5,
        headless=False,
        max_breadth_discovery_pages=5,
        max_breadth_leaf_categories=5,
        max_breadth_detail_samples=5,
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


def _live_probe_parallel(store, targets, delay_seconds, start_index=1, workers=2):
    from .async_browser_probe import probe_urls_parallel

    return asyncio.run(
        probe_urls_parallel(store, targets, delay_seconds, workers=workers, start_index=start_index)
    )


def _call_probe(probe, store, targets, delay_seconds, start_index=1, pause_before=False):
    if pause_before and delay_seconds > 0:
        time.sleep(delay_seconds)
    return probe(store, targets, delay_seconds, start_index=start_index)


def _write_structured_data_report(store: RunStore, inspection) -> None:
    text = "# Structured data report\n\n"
    text += f"- Product card candidates: {inspection.product_card_candidate_count}\n"
    text += f"- Structured data kinds: {', '.join(inspection.structured_data_kinds) or 'none'}\n"
    text += f"- Structured data fields: {', '.join(inspection.structured_data_fields) or 'none'}\n"
    text += f"- Candidate selector evidence: `{inspection.candidate_selector}`\n"
    text += "\nThis report describes saved HTML only; no private endpoint is called.\n"
    (store.root / "structured_data_report.md").write_text(text, encoding="utf-8")


def _write_detail_field_report(
    store: RunStore,
    detail_events: list[ProbeEvent],
    start_index: int,
) -> str:
    field_maps = []
    for index, event in enumerate(detail_events, start=start_index):
        if event.access_state is not AccessState.NORMAL:
            continue
        path = store.html_dir / f"page_{index:02d}.html"
        if path.exists():
            field_maps.append(inspect_detail_fields(path.read_text(encoding="utf-8")))
    if not field_maps:
        return "未保存可分析的详情页样本"
    write_detail_field_availability(field_maps, store.root / "detail_field_availability.csv")
    present = sorted(field for field in field_maps[0] if field_maps[0][field])
    return f"已离线分析 {len(field_maps)} 个详情页样本；首个样本可观察字段：{', '.join(present) or '无'}"


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
    if len(root_events) < 3 or any(
        event.access_state is not AccessState.NORMAL for event in root_events
    ):
        return "NO-GO"
    if any(
        event.access_state is not AccessState.NORMAL
        for event in [*category_events, *detail_events]
    ):
        return "CONDITIONAL GO"
    if not records:
        return "CONDITIONAL GO"
    normal_category_nodes = {
        browse_node_id_from_url(event.requested_url)
        for event in category_events
        if event.access_state is AccessState.NORMAL
    }
    if None in normal_category_nodes:
        return "CONDITIONAL GO"
    record_category_nodes = {
        browse_node_id_from_url(record.source_url)
        for record in records
        if record.asin is not None and record.rank is not None and record.source_url is not None
    }
    if len(normal_category_nodes) < 3 or not normal_category_nodes <= record_category_nodes:
        return "CONDITIONAL GO"
    availability = {
        row["field"]: row["availability_rate"] for row in build_field_availability(records)
    }
    return (
        "GO"
        if availability.get("asin", 0) >= 0.95 and availability.get("rank", 0) >= 0.95
        else "CONDITIONAL GO"
    )


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
        discovered_categories = discover_categories(kitchen_html, settings.root_urls["kitchen"])
        discovered_categories, categories = select_trial_categories(
            discovered_categories,
            settings.max_categories,
        )
        all_categories = list(discovered_categories)
        level3_categories = []
        root_records = parse_root_sample(
            kitchen_html,
            settings.root_urls["kitchen"],
            max_products=min(20, settings.max_products_per_category),
        )
        records.extend(root_records)
        store.log_info(
            "page=page_03 page_type=root category=Hogar y cocina cards=%s asins=%s",
            len(root_records),
            sum(record.asin is not None for record in root_records),
        )
        category_targets = [node.category_url for node in categories]
        if category_targets:
            category_events = _call_probe(
                probe_fn,
                store,
                category_targets,
                settings.page_delay_seconds,
                start_index=4,
                pause_before=True,
            )
            for index, (node, event) in enumerate(
                zip(categories, category_events),
                start=4,
            ):
                if event.access_state is not AccessState.NORMAL:
                    continue
                category_path = store.html_dir / f"page_{index:02d}.html"
                if not category_path.exists():
                    continue
                category_html = category_path.read_text(encoding="utf-8")
                level3_categories.extend(
                    discover_categories(
                        category_html,
                        node.category_url,
                        parent_category=node.category_name_es,
                        depth=3,
                    )
                )
                parsed_records = parse_product_cards(
                    category_html,
                    node.category_url,
                    {
                        "level2_category_es": node.category_name_es,
                        "browse_node_id": node.browse_node_id,
                    },
                )
                parsed_records = parsed_records[: settings.max_products_per_category]
                records.extend(parsed_records)
                store.log_info(
                    "page=page_%02d page_type=category category=%s cards=%s asins=%s",
                    index,
                    node.category_name_es,
                    len(parsed_records),
                    sum(record.asin is not None for record in parsed_records),
                )
        all_categories.extend(level3_categories)
        write_category_tree(
            all_categories,
            store.root / "category_tree.csv",
            store.root / "category_tree.json",
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
                    pause_before=True,
                )
        summary.update(
            {
                "card_structure": f"{inspection.product_card_candidate_count} 个候选卡片",
                "category_tree": (
                    f"{len(discovered_categories)} 个二级类目，另取 {len(categories)} 个进行试跑；"
                    f"从已保存的试跑页面发现 {len(level3_categories)} 个三级节点"
                ),
                "category_count": len(discovered_categories),
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
                "level3_observation": (
                    f"从 {len(category_events)} 个正常二级页离线发现 {len(level3_categories)} 个三级节点"
                    if level3_categories
                    else "已检查正常二级页，未发现可验证的三级节点"
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
    detail_summary = _write_detail_field_report(
        store,
        detail_events,
        start_index=4 + len(category_events),
    )
    summary["detail_fields"] = (
        f"{detail_summary}；其中 {blocked_detail_count} 个受访问状态限制，"
        "仅将可验证的字段纳入结论"
    )
    decision = choose_decision(root_events, category_events, detail_events, records)
    summary["decision"] = decision
    write_report(store.root, summary)
    result = ReconResult(
        visited_page_count=visited + len(category_events) + len(detail_events),
        decision=decision,
        run_dir=store.root,
    )
    store.close()
    return result


def _normal_html(store: RunStore, page_index: int, event: ProbeEvent) -> str | None:
    if event.access_state is not AccessState.NORMAL:
        return None
    path = store.html_dir / f"page_{page_index:02d}.html"
    return path.read_text(encoding="utf-8") if path.exists() else None


def choose_breadth_decision(
    root_events: list[ProbeEvent],
    category_events: list[ProbeEvent],
    detail_events: list[ProbeEvent],
    records: list[RankingRecord],
) -> str:
    if len(root_events) != 1 or root_events[0].access_state is not AccessState.NORMAL:
        return "NO-GO"
    if any(event.access_state is not AccessState.NORMAL for event in [*category_events, *detail_events]):
        return "CONDITIONAL GO"
    availability = {row["field"]: row["availability_rate"] for row in build_field_availability(records)}
    return "GO" if records and availability.get("asin", 0) >= 0.95 and availability.get("category_rank", 0) >= 0.95 else "CONDITIONAL GO"


def run_breadth_test(
    base_dir: Path,
    probe=None,
    settings: Settings | None = None,
    run_id: str | None = None,
    root_key: str = "kitchen",
    root_category_es: str = "Hogar y cocina",
    max_products: int | None = None,
    max_detail_samples: int | None = None,
    workers: int = 1,
) -> ReconResult:
    """Run the approved, bounded Home/Kitchen breadth test against visible pages only."""
    settings = settings or _default_settings()
    run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    store = RunStore.create(base_dir, run_id)
    probe_fn = probe or (
        (lambda target_store, targets, delay_seconds, start_index=1: _live_probe_parallel(
            target_store, targets, delay_seconds, start_index, workers
        ))
        if workers > 1
        else (lambda target_store, targets, delay_seconds, start_index=1: _live_probe(
            target_store, targets, delay_seconds, settings.headless, start_index
        ))
    )
    root_url = settings.root_urls[root_key]
    root_events = _call_probe(probe_fn, store, [root_url], settings.page_delay_seconds)
    category_events: list[ProbeEvent] = []
    leaf_events: list[ProbeEvent] = []
    detail_events: list[ProbeEvent] = []
    records: list[RankingRecord] = []
    all_nodes = []
    selected_leaf_nodes = []
    details_by_asin: dict[str, ProductDetail] = {}
    root_html = _normal_html(store, 1, root_events[0]) if root_events else None

    if root_html is not None:
        level2_nodes = discover_categories(root_html, root_url)
        discovery_nodes = level2_nodes[: settings.max_breadth_discovery_pages]
        all_nodes.extend(level2_nodes)
        if discovery_nodes:
            category_events = _call_probe(
                probe_fn, store, [node.category_url for node in discovery_nodes],
                settings.page_delay_seconds, start_index=2, pause_before=True,
            )
        branch_nodes = {}
        page_nodes: dict[str, tuple[int, object]] = {}
        for offset, (node, event) in enumerate(zip(discovery_nodes, category_events), start=2):
            html = _normal_html(store, offset, event)
            if html is None:
                break
            page_nodes[node.category_url] = (offset, node)
            children, discovery_records = parse_discovery_page(html, node, root_category_es)
            all_nodes.extend(children)
            branch_nodes[node.category_name_es] = children or [node]
            records.extend(discovery_records[: settings.max_products_per_category])
        selected_leaf_nodes = select_leaf_trial_nodes(
            branch_nodes, settings.max_breadth_leaf_categories
        )
        new_leaf_nodes = [node for _, node in selected_leaf_nodes if node.category_url not in page_nodes]
        if new_leaf_nodes and len(category_events) == len(discovery_nodes):
            leaf_events = _call_probe(
                probe_fn, store, [node.category_url for node in new_leaf_nodes],
                settings.page_delay_seconds, start_index=2 + len(category_events), pause_before=True,
            )
            for offset, (node, event) in enumerate(zip(new_leaf_nodes, leaf_events), start=2 + len(category_events)):
                html = _normal_html(store, offset, event)
                if html is None:
                    break
                branch = next(branch for branch, selected in selected_leaf_nodes if selected.category_url == node.category_url)
                records.extend(parse_product_cards(html, node.category_url, {
                    "root_category_es": root_category_es, "level2_category_es": branch,
                    "level3_category_es": node.category_name_es, "category_l1": root_category_es,
                    "category_l2": branch, "category_l3": node.category_name_es,
                    "leaf_category": node.category_name_es, "browse_node_id": node.browse_node_id,
                    "ranking_source_url": node.category_url,
                })[: settings.max_products_per_category])
        selected_names = {node.category_name_es for _, node in selected_leaf_nodes}
        detail_records = select_detail_records(
            [record for record in records if record.leaf_category in selected_names],
            max_detail_samples if max_detail_samples is not None else settings.max_breadth_detail_samples,
        )
        if max_products is not None:
            selected_asins = {record.asin for record in detail_records[:max_products]}
            records = [record for record in records if record.asin in selected_asins]
            detail_records = detail_records[:max_products]
        if detail_records and all(event.access_state is AccessState.NORMAL for event in [*category_events, *leaf_events]):
            detail_events = _call_probe(
                probe_fn, store, [record.product_url for record in detail_records],
                settings.page_delay_seconds, start_index=2 + len(category_events) + len(leaf_events), pause_before=True,
            )
            for offset, (record, event) in enumerate(zip(detail_records, detail_events), start=2 + len(category_events) + len(leaf_events)):
                html = _normal_html(store, offset, event)
                if html is not None and record.asin:
                    details_by_asin[record.asin] = parse_detail_page(html, record.asin)

    products = build_products(records, details_by_asin)
    write_category_tree(all_nodes, store.root / "category_tree.csv", store.root / "category_tree.json", root_category_es)
    write_ranking_csv(records, store.root / "ranking_records.csv")
    write_products_csv(products, store.root / "products.csv")
    write_field_availability_csv(records, store.root / "field_availability.csv", products)
    all_events = [*root_events, *category_events, *leaf_events, *detail_events]
    decision = choose_breadth_decision(root_events, [*category_events, *leaf_events], detail_events, records)
    summary = {
        **_summary_from_records(records),
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "tested_pages": format_tested_pages(all_events),
        "page_access_result": ", ".join(event.access_state.value for event in all_events),
        "access_restriction": "; ".join(f"{event.access_state.value}: {event.reason}" for event in all_events if event.access_state is not AccessState.NORMAL) or "未发现",
        "access_stability": "串行、至少 3 秒间隔、遇限制即停；未使用代理、重试或绕过手段。",
        "card_structure": "仅从保存的 Best Sellers HTML 离线解析。",
        "category_tree": f"发现 {len(level2_nodes) if root_html else 0} 个二级节点；实际访问 {len(category_events)} 个用于发现更深节点。",
        "category_count": len(selected_leaf_nodes),
        "level3_observation": "；".join(f"{branch} → {node.category_name_es}" for branch, node in selected_leaf_nodes) or "未能确认可访问的最深节点",
        "category_product_counts": ", ".join(f"{name}: {sum(record.leaf_category == name for record in records)}" for name in sorted({record.leaf_category for record in records if record.leaf_category})),
        "max_rank_depth": max((record.category_rank or 0 for record in records), default=0),
        "detail_fields": f"已保存并离线解析 {len(details_by_asin)} 个详情页样本（结构化 JSON 与可读文本均写入 products）。",
        "pagination": "未构造分页请求。",
        "lazy_loading": "未主动触发懒加载。",
        "decision": decision,
        "api_split": "页面记录当前可见字段；后续若正式开发，再评估合规数据源补充字段。",
    }
    write_report(store.root, summary)
    result = ReconResult(len(all_events), decision, store.root)
    store.close()
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded Amazon.es Best Sellers reconnaissance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    breadth_parser = subparsers.add_parser("breadth-run")
    breadth_parser.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    diy_parser = subparsers.add_parser("diy-run")
    diy_parser.add_argument("--config", type=Path, default=Path("config/settings.yaml"))
    diy_parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    if args.command == "breadth-run":
        result = run_breadth_test(Path.cwd(), settings=settings)
    elif args.command == "diy-run":
        result = run_breadth_test(
            Path.cwd(), settings=settings, root_key="diy",
            root_category_es="Bricolaje y herramientas", max_products=50,
            max_detail_samples=50,
            workers=args.workers,
        )
    else:
        result = run_reconnaissance(Path.cwd(), settings=settings)
    print(json.dumps({"decision": result.decision, "run_dir": str(result.run_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
