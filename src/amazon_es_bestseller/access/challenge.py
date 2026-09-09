# -*- coding: utf-8 -*-
"""Bounded challenge-page recovery adapter.

The browser session may wait for a challenge page to clear or, when explicitly
enabled in a visible session, pause for the user to take over.  It never solves
CAPTCHA or attempts to bypass Amazon access controls.
"""
from __future__ import annotations

from ..models import AccessState


def maybe_wait_for_challenge(session, state: AccessState, html: str, status):
    """Give a real browser session its configured challenge recovery chance.

    The third return value records whether the initial challenge transitioned
    to a final NORMAL page.  Accept two-value handlers for compatibility with
    older fake sessions and adapters.
    """
    if state is not AccessState.CHALLENGE:
        return state, html, False
    handler = getattr(session, "wait_for_challenge_clear", None)
    if not callable(handler):
        return state, html, False
    result = handler(html=html, status=status)
    if not isinstance(result, tuple):
        raise TypeError("挑战恢复处理器必须返回 (state, html) 或 (state, html, recovered)")
    if len(result) == 3:
        final_state, final_html, recovered = result
    elif len(result) == 2:
        final_state, final_html = result
        recovered = final_state is AccessState.NORMAL
    else:
        raise ValueError("挑战恢复处理器返回值长度无效")
    return final_state, final_html, bool(recovered)
