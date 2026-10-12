"""Opt-in audited fast continuation, unchanged source scope and access policy."""
import asyncio
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("coverage_entry", ROOT / "tools/parallel_link_coverage.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class RecordedOrdinaryLoadFailure(Exception):
    pass


def record_exhausted_page(output, directory, meta):
    """Only exact, restriction-free ERR_FAILED is eligible for omission."""
    from amazon_es_bestseller.access.detector import detect_access_status
    path = output / meta["html_file"]
    if (meta.get("http_status") is not None
        or meta.get("final_url") != "chrome-error://chromewebdata/"
        or meta.get("access_state") != "UNKNOWN"
        or "net::ERR_FAILED" not in meta.get("error", "")
        or entry.file_hash(path) != meta["html_sha256"]
        or detect_access_status(None, path.read_text(encoding="utf-8")).value in entry.RESTRICTED):
        return False
    entry.immutable_json(directory / "page_result.json", {
        "evidence": meta, "records": [], "status": "FAILED_LOAD_AFTER_ONE_RETRY",
        "source_complete": False, "snapshot_authoritative": False,
        "disposition": "No additional request; retain failed evidence; other reviewed sources only",
        "authorization": "User: 提提速，我要半个小时结束"})
    return True


class FastParallelCoverage(entry.ParallelCoverage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entry_hashes[str(Path(__file__).resolve())] = entry.file_hash(Path(__file__))

    async def capture(self, page, url, directory, worker):
        feed = await super().capture(page, url, directory, worker)
        if feed[0] is None and "ranking_snapshots" in directory.relative_to(self.output).parts:
            if record_exhausted_page(self.output, directory, feed[1]):
                print("EXHAUSTED_NETWORK_PAGE_RECORDED: " + url, flush=True)
                raise RecordedOrdinaryLoadFailure(url)
        return feed

    async def snapshot(self, page, category, role, worker):
        while True:
            try:
                return await super().snapshot(page, category, role, worker)
            except RecordedOrdinaryLoadFailure:
                # The durable failure result is reused as an empty failed page,
                # not labelled successful; no source URL or quota is changed.
                continue


def main():
    batch_dir = ROOT / "outputs/amazon_es_fresh377_20261011"
    output = batch_dir / "parallel3_fast"
    from contextlib import ExitStack
    with ExitStack() as stack:
        entry.lock(stack, batch_dir / "migration_origin/process.lock")
        entry.lock(stack, output / "process.lock")
        batch = FastParallelCoverage(batch_dir / "migration_origin", output,
                                     batch_dir / "authorized_fast_plan.json",
                                     resume=(output / "run_metadata.json").exists())
        batch.guard()
        asyncio.run(batch.run(str(ROOT / "outputs/amazon_es_bestseller_5500_20261010_linkfirst/browser_profile")))


if __name__ == "__main__":
    main()
