"""Build the executable 5,000-SKU plan from a reviewed discovery snapshot.

The mapping file is intentionally human-reviewed.  This script verifies that
every selected URL was actually observed in the current Amazon Bestseller
snapshot; it never guesses a research-category mapping from a title.

Mapping shape (a plain URL list is also accepted as primary-only):

    {"居家生活": {"primary": ["https://www.amazon.es/gp/bestsellers/kitchen/..."],
                   "reserve": ["https://www.amazon.es/gp/bestsellers/home/..."]}}
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def canonical(value: str) -> str:
    parts = urlsplit(str(value or "").strip())
    path = parts.path.rstrip("/") or "/"
    if "/ref=" in path:
        path = path.split("/ref=", 1)[0].rstrip("/") or "/"
    for prefix in ("/-/en", "/-/es", "/-/pt"):
        if path == prefix:
            path = "/"
            break
        if path.startswith(prefix + "/"):
            path = path[len(prefix):] or "/"
            break
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def snapshot_reference(snapshot: str | Path, project_root: str | Path = PROJECT_ROOT) -> str:
    """Return a portable project-root-relative snapshot reference.

    The executable plan is committed to Git and must work after a clone on a
    different drive or worktree.  A snapshot outside the selected project root
    is rejected instead of leaking a machine-specific absolute path into the
    plan.
    """
    snapshot_path = Path(snapshot).expanduser()
    if not snapshot_path.is_absolute():
        snapshot_path = Path.cwd() / snapshot_path
    snapshot_path = snapshot_path.resolve()
    root = Path(project_root).expanduser().resolve()
    try:
        relative = snapshot_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "snapshot 必须位于项目根目录内，不能写入机器绝对路径：%s" % snapshot_path
        ) from exc
    return relative.as_posix()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT),
                        help="项目根目录；source_snapshot 将相对此目录写入")
    args = parser.parse_args()
    template = json.loads(Path(args.template).read_text(encoding="utf-8"))
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
    snapshot_path = Path(args.snapshot)
    pages = snapshot.get("pages") if isinstance(snapshot, dict) else None
    links = snapshot.get("links") if isinstance(snapshot, dict) else None
    if not isinstance(pages, list) or not pages:
        raise SystemExit("snapshot 缺少逐页发现证据")
    for page in pages:
        if not isinstance(page, dict) or not page.get("source_url") or not page.get("http_status"):
            raise SystemExit("snapshot 页面缺少 source_url 或 http_status")
        html_file = str(page.get("html_file") or "").strip()
        if not html_file or not (snapshot_path.parent / html_file).is_file():
            raise SystemExit("snapshot 缺少原始 HTML 证据：%s" % page.get("source_url"))
    if not isinstance(links, list):
        raise SystemExit("snapshot 缺少解析后的 links")
    if int(snapshot.get("page_count", len(pages))) != len(pages):
        raise SystemExit("snapshot page_count 与 pages 不一致")
    if int(snapshot.get("link_count", len(links))) != len(links):
        raise SystemExit("snapshot link_count 与 links 不一致")
    observed = {canonical(row.get("source_url")) for row in snapshot.get("pages", [])}
    observed.update(canonical(row.get("url")) for row in snapshot.get("links", []))
    if not isinstance(mapping, dict):
        raise SystemExit("mapping 必须是 research_category → URL 数组对象")
    seen = set()
    for category in template["categories"]:
        group = category["research_category"]
        mapped = mapping.get(group, [])
        if isinstance(mapped, list):
            primary_urls, reserve_urls = mapped, []
        elif isinstance(mapped, dict):
            primary_urls = mapped.get("primary", [])
            reserve_urls = mapped.get("reserve", [])
        else:
            primary_urls, reserve_urls = [], []
        if (not isinstance(primary_urls, list) or not isinstance(reserve_urls, list)
                or not primary_urls and not reserve_urls):
            raise SystemExit("研究类目 %s 没有审核来源" % group)
        sources = []
        for role, urls in (("primary", primary_urls), ("reserve", reserve_urls)):
            for raw in urls:
                url = canonical(raw)
                if url not in observed:
                    raise SystemExit("来源未出现在当前类目快照：%s" % url)
                if url in seen:
                    raise SystemExit("来源被多个研究类目重复分配：%s" % url)
                seen.add(url)
                sources.append({"source_url": url, "role": role, "status": "pending"})
        category["sources"] = sources
    template["discovery_required"] = False
    template["sources_reviewed"] = True
    try:
        template["source_snapshot"] = snapshot_reference(args.snapshot, args.project_root)
    except ValueError as exc:
        raise SystemExit(str(exc))
    default_pages = int(template.get("pages_per_url", 2) or 2)
    for category in template["categories"]:
        category_pages = int(category.get("pages_per_url", default_pages) or default_pages)
        category["pages_per_url"] = category_pages
        for source in category["sources"]:
            source["pages_per_url"] = category_pages
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已生成审核任务计划：%s；来源页 %d" % (out, len(seen)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
