"""Explicit bounded parallel3 switch; each category worker remains serial."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import ExitStack
import hashlib
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from bounded_link_details import Batch, ParserHold, lock  # noqa: E402
from incremental_link_collection import copy_evidence  # noqa: E402
from amazon_es_bestseller.access.detector import detect_access_status  # noqa: E402
from amazon_es_bestseller.access.location import inspect_delivery_location  # noqa: E402
from amazon_es_bestseller.monitoring.link_first_coverage import CoverageRunner  # noqa: E402
from amazon_es_bestseller.orchestration.checkpoint import read_json, write_json_atomic  # noqa: E402
from amazon_es_bestseller.orchestration.link_first import (  # noqa: E402
    RESTRICTED, canonical_json_sha256, file_hash, immutable_json, page_url, utc,
)
from amazon_es_bestseller.runtime_state import VersionedCheckpointStore  # noqa: E402


def freeze_origin(origin, output, plan):
    origin, output, plan = origin.resolve(), output.resolve(), plan.resolve()
    if origin == output or origin in output.parents or output in origin.parents:
        raise ValueError("INDEPENDENT_PARALLEL_RUN_REQUIRED")
    saved = output / "parallel_inputs.json"
    if saved.exists():
        value = read_json(saved, None)
        if value["origin"] != str(origin) or value["plan"] != str(plan):
            raise ValueError("ORIGIN_INPUT_DRIFT")
        for path, expected in value["input_hashes"].items():
            if file_hash(Path(path)) != expected:
                raise ValueError("ORIGIN_INPUT_DRIFT")
        return value
    state = VersionedCheckpointStore(origin / "checkpoints", "link_first_state").load()
    if state["status"] not in {"COOLDOWN", "PREPARED", "SAMPLE_COMPLETE"} or state.get("hold_token"):
        raise ValueError("PARALLEL_SWITCH_REQUIRES_SAFE_PAUSE")
    old_plan = read_json(origin / "reviewed_plan_copy.json", {})
    new_plan = read_json(plan, {})
    for field in ("target_unique", "categories", "source_snapshot", "rank_start", "rank_end", "pages_per_url"):
        if old_plan.get(field) != new_plan.get(field):
            raise ValueError("PARALLEL_SWITCH_SCOPE_DRIFT")
    paths = [origin / "run_metadata.json", origin / "reviewed_plan_copy.json",
             origin / "checkpoints/link_first_state.latest.json", plan]
    meta = read_json(origin / "run_metadata.json", {})
    paths.extend(Path(p) for p in meta.get("excluded_current_files", {}))
    copied = {}
    for directory in ("ranking_snapshots", "detail_evidence"):
        for path in sorted((origin / directory).rglob("*")):
            if path.is_file():
                copy_evidence(origin, output, str(path.relative_to(origin)), copied)
    value = {"contract": "parallel3-fresh-coverage-switch-v1", "origin": str(origin),
             "plan": str(plan), "created_at": utc(), "origin_state": state,
             "completed_segments": state["completed_segments"], "detail_results": state["detail_results"],
             "copied_file_hashes": copied, "input_hashes": {str(p): file_hash(p) for p in paths},
             "exclude_current_files": list(meta.get("excluded_current_files", {})),
             "translation": "disabled", "authorization": "User: 三路并行"}
    immutable_json(saved, value)
    return value


class FeedParser(CoverageRunner):
    def __init__(self, *args, switch_hash, entry_hashes, **kwargs):
        self.switch_hash, self.entry_hashes = switch_hash, entry_hashes
        self.feeds = {}
        super().__init__(*args, **kwargs)

    def runtime_metadata(self):
        return {**super().runtime_metadata(), "scheduler": "parallel3", "max_concurrent_requests": 3,
                "per_worker_serial": True, "shared_access_stop": True,
                "parallel_inputs_sha256": self.switch_hash, "parallel_entry_hashes": self.entry_hashes}

    def _capture(self, session, url, destination, **kwargs):
        self.guards()
        value = self.feeds[str(destination)]
        if value[1]["requested_url"] != url:
            raise ValueError("CAPTURE_FEED_BINDING_MISMATCH")
        if file_hash(self.output / value[1]["html_file"]) != value[1]["html_sha256"]:
            raise ValueError("CAPTURE_FEED_HTML_DRIFT")
        return value

    def _manual_hold(self, status, error):
        raise ParserHold(status + ": " + error)


class ParallelCoverage(Batch):
    def __init__(self, origin, output, plan, *, resume=False):
        self.origin, self.output = origin.resolve(), output.resolve()
        self.inputs = freeze_origin(self.origin, self.output, plan)
        self.inputs_hash = file_hash(self.output / "parallel_inputs.json")
        self.entry_hashes = {str(p): file_hash(p) for p in (
            Path(__file__), ROOT / "tools/bounded_link_details.py", ROOT / "tools/incremental_link_collection.py")}
        self.runner = FeedParser(plan, output, ROOT, sample_size=50, continue_after_sample=True,
                                 resume=resume, exclude_current_details=self.inputs["exclude_current_files"],
                                 switch_hash=self.inputs_hash, entry_hashes=self.entry_hashes)
        if not resume:
            for key in ("completed_segments", "detail_results"):
                self.runner.state[key].update(self.inputs[key])
            self.runner.links, self.runner.records, self.runner.selected = [], {}, {}
            self.runner._recover()
            self.runner.state.update(phase=self.inputs["origin_state"]["phase"], error="", hold_token="")
        self.worker_states = {int(k): v for k, v in self.runner.state.get("parallel_workers", {}).items()}
        self.worker_states.setdefault(1, {"status": "COOLDOWN",
                                          "cooldown_until": self.inputs["origin_state"].get("cooldown_until", 0)})
        for n in (2, 3):
            self.worker_states.setdefault(n, {"status": "READY", "cooldown_until": 0})
        self.now = time.time
        self.ready = asyncio.Event()
        self.ready.set()
        self.holds, self.token = {}, ""
        self.dispatch_lock = asyncio.Lock()
        self.categories = {c["research_category"]: c for c in self.runner.plan["categories"]}
        self.allowed_links = {r["product_link"] for r in self.runner.links}
        for cat in self.categories.values():
            for source in cat["sources"]:
                for page in range(1, source.get("pages_per_url", cat["pages_per_url"]) + 1):
                    self.allowed_links.add(page_url(source["source_url"], page))
        self._recover_orphans()
        self.progress("PREPARED")

    def _recover_orphans(self):
        links = {r["snapshot_item_id"]: r for r in self.runner.links}
        changed = False
        for path in sorted((self.output / "detail_evidence").glob("*/result.json")):
            item = path.parent.name
            if item in self.runner.state["detail_results"]:
                continue
            value = read_json(path, {})
            if item not in links or value.get("snapshot_item_id") != item or value.get("requested_url") != links[item]["product_link"]:
                raise ValueError("UNBOUND_ORPHAN_RESULT")
            if value.get("html_file") and file_hash(self.output / value["html_file"]) != value["html_sha256"]:
                raise ValueError("ORPHAN_HTML_DRIFT")
            self.runner.state["detail_results"][item] = {
                "result_file": str(path.relative_to(self.output)), "sha256": file_hash(path), "status": value["status"]}
            changed = True
        if changed:
            self.runner.links, self.runner.records, self.runner.selected = [], {}, {}
            self.runner._recover()

    def guard(self):
        self.runner.guards()
        if file_hash(self.output / "parallel_inputs.json") != self.inputs_hash:
            raise ValueError("PARALLEL_INPUT_DRIFT")
        for path, expected in {**self.entry_hashes, **self.inputs["input_hashes"]}.items():
            if file_hash(Path(path)) != expected:
                raise ValueError("PARALLEL_CODE_OR_ORIGIN_DRIFT")

    def progress(self, status=None):
        self.runner.state["parallel_workers"] = self.worker_states
        self.runner.save(status or self.runner.state["status"])
        write_json_atomic(self.output / "parallel_progress.json", {
            "status": self.runner.state["status"], "phase": self.runner.state["phase"], "updated_at": utc(),
            "scheduler": "parallel3", "workers": self.worker_states, "target_unique": self.runner.plan["target_unique"],
            "attempted_links": len(self.runner.state["detail_results"]),
            "selected_unique": len(self.runner.selected), "confirmed_unique": len(self.runner.records),
            "snapshot_links": len(self.runner.links), "statuses": dict(Counter(
                r["status"] for r in self.runner.state["detail_results"].values())),
            "hold_token": self.token, "holds": {k: v["reason"] for k, v in self.holds.items()},
            "previous_details_reuse": False, "translation": "disabled", "formal_release": False})

    async def wait_cooldown(self, number):
        if self.now() < self.worker_states[number].get("cooldown_until", 0):
            self.worker_states[number]["status"] = "COOLDOWN"
            self.progress()
        while self.now() < self.worker_states[number].get("cooldown_until", 0):
            await self.gate()
            await asyncio.sleep(min(1, self.worker_states[number]["cooldown_until"] - self.now()))
        self.worker_states[number]["status"] = "READY"

    async def normal_html(self, page, worker):
        while True:
            await self.gate()
            html = await page.content()
            state = detect_access_status(200, html).value
            if state in RESTRICTED:
                self.block(worker, page, state + " during ranking render")
                continue
            if inspect_delivery_location(html).is_spain is not True:
                self.block(worker, page, "Spain destination changed", "DELIVERY_LOCATION_HOLD")
                continue
            return html

    async def ranking_capture(self, page, url, directory, worker):
        html, meta = await self.capture(page, url, directory, worker)
        if html is None:
            self.block(worker, page, "Ranking failed ordinary load twice: " + url, "LOAD_FAILURE_HOLD")
            await self.gate()
            raise ParserHold("Ranking requires audited continuation")
        previous, stable = None, 0
        for _ in range(18):
            await self.normal_html(page, worker)
            await page.evaluate("window.scrollBy(0, Math.max((window.innerHeight || 700) * 0.85, 500))")
            await asyncio.sleep(0.7)
            await self.normal_html(page, worker)
            metrics = await page.evaluate("""() => ({count: document.querySelectorAll('[id^="p13n-asin-index-"]').length,
                height: document.documentElement.scrollHeight, y: window.scrollY, viewport: window.innerHeight})""")
            signature = (metrics["count"], metrics["height"])
            stable = stable + 1 if signature == previous else 0
            previous = signature
            if metrics["y"] + metrics["viewport"] >= metrics["height"] - 24 and stable >= 2:
                break
        html = await self.normal_html(page, worker)
        path = directory / ("rendered_" + Path(meta["html_file"]).name)
        path.write_text(html, encoding="utf-8")
        meta.update(html_file=str(path.relative_to(self.output)), html_sha256=file_hash(path), final_url=page.url)
        immutable_json(path.with_suffix(".meta.json"), meta)
        return html, meta

    async def snapshot(self, page, category, role, worker):
        group = category["research_category"]
        for segment in self.runner.state["completed_segments"]:
            value = read_json(self.output / "ranking_snapshots" / segment / "manifest.json", {})
            if value.get("research_category") == group and value.get("role") == role:
                return
        segment = hashlib.sha256((str(self.output) + "|" + group + "|" + role).encode()).hexdigest()[:16]
        directory = self.output / "ranking_snapshots" / segment
        for n, source in enumerate(s for s in category["sources"] if s["role"] == role):
            for p in range(1, source.get("pages_per_url", category["pages_per_url"]) + 1):
                dest = directory / ("source_%03d_page_%02d" % (n, p))
                if (dest / "page_result.json").exists():
                    continue
                url = page_url(source["source_url"], p)
                self.worker_states[worker].update(status="RANKING", url=url, category=group)
                self.progress()
                # Resume normal saved captures offline, never request them twice.
                saved = sorted(dest.glob("rendered_*.meta.json"))
                if saved:
                    meta = read_json(saved[-1], {})
                    if meta["requested_url"] != url or file_hash(self.output / meta["html_file"]) != meta["html_sha256"]:
                        raise ValueError("SAVED_RANKING_CAPTURE_DRIFT")
                    feed = (self.output / meta["html_file"]).read_text(encoding="utf-8"), meta
                else:
                    feed = await self.ranking_capture(page, url, dest, worker)
                self.runner.feeds[str(dest)] = feed
        await self.gate()
        try:
            self.runner._snapshot(None, category, role)
        except ParserHold as exc:
            self.block(worker, page, str(exc), str(exc).split(":", 1)[0])
            await self.gate()
            raise
        self.allowed_links.update(r["product_link"] for r in self.runner.links)
        self.progress()

    async def detail(self, page, link, category, worker):
        item = link["snapshot_item_id"]
        if item in self.runner.state["detail_results"]:
            return
        dest = self.output / "detail_evidence" / item
        saved = sorted(dest.glob("*.meta.json"))
        feed = None
        for path in reversed(saved):
            meta = read_json(path, {})
            if meta.get("requested_url") == link["product_link"] and meta.get("access_state") == "NORMAL" and not meta.get("error"):
                if file_hash(self.output / meta["html_file"]) != meta["html_sha256"]:
                    raise ValueError("SAVED_DETAIL_HTML_DRIFT")
                feed = (self.output / meta["html_file"]).read_text(encoding="utf-8"), meta
                break
        self.worker_states[worker].update(status="DETAIL", url=link["product_link"], category=category["research_category"])
        self.progress()
        if feed is None:
            feed = await self.capture(page, link["product_link"], dest, worker)
        await self.gate()
        self.guard()
        self.runner.feeds[str(dest)] = feed
        try:
            self.runner._detail(None, link, category)
        except ParserHold as exc:
            self.block(worker, page, str(exc), "SYSTEMATIC_PARSER_HOLD")
            await self.gate()
            raise
        self.progress()

    def sample_budget(self, group):
        groups = list(self.categories)
        base, extra = divmod(50, len(groups))
        return base + (groups.index(group) < extra)

    def attempted(self, group):
        return sum(read_json(self.output / r["result_file"], {})["research_category"] == group
                   for r in self.runner.state["detail_results"].values())

    async def collect_category(self, page, cat, worker, phase):
        group = cat["research_category"]
        await self.snapshot(page, cat, "primary", worker)
        if phase == "SAMPLE":
            budget = self.sample_budget(group)
            links = self.runner._eligible_links(cat)
            sources = sorted({r["ranking_source_url"] for r in links})
            by_source = {s: [r for r in links if r["ranking_source_url"] == s
                            and r["snapshot_item_id"] not in self.runner.state["detail_results"]] for s in sources}
            ordered = [by_source[s][n] for n in range(budget) for s in sources if len(by_source[s]) > n]
            for link in ordered:
                if self.attempted(group) >= budget:
                    break
                await self.detail(page, link, cat, worker)
            if self.attempted(group) < budget:
                raise ValueError("SAMPLE_LINK_SHORTFALL: " + group)
            return
        for role in ("primary", "reserve"):
            if self.runner._category_count(group) >= cat["target_unique"]:
                break
            if role == "reserve":
                await self.snapshot(page, cat, role, worker)
            for link in self.runner._eligible_links(cat):
                if self.runner._category_count(group) >= cat["target_unique"]:
                    break
                if link["source_role"] == role:
                    await self.detail(page, link, cat, worker)

    async def category_worker(self, number, page, queue, phase):
        while not queue.empty():
            await self.wait_cooldown(number)
            await self.gate()
            try:
                cat = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.worker_states[number].update(status="RUNNING", category=cat["research_category"], phase=phase)
            self.progress()
            await self.collect_category(page, cat, number, phase)
            self.worker_states[number].update(status="COOLDOWN", url="",
                                              cooldown_until=self.now() + self.runner.metadata["cooldown_seconds"])
            self.progress()
            queue.task_done()

    async def run_phase(self, pages, phase):
        queue = asyncio.Queue()
        for group, cat in self.categories.items():
            needed = self.attempted(group) < self.sample_budget(group) if phase == "SAMPLE" else self.runner._category_count(group) < cat["target_unique"]
            if needed:
                queue.put_nowait(cat)
        await asyncio.gather(*(self.category_worker(n + 1, page, queue, phase) for n, page in enumerate(pages)))

    async def run(self, profile_dir):
        if self.runner.state["phase"] == "FINISHED":
            return read_json(self.output / "collection_report.json", {})
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(profile_dir, headless=False)
            try:
                pages = [await context.new_page() for _ in range(3)]
                self.progress("RUNNING")
                await self.preflight(pages[0])
                if self.runner.state["phase"] == "SAMPLE":
                    await self.run_phase(pages, "SAMPLE")
                    report = self.runner.batch_audit("sample_final")
                    hard = {"FIELD_BINDING_ERROR", "PARSER_MISSED", "BULLETS_BINDING_ERROR", "STRUCTURE_ERROR"}
                    if hard.intersection(report["issues"]) or report["statuses"].get("SUCCESS", 0) < 45 or report["attempted"] < 50:
                        raise ParserHold("SAMPLE_BLOCKED: offline review required")
                    immutable_json(self.output / "sample_gate.json", {"status": "PASS_COLLECTION_SAMPLE", **report,
                                                                      "formal_source_gate": "NOT_EVALUATED"})
                    self.runner.state["phase"] = "FULL"
                    self.progress("RUNNING")
                await self.run_phase(pages, "FULL")
                await self.gate()
                self.guard()
                report = self.runner._final()
                self.progress()
                print(report, flush=True)
                return report
            except Exception as exc:
                self.ready.clear()
                self.progress("ERROR")
                print("ERROR: " + str(exc), flush=True)
                # Browser and process remain visible/alive for human diagnosis.
                while True:
                    await asyncio.sleep(1)
            finally:
                await context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("origin", "out-dir", "plan"):
        parser.add_argument("--" + field, type=Path, required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        lock(stack, args.origin / "process.lock")
        lock(stack, args.out_dir / "process.lock")
        batch = ParallelCoverage(args.origin, args.out_dir, args.plan, resume=args.resume)
        batch.guard()
        if args.prepare_only:
            print({"status": "PARALLEL3_PREPARED", "preserved_results": len(batch.runner.state["detail_results"]),
                   "selected_unique": len(batch.runner.selected), "max_workers": 3}, flush=True)
        else:
            asyncio.run(batch.run(str(Path(args.profile_dir).resolve())))


if __name__ == "__main__":
    main()
