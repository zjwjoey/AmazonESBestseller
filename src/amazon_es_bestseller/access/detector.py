# -*- coding: utf-8 -*-
"""访问状态检测（docs/ARCHITECTURE.md §5）。

纯函数：根据 HTTP 状态码 + 页面文本判定访问状态，供采集层决定如何应对。
"""
from __future__ import annotations

import re
from typing import Optional

from ..models import AccessState

#: 与 extract_details.js line 48 完全一致：页面前 300 字符命中即判定验证码挑战
CAPTCHA_RE = re.compile(r'Captcha|Type the characters|resolver el captcha', re.IGNORECASE)


class AccessStopError(RuntimeError):
    """访问受限（ARCHITECTURE §6）：按策略停止采集，保留已落盘证据。"""


def require_normal_access(state: "AccessState", context: str) -> None:
    """非 NORMAL 访问状态 → AccessStopError；NORMAL 返回。

    context 应带可定位信息（URL/ASIN + HTTP 状态码），供停止时报错。
    """
    if state is not AccessState.NORMAL:
        raise AccessStopError(
            "访问状态 %s（%s），按策略停止采集" % (state.value, context))


def detect_access_status(status_code: Optional[int], page_text: str = "") -> AccessState:
    """状态码 + 页面文本 → AccessState。

    判定优先级：验证码挑战（文本信号）> 403/429/5xx（状态码）> 200 正常。
    无信号 → UNKNOWN（不臆断）。
    """
    if page_text and CAPTCHA_RE.search(str(page_text)[:300]):
        return AccessState.CHALLENGE
    if status_code == 403:
        return AccessState.BLOCKED
    if status_code == 429:
        return AccessState.RATE_LIMITED
    if status_code is not None and status_code >= 500:
        return AccessState.NETWORK_ERROR
    if status_code == 200:
        return AccessState.NORMAL
    return AccessState.UNKNOWN
