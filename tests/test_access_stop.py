# -*- coding: utf-8 -*-
"""访问门禁测试（ARCHITECTURE §6）：非 NORMAL 停止采集，受限页 HTML 保留证据。

全部离线：用 fake session 模拟受限访问，不启动真实浏览器。
"""
import pytest

from amazon_es_bestseller.access.detector import AccessStopError, require_normal_access, detect_access_status
from amazon_es_bestseller.models import AccessState

#: 页面前 300 字符含 CAPTCHA 信号 → CHALLENGE
CHALLENGE_HTML = (
    "Captcha: Type the characters you see in this image to continue shopping. "
    "<div>resolver el captcha</div>" + "<p>x</p>" * 80
)
NORMAL_HTML = "<html><body><h1 id='productTitle'>Cafetera</h1></body></html>"


class _FakePage:
    def __init__(self, html):
        self._html = html

    def content(self):
        return self._html


class _FakeSession:
    """BrowserSession 最小替身：goto 返回固定状态码，page.content 返回固定 HTML。"""

    def __init__(self, status, html):
        self._status = status
        self.page = _FakePage(html)

    def goto(self, url):
        return self._status

    def wait_for_product_page(self):
        pass

    def wait_for_price_text(self):
        pass

    def wait_between_requests(self):
        pass


def test_require_normal_access_ok():
    require_normal_access(AccessState.NORMAL, "HTTP 200")  # 不抛


def test_http_200_validation_page_is_challenge_even_when_signal_is_deep():
    from amazon_es_bestseller.access.detector import detect_access_status
    html = "<html><body>" + ("x " * 500) + "/errors_page/validateCaptcha?foo=1" + "</body></html>"
    assert detect_access_status(200, html) is AccessState.CHALLENGE


@pytest.mark.parametrize("state", [
    AccessState.BLOCKED, AccessState.RATE_LIMITED, AccessState.CHALLENGE,
    AccessState.NETWORK_ERROR, AccessState.UNKNOWN,
])
def test_require_normal_access_blocks(state):
    with pytest.raises(AccessStopError) as ei:
        require_normal_access(state, "HTTP 403")
    assert "访问状态 %s" % state.value in str(ei.value)
    assert "HTTP 403" in str(ei.value)          # 上下文可定位
    assert isinstance(ei.value, RuntimeError)


def test_collect_details_stops_on_challenge(tmp_path):
    from amazon_es_bestseller.collection.detail import collect_details
    session = _FakeSession(200, CHALLENGE_HTML)
    with pytest.raises(AccessStopError) as ei:
        collect_details(["B008YETL18"], session, str(tmp_path))
    assert "CHALLENGE" in str(ei.value)
    assert "B008YETL18" in str(ei.value)
    # 受限页 HTML 已落盘保留证据；details.json 不写出（不产出不可信详情）
    assert (tmp_path / "html" / "B008YETL18.html").exists()
    assert not (tmp_path / "details.json").exists()


def test_collect_details_stops_on_403(tmp_path):
    from amazon_es_bestseller.collection.detail import collect_details
    session = _FakeSession(403, NORMAL_HTML)
    with pytest.raises(AccessStopError):
        collect_details(["B008YETL18"], session, str(tmp_path))
    assert (tmp_path / "html" / "B008YETL18.html").exists()
    assert not (tmp_path / "details.json").exists()


def test_collect_rankings_stops_on_403(tmp_path):
    from amazon_es_bestseller.collection.ranking import collect_rankings
    session = _FakeSession(403, NORMAL_HTML)
    with pytest.raises(AccessStopError):
        collect_rankings(["https://www.amazon.es/zgbs/1"], session, str(tmp_path))
    # 受限页 HTML 已落盘；rankings.json 不写出
    html_files = list(tmp_path.glob("runs/*/html/*.html"))
    assert len(html_files) == 1
    assert not list(tmp_path.glob("runs/*/rankings.json"))


def test_collect_normal_still_writes(tmp_path):
    from amazon_es_bestseller.collection.detail import collect_details
    session = _FakeSession(200, NORMAL_HTML)
    details = collect_details(["B008YETL18"], session, str(tmp_path))
    assert details[0]["access_state"] == "NORMAL"
    assert (tmp_path / "details.json").exists()


class _FakeSessionFlaky:
    """goto 按 ASIN 抛异常（模拟瞬时加载超时）或返回固定状态码/HTML。"""

    def __init__(self, status, html, fail_on=()):
        self._status = status
        self._html = html
        self._fail_on = set(fail_on)
        self.goto_calls = []
        self.page = _FakePage(html)

    def goto(self, url):
        self.goto_calls.append(url)
        if url.rsplit("/", 1)[-1] in self._fail_on:
            raise TimeoutError("Page.goto: Timeout 45000ms exceeded.")
        return self._status

    def wait_for_product_page(self):
        pass

    def wait_for_price_text(self):
        pass

    def wait_between_requests(self):
        pass


def test_collect_details_resume_from_existing_html(tmp_path):
    """断点续采：已落盘非受限 HTML → 离线恢复，不再发请求（幂等）。"""
    from amazon_es_bestseller.collection.detail import collect_details
    (tmp_path / "html").mkdir()
    (tmp_path / "html" / "B008YETL18.html").write_text(NORMAL_HTML, encoding="utf-8")
    # 若 resume 失效去请求，goto 对该 ASIN 必抛 → 测试即失败
    session = _FakeSessionFlaky(200, NORMAL_HTML, fail_on={"B008YETL18"})
    details = collect_details(["B008YETL18"], session, str(tmp_path))
    assert len(details) == 1
    assert details[0]["resumed_from_html"] is True
    assert details[0]["access_state"] == "NORMAL"
    assert session.goto_calls == []          # 未发任何请求
    assert (tmp_path / "details.json").exists()


def test_collect_details_resume_detects_captcha_html(tmp_path):
    """断点续采发现已落盘证据受限 → AccessStopError，不把挑战页当成功恢复。"""
    from amazon_es_bestseller.collection.detail import collect_details
    (tmp_path / "html").mkdir()
    (tmp_path / "html" / "B008YETL18.html").write_text(CHALLENGE_HTML, encoding="utf-8")
    with pytest.raises(AccessStopError) as ei:
        collect_details(["B008YETL18"], _FakeSession(200, NORMAL_HTML), str(tmp_path))
    assert "已落盘证据受限" in str(ei.value)
    assert not (tmp_path / "details.json").exists()


def test_collect_details_resume_rejects_normal_non_product_html(tmp_path):
    from amazon_es_bestseller.collection.detail import collect_details
    (tmp_path / "html").mkdir()
    (tmp_path / "html" / "B008YETL18.html").write_text("<html><body>hola</body></html>", encoding="utf-8")
    with pytest.raises(AccessStopError, match="校验失败"):
        collect_details(["B008YETL18"], _FakeSession(200, NORMAL_HTML), str(tmp_path))


def test_collect_details_timeout_isolates_and_continues(tmp_path):
    """失败隔离：单个 ASIN goto 超时 → 记失败继续，不毁整批；失败页无证据落盘。"""
    from amazon_es_bestseller.collection.detail import collect_details
    session = _FakeSessionFlaky(200, NORMAL_HTML, fail_on={"B0CK2B7GW5"})
    details = collect_details(["B0CK2B7GW5", "B008YETL18"], session, str(tmp_path))
    assert [d["asin"] for d in details] == ["B008YETL18"]   # 超时 ASIN 隔离
    assert len(session.goto_calls) == 2                      # 第二个照常请求
    assert not (tmp_path / "html" / "B0CK2B7GW5.html").exists()  # 失败页无落盘
    assert (tmp_path / "html" / "B008YETL18.html").exists()
    assert (tmp_path / "details.json").exists()


def test_browser_wait_helpers_use_bounded_render_delay_without_dom_block(tmp_path, monkeypatch):
    from amazon_es_bestseller.access import browser

    class UnresponsivePage:
        def wait_for_selector(self, *args, **kwargs):
            raise AssertionError("must not block on selector protocol")

        def wait_for_function(self, *args, **kwargs):
            raise AssertionError("must not block on function protocol")

    delays = []
    monkeypatch.setattr(browser.time, "sleep", lambda seconds: delays.append(seconds))
    session = browser.BrowserSession()
    session.page = UnresponsivePage()
    session.wait_for_product_page(timeout_ms=20000)
    session.wait_for_price_text(timeout_ms=10000)
    assert delays == [2.0, 1.0]
