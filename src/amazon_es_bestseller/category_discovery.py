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


def _browse_node_id(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if query.get("node"):
        return query["node"][0]
    match = re.search(r"/zgbs/[^/]+/(\d+)", parsed.path)
    return match.group(1) if match else None


def discover_categories(html: str, source_page: str) -> list[CategoryNode]:
    soup = BeautifulSoup(html, "lxml")
    results: list[CategoryNode] = []
    seen: set[str] = set()
    source_path = urlparse(source_page).path.rstrip("/")
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(source_page, anchor["href"])
        parsed = urlparse(absolute)
        if (
            "/dp/" in parsed.path
            or "/gp/bestsellers/kitchen" not in parsed.path
            or parsed.path.rstrip("/") == source_path
            or parsed.fragment
        ):
            continue
        name = " ".join(anchor.get_text(" ", strip=True).split())
        if not name or absolute in seen:
            continue
        seen.add(absolute)
        results.append(
            CategoryNode(
                category_name_es=name,
                category_url=absolute,
                browse_node_id=_browse_node_id(absolute),
                parent_category="Hogar y cocina",
                depth=2,
                source_page=source_page,
            )
        )
    return results
