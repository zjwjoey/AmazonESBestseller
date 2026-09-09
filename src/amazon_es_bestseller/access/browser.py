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
        # Challenge recovery is deliberately bounded and opt-in for human
        # takeover.  CLI overrides these values without changing fake-session
        # constructors used by offline tests.
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
        """Wait for a challenge page to clear, then optionally ask for takeover.

        Polling uses short sleeps so the browser remains responsive.  A normal
        page after the wait is returned to the caller for parsing; a challenge
        that remains is returned unchanged and the caller's access gate stops
        the run.  Manual assistance is never attempted in headless mode.
        """
        wait_seconds = max(float(getattr(self, "challenge_wait_seconds", 180.0)), 0.0)
        current_html = html
        current_state = detect_access_status(status, current_html)
        if current_state is not AccessState.CHALLENGE:
            return current_state, current_html, False

        _safe_print("检测到 Amazon 挑战页，将等待 %.0f 秒并检查是否自动恢复。" % wait_seconds)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            time.sleep(min(5.0, max(deadline - time.monotonic(), 0.0)))
            try:
                current_html = self._stable_page_content(timeout_seconds=5.0)
            except DeliveryLocationError:
                continue
            # A refreshed document has no reliable relationship to the
            # initial response status.  Its HTML is the evidence for the
            # final effective state; challenge markers remain authoritative.
            current_state = detect_access_status(200, current_html)
            if current_state is AccessState.NORMAL:
                _safe_print("Amazon 挑战页已自动恢复，继续采集。")
                return current_state, current_html, True

        if getattr(self, "manual_assist", False):
            if self.headless:
                _safe_print("当前为无头浏览器，无法进行人工接管；请使用 --headful --manual-assist。")
            else:
                _safe_print("请在可见浏览器中人工完成 Amazon 挑战；程序不会替你识别或输入验证码。")
                try:
                    input("After taking over the visible browser, press Enter to recheck: ")
                except (EOFError, KeyboardInterrupt):
                    return current_state, current_html
                try:
                    current_html = self._stable_page_content(timeout_seconds=10.0)
                except DeliveryLocationError:
                    return current_state, current_html
                current_state = detect_access_status(200, current_html)
                if current_state is AccessState.NORMAL:
                    _safe_print("人工协助后页面已恢复，继续采集。")
                    return current_state, current_html, True
        _safe_print("等待后 Amazon 仍返回挑战页，按访问安全策略停止。")
        return current_state, current_html, False
