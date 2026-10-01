# -*- coding: utf-8 -*-
"""Challenge-page access gate adapter.

The production ``BrowserSession`` implements an immediate stop.  The small
adapter remains injectable so offline fake sessions can exercise evidence and
recovery-state parsing without launching a browser.
"""
from __future__ import annotations

from ..models import AccessState


def maybe_wait_for_challenge(session, state: AccessState, html: str, status):
    """Delegate to the session's policy handler and preserve its result."""
    if state is not AccessState.CHALLENGE:
        return state, html, False
    handler = getattr(session, "wait_for_challenge_clear", None)
    if not callable(handler):
        return state, html, False
    result = handler(html=html, status=status)
    if not isinstance(result, tuple):
        raise TypeError("挑战处理器必须返回 (state, html) 或 (state, html, recovered)")
    if len(result) == 3:
        final_state, final_html, recovered = result
    elif len(result) == 2:
        final_state, final_html = result
        recovered = final_state is AccessState.NORMAL
    else:
        raise ValueError("挑战处理器返回值长度无效")
    return final_state, final_html, bool(recovered)
