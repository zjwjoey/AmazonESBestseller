from amazon_es_bestseller.image_downloader import download_product_images
from amazon_es_bestseller.models import ProductSummary


def test_image_downloader_writes_asin_filename_and_records_success(tmp_path):
    product = ProductSummary(asin="B012345678", image_url="https://images.example/one")

    results = download_product_images(
        [product],
        tmp_path,
        delay_seconds=0,
        fetch=lambda url: (b"jpeg-bytes", "image/jpeg"),
    )

    assert results[0].status == "downloaded"
    assert product.image_download_status == "downloaded"
    assert product.image_path == "images/B012345678.jpg"
    assert (tmp_path / "B012345678.jpg").read_bytes() == b"jpeg-bytes"


def test_image_downloader_records_one_failure_without_retrying(tmp_path):
    product = ProductSummary(asin="B012345678", image_url="https://images.example/missing")
    calls = []

    def fail_once(url):
        calls.append(url)
        raise OSError("HTTP 404")

    results = download_product_images([product], tmp_path, delay_seconds=0, fetch=fail_once)

    assert len(calls) == 1
    assert results[0].status == "failed"
    assert product.image_download_status == "failed"
    assert product.image_download_error == "HTTP 404"


def test_image_downloader_stops_after_access_restriction(tmp_path):
    products = [
        ProductSummary(asin="B012345678", image_url="https://images.example/blocked"),
        ProductSummary(asin="B012345679", image_url="https://images.example/unused"),
    ]
    calls = []

    def blocked(url):
        calls.append(url)
        raise OSError("HTTP 403")

    results = download_product_images(products, tmp_path, delay_seconds=0, fetch=blocked)

    assert len(calls) == 1
    assert len(results) == 1
    assert products[1].image_download_status is None
