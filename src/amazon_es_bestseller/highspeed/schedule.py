"""Optional daily expansion without a fixed collection period or live scheduler."""
from __future__ import annotations
from datetime import date, timedelta
import hashlib
from .budgets import aware_time, madrid_day

def daily_plan(template, *, now=None):
    day = madrid_day(now)
    key = hashlib.sha256(str(template["run_id"]).encode()).hexdigest()[:12]
    identifier = f"daily_{key}_{day}"
    return {**template, "run_id": identifier, "snapshot_id": identifier, "snapshot_date_madrid": day,
            "live_enabled": False, "daily_schedule_enabled": False}

def research_window(*, now=None, days=28):
    if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
        raise ValueError("POSITIVE_RESEARCH_WINDOW_REQUIRED")
    start = date.fromisoformat(madrid_day(now))
    return [(start + timedelta(days=i)).isoformat() for i in range(days)]


def source_order(sources, *, policy="PLAN_ORDER"):
    """Opt-in V3 type round robin; retains exact approved records and order.

    No quota, synthesized URL, source expansion, concurrency or category filter.
    The caller must put this precise sequence into its reviewed plan before live.
    """
    from collections import deque
    from ..multisource.contracts import SOURCE_ROOTS
    rows=list(sources)
    if policy=="PLAN_ORDER":
        return rows
    if policy!="FIVE_TYPE_ROUND_ROBIN_V3":
        raise ValueError("UNKNOWN_SOURCE_ORDER_POLICY")
    queues={kind:deque() for kind in SOURCE_ROOTS}
    for row in rows:
        if row.get("source_type") not in queues:
            raise ValueError("UNKNOWN_SOURCE_TYPE")
        queues[row["source_type"]].append(row)
    result=[]
    while any(queues.values()):
        for queue in queues.values():
            if queue:
                result.append(queue.popleft())
    return result
