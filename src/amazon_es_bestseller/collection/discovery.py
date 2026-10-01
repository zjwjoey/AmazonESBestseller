# -*- coding: utf-8 -*-
"""Amazon.es Bestseller category discovery.

Discovery is intentionally a separate stage from collection.  It records the
current Amazon navigation evidence and never invents a category name or Node
ID.  A reviewed task plan can then reference the saved URLs without coupling
the collector to a stale hard-coded tree.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import json
import os
import re
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..access.detector import detect_access_status, require_normal_access

_NODE_RE = re.compile(r"/gp/bestsellers/[^/]+/(\d+)(?:/|$)")
_ROOT_RE = re.compile(r"/gp/bestsellers/[^/]+/?(?:\?.*)?$")
_SKIP_NAMES = {"best sellers", "los más vendidos", "los mas vendidos",
               "any department", "cualquier departamento"}


def _node_id(url: str) -> str | None:
    match = _NODE_RE.search(urlsplit(url).path)
    return match.group(1) if match else None


def _canonical_url(href: str, base_url: str) -> str:
    absolute = urljoin(base_url, href)
    parts = urlsplit(absolute)
    if parts.netloc.lower() not in {"amazon.es", "www.amazon.es"}:
        return ""
    path = parts.path.rstrip("/") or "/"
    return "https://www.amazon.es" + path


def parse_bestseller_navigation(html: str, source_url: str,
                                discovered_at: str | None = None) -> dict:
    """Parse one saved Bestseller page into a traceable navigation snapshot."""
    soup = BeautifulSoup(html, "lxml")
    title = ""
    for selector in ("h1", "title"):
        node = soup.select_one(selector)
        if node:
            title = node.get_text(" ", strip=True)
            if title:
                break
    links = []
    seen: set[str] = set()
    for anchor in soup.select('a[href*="/gp/bestsellers/"]'):
        url = _canonical_url(anchor.get("href", ""), source_url)
        name = anchor.get_text(" ", strip=True)
        if not url or not name or name.casefold() in _SKIP_NAMES or url in seen:
            continue
        seen.add(url)
        links.append({
            "name": name,
            "url": url,
            "browse_node_id": _node_id(url),
            "parent_url": source_url,
        })
    return {
        "source_url": source_url,
        "title": title,
        "browse_node_id": _node_id(source_url),
        "is_top_level": bool(_ROOT_RE.search(urlsplit(source_url).path.rstrip("/") + "/")),
        "discovered_at": discovered_at or datetime.now().isoformat(timespec="seconds"),
        "links": links,
    }


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def discover_bestseller_tree(urls: Iterable[str], session, out_dir: str,
                             max_depth: int = 1, max_pages: int = 200) -> dict:
    """Fetch bounded Bestseller navigation pages serially and save evidence.

    ``max_depth=1`` reads each supplied root and its direct navigation links;
    callers should review the resulting snapshot before creating a collection
    plan.  Access restrictions stop the discovery stage immediately.
    """
    root_urls = [str(url).strip() for url in urls if str(url).strip()]
    if not root_urls:
        raise ValueError("至少需要一个 Bestseller 根 URL")
    output = Path(out_dir)
    html_dir = output / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    queue = deque((url, 0, "") for url in root_urls)
    queued = set(root_urls)
    pages: list[dict] = []
    while queue and len(pages) < max(1, int(max_pages)):
        url, depth, parent = queue.popleft()
        status = session.goto(url)
        session.wait_between_requests()
        html = session.page.content()
        file_name = "page_%04d.html" % len(pages)
        (html_dir / file_name).write_text(html, encoding="utf-8")
        state = detect_access_status(status, html)
        require_normal_access(state, "类目发现 HTTP %s，URL %s" % (status, url))
        snapshot = parse_bestseller_navigation(html, url)
        snapshot.update({"depth": depth, "parent_url": parent,
                         "http_status": status, "html_file": str(Path("html") / file_name)})
        pages.append(snapshot)
        if depth < max(0, int(max_depth)):
            for link in snapshot["links"]:
                child = link["url"]
                if child not in queued:
                    queued.add(child)
                    queue.append((child, depth + 1, url))
    links = []
    seen = set()
    for page in pages:
        for link in page.get("links", []):
            if link["url"] not in seen:
                seen.add(link["url"])
                links.append(link)
    result = {
        "schema_version": 1,
        "discovered_at": datetime.now().isoformat(timespec="seconds"),
        "root_urls": root_urls,
        "max_depth": int(max_depth),
        "pages": pages,
        "links": links,
        "page_count": len(pages),
        "link_count": len(links),
    }
    _write_json_atomic(output / "category_tree_snapshot.json", result)
    return result
