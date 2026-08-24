from pathlib import Path

from amazon_es_bestseller.config import load_settings


def test_load_settings_enforces_reconnaissance_hard_limits():
    settings = load_settings(Path("config/settings.yaml"))
    assert settings.max_categories == 3
    assert settings.max_products_per_category == 50
    assert settings.max_detail_samples == 5
    assert settings.page_delay_seconds >= 3
