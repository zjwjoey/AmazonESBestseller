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


def test_probe_applies_delay_when_batch_uses_nonzero_artifact_index(tmp_path, monkeypatch):
    page = FakePage(tmp_path)
    store = RunStore.create(tmp_path, "delayed")
    sleeps = []
    monkeypatch.setattr("amazon_es_bestseller.browser_probe.time.sleep", sleeps.append)

    probe_urls(
        page,
        store,
        ["https://www.amazon.es/1", "https://www.amazon.es/2", "https://www.amazon.es/3"],
        delay_seconds=3,
        start_index=4,
    )

    assert sleeps == [3, 3]


def test_hidden_sign_in_copy_does_not_block_a_normal_page(tmp_path):
    page = FakePage(tmp_path)
    page.next_result = (
        "Amazon.es product",
        '<main>Product</main><div class="aok-hidden">Sign in to continue</div>',
        200,
    )
    store = RunStore.create(tmp_path, "hidden-login-copy")

    events = probe_urls(page, store, ["https://www.amazon.es/dp/B012345678"], delay_seconds=0)

    assert events[0].access_state is AccessState.NORMAL


def test_probe_marks_external_redirect_as_unknown(tmp_path):
    class RedirectPage(FakePage):
        def goto(self, url, wait_until="domcontentloaded", timeout=45000):
            self.goto_calls.append(url)
            self.url = "https://example.invalid/interstitial"
            return FakeResponse(200)

    page = RedirectPage(tmp_path)
    store = RunStore.create(tmp_path, "redirect")

    events = probe_urls(page, store, ["https://www.amazon.es/"], delay_seconds=0)

    assert events[0].access_state is AccessState.UNKNOWN
    assert "unexpected host" in events[0].reason
