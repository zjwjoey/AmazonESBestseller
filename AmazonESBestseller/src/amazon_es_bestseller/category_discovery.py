import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class CategoryNode:
    category_name_es: str
    category_url: str
    browse_node_id: str | None
    parent_category: str
    depth: int
    source_page: str


def select_leaf_trial_nodes(
    branch_nodes: dict[str, list[CategoryNode]],
    max_leaf_categories: int,
) -> list[tuple[str, CategoryNode]]:
    """Choose deepest observed candidates round-robin across level-2 branches."""
    remaining = {branch: list(nodes) for branch, nodes in branch_nodes.items() if nodes}
    selected: list[tuple[str, CategoryNode]] = []
    while remaining and len(selected) < max_leaf_categories:
        for branch in list(remaining):
            if len(selected) >= max_leaf_categories:
                break
            selected.append((branch, remaining[branch].pop(0)))
            if not remaining[branch]:
                del remaining[branch]
    return selected


def browse_node_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if query.get("node"):
        return query["node"][0]
    match = re.search(r"/zgbs/[^/]+/(\d+)", parsed.path)
    if match:
        return match.group(1)
    match = re.search(r"/gp/bestsellers/[^/]+/(\d+)(?:/|$)", parsed.path)
    return match.group(1) if match else None


def discover_categories(
    html: str,
    source_page: str,
    *,
    parent_category: str = "Hogar y cocina",
    depth: int = 2,
) -> list[CategoryNode]:
    soup = BeautifulSoup(html, "lxml")
    results: list[CategoryNode] = []
    seen_urls: set[str] = set()
    seen_node_ids: set[str] = set()
    source_path = urlparse(source_page).path.rstrip("/")
    path_parts = source_path.split("/")
    root_path = "/".join(path_parts[:4])
    navigation_anchors = soup.select(
        "#category-nav a[href], [class*='zg-browse'] a[href], [id*='zg-browse'] a[href]"
    )
    if depth > 2 and not navigation_anchors:
        return []
    for anchor in navigation_anchors or soup.find_all("a", href=True):
        absolute = urljoin(source_page, anchor["href"])
        parsed = urlparse(absolute)
        if (
            "/dp/" in parsed.path
            or root_path not in parsed.path
            or parsed.path.rstrip("/") == source_path
            or parsed.fragment
        ):
            continue
        if source_path == root_path:
            suffix = parsed.path.split(root_path, 1)[-1].strip("/")
            numeric_parts = [part for part in suffix.split("/") if part.isdigit()]
            if len(numeric_parts) != 1:
                continue
        elif depth > 2:
            source_node_id = browse_node_id_from_url(source_page)
            candidate_node_id = browse_node_id_from_url(absolute)
            if not candidate_node_id or candidate_node_id == source_node_id:
                continue
        name = " ".join(anchor.get_text(" ", strip=True).split())
        browse_node_id = browse_node_id_from_url(absolute)
        if (
            not name
            or absolute in seen_urls
            or (browse_node_id is not None and browse_node_id in seen_node_ids)
        ):
            continue
        seen_urls.add(absolute)
        if browse_node_id is not None:
            seen_node_ids.add(browse_node_id)
        results.append(
            CategoryNode(
                category_name_es=name,
                category_url=absolute,
                browse_node_id=browse_node_id,
                parent_category=parent_category,
                depth=depth,
                source_page=source_page,
            )
        )
    return results
