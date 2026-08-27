# -*- coding: utf-8 -*-
"""串行浏览器会话（复刻 extract_details.js 的访问纪律）。

约束（ARCHITECTURE §65-§67）：
  - 串行 + 显式延迟，无重试、无 stealth、无 CAPTCHA 绕过；
  - playwright 在 __enter__ 时才导入，纯解析/测试层永不触碰浏览器。
"""
from __future__ import annotations

import time
from typing import Optional


class BrowserSession:
    """sync_playwright 上下文管理器：goto 读取 response.status，等待逻辑复刻 JS。"""

    PAGE_DELAY_SECONDS = 2.0

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self.browser = None
        self.page = None

    def __enter__(self) -> "BrowserSession":
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(headless=self.headless)
        self.page = self.browser.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.browser is not None:
            self.browser.close()
            self.browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        return False

    def goto(self, url: str, timeout_ms: int = 45000) -> Optional[int]:
        """访问 URL，返回 HTTP 状态码（无响应对象时 None）。"""
        resp = self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return resp.status if resp is not None else None

    def wait_for_product_page(self, timeout_ms: int = 20000) -> None:
        """给详情页固定渲染缓冲，避免 DOM 协议等待在失活页面上挂死。"""
        time.sleep(min(max(float(timeout_ms), 0.0) / 1000.0, 2.0))

    def wait_for_price_text(self, timeout_ms: int = 10000) -> None:
        """给价格脚本再留短暂缓冲，不调用可能长期不返回的 DOM 等待。"""
        time.sleep(min(max(float(timeout_ms), 0.0) / 1000.0, 1.0))

    def wait_between_requests(self, delay: Optional[float] = None) -> None:
        """串行采集的显式页间延迟（默认 2.0 秒）。"""
        time.sleep(delay if delay is not None else self.PAGE_DELAY_SECONDS)
