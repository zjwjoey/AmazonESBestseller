import csv
import logging
from dataclasses import asdict
from pathlib import Path

from .models import ProbeEvent


class RunStore:
    def __init__(self, root: Path):
        self.root = root
        self.html_dir = root / "html"
        self.screenshots_dir = root / "screenshots"
        self.raw_dir = root / "raw"
        self.failures_dir = root / "failures"
        self.parsed_dir = root / "parsed"
        self.logs_dir = root / "logs"
        self.events_path = root / "access_events.csv"
        self.logger = logging.getLogger(f"amazon_es_bestseller.run.{root.name}")

    @classmethod
    def create(cls, base_dir: Path, run_id: str) -> "RunStore":
        root = base_dir / "runs" / run_id
        if root.exists():
            raise FileExistsError(f"run already exists: {root}")
        store = cls(root)
        for directory in (
            root,
            store.html_dir,
            store.screenshots_dir,
            store.raw_dir,
            store.failures_dir,
            store.parsed_dir,
            store.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=False)
        store.logger.setLevel(logging.INFO)
        store.logger.propagate = False
        handler = logging.FileHandler(store.logs_dir / "run.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        store.logger.addHandler(handler)
        return store

    def log_info(self, message: str, *args) -> None:
        self.logger.info(message, *args)

    def close(self) -> None:
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)

    def save_html(self, name: str, html: str, failure: bool = False) -> Path:
        directory = self.failures_dir if failure else self.html_dir
        path = directory / f"{name}.html"
        path.write_text(html, encoding="utf-8")
        return path

    def save_screenshot(self, name: str, page, failure: bool = False) -> Path:
        directory = self.failures_dir if failure else self.screenshots_dir
        path = directory / f"{name}.png"
        page.screenshot(path=str(path), full_page=True)
        return path

    def record_event(self, event: ProbeEvent) -> None:
        fields = list(asdict(event))
        write_header = not self.events_path.exists()
        with self.events_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if write_header:
                writer.writeheader()
            writer.writerow(asdict(event))
        self.logger.info(
            "%s %s status=%s state=%s body_length=%s duration=%.3f reason=%s",
            event.requested_url,
            event.navigation_result,
            event.status,
            event.access_state.value,
            event.body_length,
            event.load_duration,
            event.reason or "",
        )
