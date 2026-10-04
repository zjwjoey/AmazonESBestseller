"""Offline planning and immutable ranking snapshot helpers.

The public names are loaded lazily so the ranking parser can reuse the pure
identity adapters without importing the snapshot module back into itself.
"""

__all__ = [
    "DetailAction", "DetailRefreshPolicy", "build_detail_plan", "write_detail_plan",
    "RANKING_SCHEMA_VERSION", "SNAPSHOT_SCHEMA_VERSION",
    "build_ranking_snapshot", "collect_ranking_snapshot",
]


def __getattr__(name):
    if name in {"DetailAction", "DetailRefreshPolicy", "build_detail_plan", "write_detail_plan"}:
        from . import detail_planner
        return getattr(detail_planner, name)
    if name in {"RANKING_SCHEMA_VERSION", "SNAPSHOT_SCHEMA_VERSION",
                "build_ranking_snapshot", "collect_ranking_snapshot"}:
        from . import snapshot
        return getattr(snapshot, name)
    raise AttributeError(name)
