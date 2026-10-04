import json
from pathlib import Path

from amazon_es_bestseller.collection.ranking import collect_rankings


class _Page:
    def __init__(self, html):
        self.html = html

    def content(self):
        return self.html


class _Session:
    def __init__(self, html):
        self.page = _Page(html)

    def goto(self, url):
        return 200

    def wait_between_requests(self):
        return None


def test_collector_persists_identity_immediately_after_saved_html(tmp_path):
    html = "<div id='gridItemRoot' data-asin='B078C6QR1C'><a href='/dp/B078C6QR1C'>x</a></div>"
    result = collect_rankings(["https://www.amazon.es/gp/bestsellers/tools"],
                              _Session(html), str(tmp_path))
    identity = json.loads((Path(result.run_dir) / "identity.json").read_text(encoding="utf-8"))
    assert identity[0]["asin"] == "B078C6QR1C"
    assert identity[0]["product_url"] == "https://www.amazon.es/dp/B078C6QR1C"
    assert identity[0]["source_url"] == "https://www.amazon.es/gp/bestsellers/tools"
    assert identity[0]["page_number"] == 1
