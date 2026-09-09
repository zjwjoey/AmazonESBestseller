# -*- coding: utf-8 -*-
"""Bounded challenge-page recovery adapter.

The browser session may wait for a challenge page to clear or, when explicitly
enabled in a visible session, pause for the user to take over.  It never solves
CAPTCHA or attempts to bypass Amazon access controls.
"""
from __future__ import annotations

from ..models import AccessState


def maybe_wait_for_challenge(session, state: AccessState, html: str, status):
    """Give a real browser session its configured challenge recovery chance."""
    if state is not AccessState.CHALLENGE:
        return state, html
    handler = getattr(session, "wait_for_challenge_clear", None)
    if not callable(handler):
        return state, html
    return handler(html=html, status=status)
