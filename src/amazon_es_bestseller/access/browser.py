# -*- coding: utf-8 -*-
"""串行浏览器会话（复刻 extract_details.js 的访问纪律）。

约束（ARCHITECTURE §65-§67）：
  - 串行 + 显式延迟，无重试、无 stealth、无 CAPTCHA 绕过；
  - playwright 在 __enter__ 时才导入，纯解析/测试层永不触碰浏览器。
"""
from __future__ import annotations

import time
import re
from typing import Optional

from .detector import detect_access_status, require_normal_access
from .location import (DEFAULT_SPAIN_POSTAL_CODE, DeliveryLocation,
                       DeliveryLocationError, inspect_delivery_location)
from ..models import AccessState


def _safe_print(text: str) -> None:
    """Keep diagnostics usable on narrow Windows console code pages."""
    try:
        print(text)
    except UnicodeEncodeError:
        import sys
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.write(str(text).encode(encoding, errors="replace").decode(encoding) + "\n")


class BrowserSession:
    """sync_playwright 上下文管理器：goto 读取 response.status，等待逻辑复刻 JS。"""

    PAGE_DELAY_SECONDS = 2.0

    def __init__(self, headless: bool = True, profile_dir: Optional[str] = None):
        self.headless = headless
        self.profile_dir = str(profile_dir) if profile_dir is not None else None
        self._playwright = None
        self.browser = None
        self.page = None
        self.context = None
        self._delivery_location_checked = False
        self._delivery_location: Optional[DeliveryLocation] = None
        # Retained for CLI compatibility and evidence metadata.  Challenges
        # are now an immediate stop; no automatic polling or recovery occurs.
        self.challenge_wait_seconds = 180.0
        self.manual_assist = False

    def __enter__(self) -> "BrowserSession":
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        if self.profile_dir:
            # Persistent context reuses the user's browser profile/session without
            # exposing or reading cookies/local storage in application code.
            self.context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir,
                headless=self.headless,
            )
            self.browser = self.context
        else:
            self.browser = self._playwright.chromium.launch(headless=self.headless)
            self.context = self.browser
        self.page = self.context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.browser is not None:
            self.browser.close()
            self.browser = None
            self.context = None
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

    def load_lazy_ranking_content(self, max_scrolls: int = 18,
                                  step_delay: float = 0.7,
                                  stable_rounds: int = 2) -> None:
        """Scroll a bestseller page so Amazon's lazy-loaded cards are rendered.

        Amazon.es root bestseller pages can render cards 1--30 in the initial
        HTML and append ranks 31--50 only after the user scrolls.  The ranking
        parser intentionally remains HTML-only and badge-based, so the browser
        session is responsible for triggering that rendering before ``content``
        is captured.  The loop is bounded, serial and conservative; it never
        attempts to bypass an access challenge.
        """
        if self.page is None:
            return
        max_scrolls = max(1, int(max_scrolls))
        stable_rounds = max(1, int(stable_rounds))

        metrics_script = """
        () => {
          const cards = Array.from(document.querySelectorAll('[id^="p13n-asin-index-"]'));
          const ranks = cards.map(card => {
            const m = (card.innerText || '').match(/#(\\d+)/);
            return m ? Number(m[1]) : null;
          }).filter(Boolean);
          return {
            count: cards.length,
            maxRank: ranks.length ? Math.max(...ranks) : 0,
            scrollY: window.scrollY || 0,
            viewport: window.innerHeight || 0,
            scrollHeight: document.documentElement.scrollHeight || document.body.scrollHeight || 0,
          };
        }
        """
        previous = None
        stable = 0
        for _ in range(max_scrolls):
            try:
                self.page.evaluate("window.scrollBy(0, Math.max((window.innerHeight || 700) * 0.85, 500));")
                time.sleep(max(0.1, float(step_delay)))
                current = self.page.evaluate(metrics_script)
            except Exception:
                # A navigation/challenge race is handled by the normal access
                # gate after HTML capture; do not turn this helper into a retry.
                return
            signature = (current.get("count", 0), current.get("maxRank", 0),
                         current.get("scrollHeight", 0))
            if signature == previous:
                stable += 1
            else:
                stable = 0
            previous = signature
            at_bottom = (current.get("scrollY", 0) + current.get("viewport", 0)
                         >= current.get("scrollHeight", 0) - 24)
            if at_bottom and stable >= stable_rounds:
                break

    def _visible_locator(self, selectors, timeout_seconds: float = 5.0):
        """Return the first visible locator, using a short bounded poll."""
        deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
        while True:
            for selector in selectors:
                locator = self.page.locator(selector).first
                try:
                    if locator.is_visible():
                        return locator
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def _stable_page_content(self, timeout_seconds: float = 10.0) -> str:
        """Read page HTML after transient navigation races settle."""
        deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
        while True:
            try:
                return self.page.content()
            except Exception as exc:
                if time.monotonic() >= deadline:
                    raise DeliveryLocationError(
                        "无法读取 Amazon 配送地点页面内容：%s" % exc
                    ) from exc
                time.sleep(0.25)

    def ensure_spain_delivery(self, postal_code: str = DEFAULT_SPAIN_POSTAL_CODE,
                              timeout_ms: int = 45000) -> DeliveryLocation:
        """Verify the Amazon header destination and set a Spain postal code.

        The check is idempotent for one session.  If the header is not already
        a Spain destination, the normal Amazon location popover is used to
        enter the supplied five-digit postal code, then the header is checked
        again.  Failure to verify the final state stops the run rather than
        collecting prices under an ambiguous destination.
        """
        code = str(postal_code or "").strip()
        if not re.fullmatch(r"\d{5}", code):
            raise DeliveryLocationError(
                "西班牙配送邮编必须是 5 位数字，当前值为 %r" % code)
        if self._delivery_location_checked:
            return self._delivery_location or DeliveryLocation(
                "", True, code)

        status = self.goto("https://www.amazon.es/", timeout_ms=timeout_ms)
        self.wait_for_product_page(timeout_ms=20000)

        # Amazon sometimes returns a normal HTTP 202 shell and fills the
        # header asynchronously a few seconds later.  Poll only for the
        # destination control, with a hard bound; this is not a network-idle
        # wait and cannot hang on a broken page.
        deadline = time.monotonic() + 10.0
        html = self._stable_page_content()
        current = inspect_delivery_location(html)
        while not current.text and time.monotonic() < deadline:
            time.sleep(0.5)
            html = self._stable_page_content()
            current = inspect_delivery_location(html)

        state = detect_access_status(status, html)
        # A rendered Amazon 202 shell is a valid page response once the
        # destination control exists.  Re-evaluate the fully rendered body as
        # a 200-equivalent so challenge markers still stop the run.
        if state.value == "UNKNOWN" and status == 202 and current.text:
            state = detect_access_status(200, html)
        require_normal_access(state, "配送地点检查（Amazon.es 首页）")
        if current.is_spain is not True:
            location_button = self._visible_locator((
                "#nav-global-location-popover-link",
                "#contextualIngressPtLink",
            ))
            if location_button is None:
                raise DeliveryLocationError(
                    "无法打开 Amazon 配送地点设置；当前页未找到配送地点控件。"
                )
            location_button.click(timeout=5000)

            zip_input = self._visible_locator(("#GLUXZipUpdateInput",))
            if zip_input is None:
                raise DeliveryLocationError(
                    "Amazon 配送地点弹窗未提供西班牙邮编输入框。"
                )
            zip_input.fill(code, timeout=5000)
            apply_button = self._visible_locator(("#GLUXZipUpdate",))
            if apply_button is None:
                raise DeliveryLocationError("Amazon 配送地点弹窗未找到邮编确认按钮。")
            apply_button.click(timeout=5000)

            done_button = self._visible_locator((
                "#a-autoid-67",
                "#GLUXConfirmClose",
                "#a-autoid-67-announce",
            ))
            if done_button is None:
                raise DeliveryLocationError(
                    "Amazon 未确认邮编 %s，无法完成配送地点切换。" % code
                )
            done_button.click(timeout=5000)
            self.wait_for_product_page(timeout_ms=5000)

            # Amazon updates the ingress header asynchronously after closing
            # the popover.  Poll briefly, but keep the bound finite so a stale
            # or unresponsive page cannot hang a collection run.
            deadline = time.monotonic() + 5.0
            while True:
                current = inspect_delivery_location(self._stable_page_content())
                if current.is_spain is True or time.monotonic() >= deadline:
                    break
                time.sleep(0.2)

        if current.is_spain is not True:
            raise DeliveryLocationError(
                "配送地点检查失败：Amazon 当前显示 %r，未能确认已切换到西班牙邮编 %s。"
                % (current.text, code)
            )
        self._delivery_location_checked = True
        self._delivery_location = current
        return current

    def wait_for_challenge_clear(self, html: str, status=None):
        """Stop immediately on a challenge; recovery requires a new run.

        The visible browser may be inspected by a human after the process
        stops, but this method never polls, submits, or resumes collection.
        """
        current_state = detect_access_status(status, html)
        if current_state is AccessState.CHALLENGE:
            _safe_print("检测到 Amazon 挑战页，立即停止采集；请人工处理后重新启动任务。")
        return current_state, html, False
