"""External-scheduler daily entry point; no in-process scheduler or live default."""
from __future__ import annotations

from datetime import date, timedelta
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from pathlib import Path
from .contracts import timestamp


def daily_plan(config_path, *, local_date=None, now=None):
    path = Path(config_path).resolve()
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    if config.get("schema_version") != 1 or config.get("enabled") is not True:
        raise ValueError("SCHEDULE_NOT_EXPLICITLY_ENABLED")
    if config.get("timezone") != "Europe/Madrid" or config.get("days") != 14:
        raise ValueError("FOURTEEN_DAY_MADRID_SCHEDULE_REQUIRED")
    start = date.fromisoformat(config["start_date"])
    today = timestamp(now)["local_date"]
    selected = date.fromisoformat(local_date or today)
    if not start <= selected < start + timedelta(days=14):
        raise ValueError("DATE_OUTSIDE_REVIEWED_FOURTEEN_DAY_WINDOW")
    plan_path = (path.parent / config["plan_file"]).resolve()
    from .cli import _plan
    data = _plan(plan_path)
    if data.get("origin") == "LIVE_CAPTURE" and selected.isoformat() != today:
        raise ValueError("LIVE_CAPTURE_CANNOT_BE_BACKDATED")
    data["run_id"] = str(config["schedule_id"]) + "_" + selected.strftime("%Y%m%d")
    data["snapshot_id"] = data["run_id"]
    if data.get("origin") == "OFFLINE_FIXTURE":
        # Explicit fixture simulation only. Historical replay dates are immutable.
        for page in data.get("pages", []):
            instant = datetime.fromisoformat(timestamp(page["observed_at"])["observed_at"].replace("Z", "+00:00"))
            local = instant.astimezone(ZoneInfo("Europe/Madrid"))
            changed = local.replace(year=selected.year, month=selected.month, day=selected.day)
            page["observed_at"] = timestamp(changed)["observed_at"]
    elif data.get("origin") == "HISTORICAL_REPLAY" and local_date:
        dates = {timestamp(page["observed_at"])["local_date"] for page in data.get("pages", [])}
        if dates != {selected.isoformat()}:
            raise ValueError("HISTORICAL_REPLAY_CANNOT_REDATE_CAPTURE")
    return data
