"""Offline evidence reconciliation; never instantiates a live cache or provider."""

from .report import reconcile

__all__ = ["reconcile"]
