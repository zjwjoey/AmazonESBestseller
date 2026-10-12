"""Explicitly authorized three-page detail trial over an immutable link snapshot.

No ranking navigation, no ASIN derivation from URLs, no provider calls. All
Playwright pages share the original persistent profile and one access gate.
Parsing/QA/checkpoint commits stay serial through the existing detail runner.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amazon_es_bestseller.access.detector import detect_access_status  # noqa: E402
from amazon_es_bestseller.access.location import inspect_delivery_location  # noqa: E402
from amazon_es_bestseller.collection.ranking_links import safe_product_link  # noqa: E402
from amazon_es_bestseller.orchestration.checkpoint import read_json, write_json_atomic  # noqa: E402
from amazon_es_bestseller.orchestration.link_first import (  # noqa: E402
    LinkFirstRunner, RESTRICTED, canonical_json_sha256, file_hash, immutable_json, utc,
)
from amazon_es_bestseller.quality.source_fields import audit_source_fields, source_gate  # noqa: E402
from amazon_es_bestseller.runtime_state import VersionedCheckpointStore  # noqa: E402

GROUPS = {"居家生活", "厨房用品"}
CONTRACT = "bounded-link-detail-980-parallel3-v1"


def safe_copy(origin: Path, output: Path, relative: str, hashes: dict):
    source = (origin / relative).resolve()
    source.relative_to(origin.resolve())
    target = (output / relative).resolve()
    target.relative_to(output.resolve())
    expected = file_hash(source)
    hashes[relative] = expected
    if target.exists():
        if file_hash(target) != expected:
            raise ValueError("COPIED_EVIDENCE_DRIFT: " + relative)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def freeze_inputs(origin: Path, output: Path, expected_links: int):
    state_path = origin / "checkpoints" / "link_first_state.latest.json"
    state = VersionedCheckpointStore(origin / "checkpoints", "link_first_state").load()
    hashes, rows, segments = {}, [], {}
    for segment, expected in state["completed_segments"].items():
        relative = "ranking_snapshots/" + segment + "/manifest.json"
        manifest = read_json(origin / relative, None)
        if canonical_json_sha256(manifest) != expected:
            raise ValueError("SOURCE_MANIFEST_DRIFT")
        if manifest["research_category"] not in GROUPS:
            continue
        if manifest.get("asin_extraction") is not False:
            raise ValueError("NOT_A_LINK_ONLY_SNAPSHOT")
        safe_copy(origin, output, relative, hashes)
        for page in manifest["pages"]:
            if file_hash(origin / page["html_file"]) != page["html_sha256"]:
                raise ValueError("SOURCE_RANKING_HTML_DRIFT")
            safe_copy(origin, output, page["html_file"], hashes)
        rows.extend(manifest["records"])
        segments[segment] = expected
    ids = {row["snapshot_item_id"] for row in rows}
    if len(rows) != expected_links or len(ids) != len(rows):
        raise ValueError("BOUNDED_LINK_COUNT_OR_ITEM_ID_MISMATCH")
    if {row["research_category"] for row in rows} != GROUPS:
        raise ValueError("BOUNDED_CATEGORY_SCOPE_MISMATCH")
    details = {}
    for item_id, binding in state["detail_results"].items():
        if item_id not in ids:
            continue
        result = read_json(origin / binding["result_file"], None)
        if file_hash(origin / binding["result_file"]) != binding["sha256"]:
            raise ValueError("SOURCE_DETAIL_RESULT_DRIFT")
        safe_copy(origin, output, binding["result_file"], hashes)
        if result.get("html_file"):
            if file_hash(origin / result["html_file"]) != result["html_sha256"]:
                raise ValueError("SOURCE_DETAIL_HTML_DRIFT")
            safe_copy(origin, output, result["html_file"], hashes)
        details[item_id] = binding
    frozen = {"contract": CONTRACT, "origin": str(origin.resolve()),
              "source_checkpoint_sha256": file_hash(state_path),
              "source_metadata_sha256": file_hash(origin / "run_metadata.json"),
              "snapshot_link_count": len(rows), "link_sha256": canonical_json_sha256(rows),
              "categories": sorted(GROUPS), "workers": 3, "ranking_requests": 0,
              "translation": "disabled", "source_files": hashes,
              "completed_segments": segments, "reused_detail_results": details,
              "links": rows, "formal_release": False,
              "authorization": "User: 这两个类目，使用三路浏览器并行提取详情，980条先提取完看看效果"}
    immutable_json(output / "batch_inputs.json", frozen)
    return frozen


class ParserHold(RuntimeError):
    pass


class EvidenceParser(LinkFirstRunner):
    def _capture(self, session, url, destination, **kwargs):
        if url != self.feed[1]["requested_url"]:
            raise ValueError("DETAIL_LINK_BINDING_MISMATCH")
        return self.feed

    def _manual_hold(self, status, error):
        raise ParserHold(status + ": " + error)


class Batch:
    def __init__(self, origin: Path, output: Path, plan: Path, *, resume=False):
        self.origin, self.output = origin.resolve(), output.resolve()
        self.runner = EvidenceParser(plan, output, ROOT, sample_size=50,
                                     continue_after_sample=False, resume=resume)
        self.inputs = freeze_inputs(self.origin, self.output, 980)
        self.entry_hash = file_hash(Path(__file__))
        immutable_json(output / "batch_runtime.json", {
            "contract": CONTRACT, "entry_sha256": self.entry_hash,
            "inputs_sha256": file_hash(output / "batch_inputs.json"),
            "profile_policy": "same-original-profile-three-visible-pages",
            "max_concurrent_requests": 3, "per_worker_serial": True,
            "new_categories": False, "ranking": False, "translation": False})
        for segment, value in self.inputs["completed_segments"].items():
            self.runner.state["completed_segments"].setdefault(segment, value)
        for item_id, value in self.inputs["reused_detail_results"].items():
            self.runner.state["detail_results"].setdefault(item_id, value)
        self.runner.links = []
        self.runner.records = {}
        self.runner.selected = {}
        self.runner._recover()
        self.runner.state.update(phase="BOUNDED_DETAIL", error="", hold_token="", cooldown_until=0)
        self.worker_states = {}
        self.ready = asyncio.Event()
        self.ready.set()
        self.holds = {}
        self.token = ""
        self.allowed_links = {r["product_link"] for r in self.inputs["links"] if r["product_link"]}
        self.dispatch_lock = asyncio.Lock()
        self.categories = {c["research_category"]: c for c in self.runner.plan["categories"]}
        self.inputs_hash = file_hash(output / "batch_inputs.json")
        # A crash between immutable result write and checkpoint commit must not
        # cause a second request. Recover only results bound to frozen items.
        by_id = {row["snapshot_item_id"]: row for row in self.inputs["links"]}
        recovered = False
        for path in sorted((output / "detail_evidence").glob("*/result.json")):
            item_id = path.parent.name
            if item_id in self.runner.state["detail_results"]:
                continue
            result = read_json(path, None)
            if (item_id not in by_id or result.get("snapshot_item_id") != item_id
                    or result.get("requested_url") != by_id[item_id]["product_link"]
                    or not result.get("status")):
                raise ValueError("UNBOUND_ORPHAN_RESULT")
            if result.get("html_file") and file_hash(output / result["html_file"]) != result["html_sha256"]:
                raise ValueError("ORPHAN_HTML_DRIFT")
            self.runner.state["detail_results"][item_id] = {
                "result_file": str(path.relative_to(output)), "sha256": file_hash(path), "status": result["status"]}
            recovered = True
        if recovered:
            self.runner.records = {}
            self.runner.selected = {}
            self.runner.links = []
            self.runner._recover()
        self.progress("PREPARED")

    def guard(self):
        self.runner.guards()
        if file_hash(Path(__file__)) != self.entry_hash or file_hash(self.output / "batch_inputs.json") != self.inputs_hash:
            raise ValueError("BOUNDED_RUNTIME_DRIFT")
        if file_hash(self.origin / "checkpoints" / "link_first_state.latest.json") != self.inputs["source_checkpoint_sha256"]:
            raise ValueError("ORIGIN_CHECKPOINT_CHANGED")

    def progress(self, status=None):
        if status:
            self.runner.save(status)
        counts = Counter(binding["status"] for binding in self.runner.state["detail_results"].values())
        write_json_atomic(self.output / "batch_progress.json", {
            "contract": CONTRACT, "updated_at": utc(), "status": status or self.runner.state["status"],
            "snapshot_links": len(self.inputs["links"]),
            "completed_links": len(self.runner.state["detail_results"]),
            "pending_links": len(self.inputs["links"]) - len(self.runner.state["detail_results"]),
            "unique_confirmed_asins": len(self.runner.records), "statuses": dict(counts),
            "reused_results": len(self.inputs["reused_detail_results"]),
            "workers": self.worker_states, "hold_token": self.token, "holds": list(self.holds),
            "translation": "disabled", "formal_release": False})

    def block(self, worker, page, reason, status="ACCESS_STOP"):
        # Clear immediately upon observation, before any evidence IO or await.
        self.ready.clear()
        self.holds[worker] = {"page": page, "reason": reason, "status": status}
        self.token = canonical_json_sha256({"at": utc(), "holds": {str(k): v["reason"] for k, v in self.holds.items()}})
        self.progress(status)
        print(status + ": worker " + str(worker) + ": " + reason, flush=True)

    async def gate(self):
        while not self.ready.is_set():
            control = read_json(self.output / "operator_control.json", {})
            if control.get("action") == "resume" and control.get("human_handled") is True and control.get("hold_token") == self.token:
                self.guard()
                epoch = self.token
                cleared = True
                for hold in list(self.holds.values()):
                    html = await hold["page"].content()
                    if (hold["status"] not in {"ACCESS_STOP", "DELIVERY_LOCATION_HOLD"}
                            or detect_access_status(200, html).value != "NORMAL"
                            or inspect_delivery_location(html).is_spain is not True):
                        cleared = False
                if cleared and self.token == epoch:
                    self.holds.clear()
                    self.token = ""
                    self.ready.set()
                    self.progress("RUNNING")
                    return
            await asyncio.sleep(1)

    async def navigate(self, page, url):
        if url != "https://www.amazon.es/" and url not in self.allowed_links:
            raise ValueError("UNAUTHORIZED_NAVIGATION")
        while True:
            await self.gate()
            async with self.dispatch_lock:
                if not self.ready.is_set():
                    continue
                self.guard()
                task = asyncio.create_task(page.goto(url, wait_until="domcontentloaded", timeout=45000))
                break
        return await task

    async def capture(self, page, url, directory, worker):
        directory.mkdir(parents=True, exist_ok=True)
        for ordinary_attempt in range(2):
            await self.gate()
            number = len(list(directory.glob("attempt_*.html"))) + 1
            error, html, status, held = "", "", None, False
            try:
                response = await self.navigate(page, url)
                status = response.status if response else None
                html = await page.content()
                access = detect_access_status(status, html).value
                if access in RESTRICTED:
                    self.block(worker, page, access + " " + url)
                    held = True
                else:
                    await self.gate()
                    await asyncio.sleep(3)
                    html = await page.content()
                    access = detect_access_status(status, html).value
                    if access in RESTRICTED:
                        self.block(worker, page, access + " " + url)
                        held = True
                    elif status == 202 and access == "UNKNOWN":
                        from bs4 import BeautifulSoup
                        if BeautifulSoup(html, "lxml").select_one("#productTitle, #nav-global-location-popover-link"):
                            access = detect_access_status(200, html).value
                    if access == "NORMAL" and inspect_delivery_location(html).is_spain is not True:
                        self.block(worker, page, "Spain delivery not verified on " + url, "DELIVERY_LOCATION_HOLD")
                        held = True
            except Exception as exc:
                error = str(exc)
                try:
                    html = await page.content()
                except Exception:
                    pass
                access = detect_access_status(status, html).value
                if access in RESTRICTED:
                    self.block(worker, page, access + " " + url)
                    held = True
            path = directory / ("attempt_%03d.html" % number)
            path.write_text(html, encoding="utf-8")
            meta = {"requested_url": url, "final_url": page.url, "http_status": status,
                    "access_state": access, "observed_at": utc(), "error": error,
                    "html_file": str(path.relative_to(self.output)), "html_sha256": file_hash(path), "worker_id": worker}
            immutable_json(path.with_suffix(".meta.json"), meta)
            if held:
                await self.gate()
                html = await page.content()
                # Human cleared this exact visible page: never auto-reopen it.
                path = directory / ("human_handled_%03d.html" % number)
                path.write_text(html, encoding="utf-8")
                meta.update(html_file=str(path.relative_to(self.output)), html_sha256=file_hash(path),
                            access_state="NORMAL", human_handled=True, final_url=page.url, error="")
                immutable_json(path.with_suffix(".meta.json"), meta)
                await asyncio.sleep(2)
                return html, meta
            await asyncio.sleep(2)
            if not error and access == "NORMAL":
                return html, meta
            if ordinary_attempt == 1:
                return None, meta
        raise AssertionError("bounded retry exhausted")

    async def preflight(self, page):
        html, _ = await self.capture(page, "https://www.amazon.es/", self.output / "preflight", 0)
        if html is None:
            self.block(0, page, "Homepage failed twice", "LOAD_FAILURE_HOLD")
            await self.gate()
        while inspect_delivery_location(html).is_spain is not True:
            self.block(0, page, "Current destination: " + inspect_delivery_location(html).text, "DELIVERY_LOCATION_HOLD")
            await self.gate()
            html = await page.content()
        immutable_json(self.output / "delivery_preflight.json", {
            "location": inspect_delivery_location(html).text, "is_spain": True})

    def commit(self, link, feed):
        self.guard()
        self.runner.feed = feed
        try:
            self.runner._detail(None, link, self.categories[link["research_category"]])
        except ParserHold:
            raise
        except Exception as exc:
            # Every link has an explicit result, including parser exceptions.
            if link["snapshot_item_id"] in self.runner.state["detail_results"]:
                raise
            result = {**feed[1], "snapshot_item_id": link["snapshot_item_id"],
                      "research_category": link["research_category"], "status": "PARSER_ERROR",
                      "selected": False, "error": str(exc), "source_url": link["ranking_source_url"]}
            path = self.output / "detail_evidence" / link["snapshot_item_id"] / "result.json"
            immutable_json(path, result)
            self.runner.state["detail_results"][link["snapshot_item_id"]] = {
                "result_file": str(path.relative_to(self.output)), "sha256": file_hash(path), "status": "PARSER_ERROR"}
            self.runner.save("RUNNING")
            self.runner._systemic["PARSER_ERROR"] += 1
            if self.runner._systemic["PARSER_ERROR"] >= 3:
                self.runner.save("SYSTEMATIC_PARSER_HOLD")
                raise ParserHold("Three consecutive parser exceptions; offline repair required")
        else:
            self.runner._systemic["PARSER_ERROR"] = 0
        self.progress("RUNNING" if self.ready.is_set() else "ACCESS_STOP")

    async def worker(self, number, page, queue):
        while not queue.empty():
            await self.gate()
            try:
                link = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self.worker_states[number] = {"status": "RUNNING", "snapshot_item_id": link["snapshot_item_id"],
                                          "url": link["product_link"], "category": link["research_category"]}
            self.progress("RUNNING")
            directory = self.output / "detail_evidence" / link["snapshot_item_id"]
            if link["snapshot_audit"] != "PASS" or not safe_product_link(link["product_link"]):
                feed = (None, {"requested_url": link["product_link"], "error": "INVALID_SNAPSHOT_LINK", "observed_at": utc()})
            else:
                feed = await self.capture(page, link["product_link"], directory, number)
            try:
                self.commit(link, feed)
            except ParserHold as exc:
                self.block(number, page, str(exc), "SYSTEMATIC_PARSER_HOLD")
                await self.gate()
            queue.task_done()
        self.worker_states[number] = {"status": "IDLE"}
        self.progress()

    def final(self):
        self.guard()
        outcomes, contexts, issues = [], [], []
        products = {}
        for link in self.inputs["links"]:
            binding = self.runner.state["detail_results"].get(link["snapshot_item_id"])
            if binding is None:
                raise ValueError("UNPROCESSED_LINK")
            result = read_json(self.output / binding["result_file"], None)
            if file_hash(self.output / binding["result_file"]) != binding["sha256"]:
                raise ValueError("FINAL_RESULT_DRIFT")
            outcomes.append({"snapshot_item_id": link["snapshot_item_id"], "url": link["product_link"],
                             "category": link["research_category"], "status": result["status"],
                             "asin": result.get("record", {}).get("asin", ""), "error": result.get("error", "")})
            issues.extend({"snapshot_item_id": link["snapshot_item_id"], **issue}
                          for issue in result.get("audit", {}).get("issues", []))
            if result["status"] == "SUCCESS":
                row = dict(result["record"])
                context = {**link, "asin": row["asin"], "identity_resolved_at_detail": True}
                contexts.append(context)
                if row["asin"] not in products:
                    row.update(research_category=link["research_category"], bestseller_rank=link["bestseller_rank"], ranking_contexts=[])
                    products[row["asin"]] = row
                products[row["asin"]]["ranking_contexts"].append(context)
        candidates = list(products.values())
        audit = audit_source_fields(candidates, ranking_matrix=contexts)
        passed, gate = source_gate(audit)
        for name, value in (("link_outcomes.json", outcomes), ("resolved_ranking_contexts.json", contexts),
                            ("spanish_candidate.json", candidates), ("field_issues.json", issues), ("source_audit.json", audit)):
            immutable_json(self.output / name, value)
        report = {"contract": CONTRACT, "status": "BOUNDED_DETAIL_COMPLETE", "link_count": len(outcomes),
                  "unique_confirmed_asins": len(candidates), "statuses": dict(Counter(r["status"] for r in outcomes)),
                  "field_issues": dict(Counter(r["code"] for r in issues)), "source_gate": gate,
                  "source_gate_passed": passed, "formal_release": False, "translation": "disabled"}
        immutable_json(self.output / "batch_report.json", report)
        self.progress("BOUNDED_DETAIL_COMPLETE")
        return report

    async def run(self, profile_dir):
        from playwright.async_api import async_playwright
        if (self.output / "batch_report.json").exists():
            return read_json(self.output / "batch_report.json", {})
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(profile_dir, headless=False)
            try:
                pages = [await context.new_page() for _ in range(3)]
                await self.preflight(pages[0])
                queue = asyncio.Queue()
                for link in self.inputs["links"]:
                    if link["snapshot_item_id"] not in self.runner.state["detail_results"]:
                        queue.put_nowait(link)
                self.progress("RUNNING")
                await asyncio.gather(*(self.worker(i + 1, page, queue) for i, page in enumerate(pages)))
                self.progress("AUDITING")
                report = self.final()
                print(report, flush=True)
                return report
            except Exception as exc:
                self.ready.clear()
                self.progress("ERROR")
                print("ERROR: " + str(exc), flush=True)
                # Preserve the visible browser and evidence for diagnosis.
                while True:
                    await asyncio.sleep(1)
            finally:
                await context.close()


def lock(stack, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = stack.enter_context(path.open("a+b"))
    if sys.platform == "win32":
        import msvcrt
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.origin.resolve() == args.out_dir.resolve():
        raise ValueError("INDEPENDENT_OUTPUT_REQUIRED")
    with ExitStack() as stack:
        lock(stack, args.origin / "process.lock")
        lock(stack, args.out_dir / "process.lock")
        batch = Batch(args.origin, args.out_dir, args.plan, resume=args.resume)
        if args.prepare_only:
            print({"status": "PREPARED_OFFLINE", "links": len(batch.inputs["links"]),
                   "reused": len(batch.inputs["reused_detail_results"]), "ranking_requests": 0}, flush=True)
        else:
            asyncio.run(batch.run(str(Path(args.profile_dir).resolve())))


if __name__ == "__main__":
    main()
