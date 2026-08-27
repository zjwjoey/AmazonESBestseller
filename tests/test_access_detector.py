# -*- coding: utf-8 -*-
"""access/ 模块测试：detector 纯函数 + browser 导入冒烟（绝不联网/不启浏览器）。"""
from amazon_es_bestseller.access.browser import BrowserSession
from amazon_es_bestseller.access.detector import CAPTCHA_RE, detect_access_status
from amazon_es_bestseller.models import AccessState


def test_detect_403_blocked():
    assert detect_access_status(403) == AccessState.BLOCKED


def test_detect_429_rate_limited():
    assert detect_access_status(429) == AccessState.RATE_LIMITED


def test_detect_5xx_network_error():
    assert detect_access_status(500) == AccessState.NETWORK_ERROR
    assert detect_access_status(502) == AccessState.NETWORK_ERROR
    assert detect_access_status(503) == AccessState.NETWORK_ERROR


def test_detect_200_normal():
    assert detect_access_status(200, "Envío GRATIS") == AccessState.NORMAL


def test_detect_captcha_text_challenge():
    # 200 页但含验证码文本 → CHALLENGE（优先级高于状态码）
    assert detect_access_status(200, "Ingrese los caracteres o resolver el captcha") == AccessState.CHALLENGE


def test_detect_captcha_beats_5xx():
    assert detect_access_status(503, "Captcha required") == AccessState.CHALLENGE


def test_detect_no_signal_unknown():
    assert detect_access_status(404) == AccessState.UNKNOWN
    assert detect_access_status(None) == AccessState.UNKNOWN


def test_detect_200_no_text_normal():
    # 200 是明确信号，无文本也算 NORMAL
    assert detect_access_status(200, "") == AccessState.NORMAL


def test_captcha_re_matches_js_variants():
    # 与 extract_details.js line 48 的 /Captcha|Type the characters|resolver el captcha/i 一致
    assert CAPTCHA_RE.search("Type the characters below")
    assert CAPTCHA_RE.search("resolver el captcha")
    assert CAPTCHA_RE.search("Captcha")


def test_captcha_signal_is_detected_beyond_first_300_chars():
    long_text = "x" * 300 + " Captcha en el final"
    assert detect_access_status(200, long_text) == AccessState.CHALLENGE


def test_browser_session_smoke():
    # 冒烟：常量与接口就位，不启动浏览器
    assert BrowserSession.PAGE_DELAY_SECONDS == 2.0
    assert hasattr(BrowserSession, "goto")
    assert hasattr(BrowserSession, "wait_for_product_page")
    assert hasattr(BrowserSession, "wait_between_requests")
