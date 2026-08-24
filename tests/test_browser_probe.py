from dataclasses import dataclass

from amazon_es_bestseller.browser_probe import probe_urls
from amazon_es_bestseller.models import AccessState
from amazon_es_bestseller.run_store import RunStore


@dataclass
class FakeResponse:
    status: int


class FakePage:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.next_result = ("Amazon.es", "<main>normal</main>", 200)
        self.goto_calls = []
        self.url = ""

    def goto(self, url, wait_until="domcontentloaded", timeout=45000):
        self.goto_calls.append(url)
        self.url = url
        return FakeResponse(self.next_result[2])

    def title(self):
        return self.next_result[0]

    def content(self):
        return self.next_result[1]

    def screenshot(self, path, full_page=True):
        from pathlib import Path

        Path(path).write_bytes(b"fake screenshot")


def test_probe_does_not_navigate_after_challenge(tmp_path):
    page = FakePage(tmp_path)
    page.next_result = ("Robot Check", "captcha", 200)
    store = RunStore.create(tmp_path, "blocked")

    events = probe_urls(
        page,
        store,
        ["https://www.amazon.es/", "https://example.invalid/"],
        delay_seconds=0,
    )

    assert len(events) == 1
    assert events[0].access_state is AccessState.CHALLENGE
    assert page.goto_calls == ["https://www.amazon.es/"]
