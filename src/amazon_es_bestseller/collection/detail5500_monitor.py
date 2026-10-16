"""One read-only local monitor. Never starts, retries or clears a collector."""
import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from ..multisource.storage import atomic_json, file_lock, read_sealed


def sample(root, pid):
    root = Path(root)
    progress = json.loads((root / "detail_progress.json").read_text(encoding="utf8"))
    worker = root / "worker_status.json"
    status = json.loads(worker.read_text(encoding="utf8")) if worker.exists() else {"phase": "NOT_STARTED"}
    # Process evidence includes command and creation time, not only existence.
    command = ("Get-CimInstance Win32_Process -Filter 'ProcessId=" + str(int(pid)) +
               "' | Select-Object ProcessId,CreationDate,CommandLine | ConvertTo-Json -Compress")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                            capture_output=True, text=True, timeout=15)
    process = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else None
    receipts = [read_sealed(p) for p in (root / "receipts").glob("*.json")]
    latest = max((r["started_at"] for r in receipts), default=None)
    now = datetime.now(timezone.utc)
    age = (now-datetime.fromisoformat(latest)).total_seconds() if latest else None
    phase = status.get("phase", "UNKNOWN")
    is_collecting = phase.endswith("COLLECTING")
    stalled = bool(is_collecting and (not process or age is not None and age > 600))
    return {"observed_at": now.isoformat(), "run_id": progress["run_id"], "process": process,
            "worker_status": status, "progress": progress, "latest_request_at": latest,
            "request_age_seconds": age, "suspected_stall": stalled,
            "network_healthy_not_inferred_from_PID": True}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True); p.add_argument("--pid", required=True, type=int)
    a = p.parse_args(); root = Path(a.root)
    with file_lock(root / "monitor.lock"):
        while True:
            state = sample(root, a.pid)
            atomic_json(root / "monitor_latest.json", state)
            from .detail5500_hourly import persist_hour
            persist_hour(root, state)
            if state["worker_status"].get("phase") == "COMPLETE" and (root / "detail_run_manifest.json").is_file():
                break
            time.sleep(30)


if __name__ == "__main__":
    main()
