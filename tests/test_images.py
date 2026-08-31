from amazon_es_bestseller.collection.images import download_images


def test_download_images_is_asin_keyed_and_resumable(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return "image/png", b"PNG"

    records = [{"asin": "b000000001", "image_url": "https://img/one"},
               {"asin": "B000000002", "image_url": "https://img/two"}]
    first = download_images(records, tmp_path, delay_seconds=0, fetcher=fetch)
    assert set(first) == {"B000000001", "B000000002"}
    assert (tmp_path / "B000000001.png").read_bytes() == b"PNG"
    second = download_images(records, tmp_path, delay_seconds=0, fetcher=fetch)
    assert second["B000000001"]["status"] == "cached"
    assert len(calls) == 2
