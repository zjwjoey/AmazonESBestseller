from __future__ import annotations

from .graph import CategoryPlacementGraph


def validate_tree(graph: CategoryPlacementGraph) -> list[str]:
    errors: list[str] = []
    roots = graph.roots()
    if len(roots) != 1:
        errors.append(f"expected exactly one root placement, found {len(roots)}")
    seen_ids: set[str] = set()
    for placement_id, row in graph.placements.items():
        if placement_id in seen_ids:
            errors.append(f"duplicate placement_id {placement_id}")
        seen_ids.add(placement_id)
        if not row.category_id or not row.marketplace:
            errors.append(f"{placement_id} has malformed marketplace/category_id")
        if row.depth != len(row.category_path) - 1:
            errors.append(f"{placement_id} depth/path mismatch")
        if not row.category_path or row.category_path[-1] != row.category_id:
            errors.append(f"{placement_id} category_path does not end in category_id")
        if len(row.category_path) != len(set(row.category_path)):
            errors.append(f"{placement_id} contains a category cycle")
        if not row.parent_placement_id:
            if row.depth != 0:
                errors.append(f"{placement_id} root depth must be zero")
            continue
        parent = graph.placements.get(row.parent_placement_id)
        if parent is None:
            errors.append(f"{placement_id} missing parent placement {row.parent_placement_id}")
            continue
        if parent.marketplace != row.marketplace:
            errors.append(f"{placement_id} crosses marketplace boundary")
        if row.parent_category_id != parent.category_id:
            errors.append(f"{placement_id} parent_category_id mismatch")
        if row.category_path[:-1] != parent.category_path:
            errors.append(f"{placement_id} category_path is not directly below parent")
        if row.depth != parent.depth + 1:
            errors.append(f"{placement_id} depth is not parent depth + 1")
    return errors
