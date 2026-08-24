from pathlib import Path

from amazon_es_bestseller.config import load_settings


def test_load_settings_enforces_reconnaissance_hard_limits():
    settings = load_settings(Path("config/settings.yaml"))
    assert settings.max_categories == 3
    assert settings.max_products_per_category == 50
    assert settings.max_detail_samples == 5
    assert settings.page_delay_seconds >= 3


def test_load_settings_rejects_values_outside_reconnaissance_limits(tmp_path: Path):
    path = tmp_path / "unsafe.yaml"
    path.write_text(
        """root_urls:
  home: https://www.amazon.es/
  bestsellers: https://www.amazon.es/gp/bestsellers
  kitchen: https://www.amazon.es/gp/bestsellers/kitchen
page_delay_seconds: 0
max_categories: 4
max_products_per_category: 51
max_detail_samples: 6
headless: false
""",
        encoding="utf-8",
    )

    try:
        load_settings(path)
    except ValueError as exc:
        assert "hard limit" in str(exc)
    else:
        raise AssertionError("unsafe reconnaissance settings must be rejected")
