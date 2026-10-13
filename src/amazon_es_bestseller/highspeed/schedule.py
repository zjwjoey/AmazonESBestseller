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
