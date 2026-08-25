from pathlib import Path

from amazon_es_bestseller.config import load_settings


def test_load_settings_enforces_reconnaissance_hard_limits():
    settings = load_settings(Path("config/settings.yaml"))
    assert settings.root_urls["diy"] == "https://www.amazon.es/gp/bestsellers/tools"
    assert settings.max_categories == 3
    assert settings.max_products_per_category == 50
    assert settings.max_detail_samples == 5
    assert settings.max_breadth_discovery_pages == 5
    assert settings.max_breadth_leaf_categories == 5
    assert settings.max_breadth_detail_samples == 100
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


def test_load_settings_rejects_breadth_limits_above_ten(tmp_path: Path):
    path = tmp_path / "unsafe-breadth.yaml"
    path.write_text(
        """root_urls:
  home: https://www.amazon.es/
  bestsellers: https://www.amazon.es/gp/bestsellers
  kitchen: https://www.amazon.es/gp/bestsellers/kitchen
page_delay_seconds: 3
max_categories: 3
max_products_per_category: 50
max_detail_samples: 5
max_breadth_discovery_pages: 11
max_breadth_leaf_categories: 11
max_breadth_detail_samples: 101
headless: false
""",
        encoding="utf-8",
    )

    try:
        load_settings(path)
    except ValueError as exc:
        assert "breadth" in str(exc)
    else:
        raise AssertionError("unsafe breadth settings must be rejected")
