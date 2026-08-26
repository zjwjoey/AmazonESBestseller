# -*- coding: utf-8 -*-
"""访问门禁测试（ARCHITECTURE §6）：非 NORMAL 停止采集，受限页 HTML 保留证据。

全部离线：用 fake session 模拟受限访问，不启动真实浏览器。
"""
import pytest

from amazon_es_bestseller.access.detector import AccessStopError, require_normal_access
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
