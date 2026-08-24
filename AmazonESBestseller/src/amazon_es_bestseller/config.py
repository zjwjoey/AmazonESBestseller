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
    settings = Settings(
        root_urls=dict(raw["root_urls"]),
        page_delay_seconds=float(raw["page_delay_seconds"]),
        max_categories=int(raw["max_categories"]),
        max_products_per_category=int(raw["max_products_per_category"]),
        max_detail_samples=int(raw["max_detail_samples"]),
        headless=bool(raw["headless"]),
    )
    if settings.page_delay_seconds < 3:
        raise ValueError("page_delay_seconds violates reconnaissance hard limit: minimum is 3")
    if settings.max_categories > 3:
        raise ValueError("max_categories violates reconnaissance hard limit: maximum is 3")
    if settings.max_products_per_category > 50:
        raise ValueError(
            "max_products_per_category violates reconnaissance hard limit: maximum is 50"
        )
    if settings.max_detail_samples > 5:
        raise ValueError("max_detail_samples violates reconnaissance hard limit: maximum is 5")
    if any(
        value < 0
        for value in (
            settings.max_categories,
            settings.max_products_per_category,
            settings.max_detail_samples,
        )
    ):
        raise ValueError("reconnaissance limits cannot be negative")
    return settings
