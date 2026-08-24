from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Settings:
    root_urls: dict[str, str]
    page_delay_seconds: float
    max_categories: int
    max_products_per_category: int
    max_detail_samples: int
    headless: bool


def load_settings(path: Path) -> Settings:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings(
        root_urls=dict(raw["root_urls"]),
        page_delay_seconds=float(raw["page_delay_seconds"]),
        max_categories=int(raw["max_categories"]),
        max_products_per_category=int(raw["max_products_per_category"]),
        max_detail_samples=int(raw["max_detail_samples"]),
        headless=bool(raw["headless"]),
    )
