# -*- coding: utf-8 -*-
"""Amazon delivery-location detection and Spain destination setup.

Amazon.es can render Spanish content while the active delivery destination is
another country.  That destination changes both availability and the visible
buy-box price, so collection must verify the header destination before reading
product evidence.

The module keeps the detection logic pure and leaves browser interaction to
``BrowserSession``.  It never reads or exports cookies/local storage and does
not attempt to bypass Amazon challenges.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from bs4 import BeautifulSoup


DEFAULT_SPAIN_POSTAL_CODE = "28001"

# These selectors target the active delivery destination in the Amazon header,
# not the footer language/country selector (which may say España independently
# of the actual delivery address).
DELIVERY_LOCATION_SELECTORS = (
    "#nav-global-location-popover-link",
    "#contextualIngressPtLink",
    "#glow-ingress-line2",
    "#glow-ingress-line1",
)


@dataclass(frozen=True)
class DeliveryLocation:
    """Observed delivery destination from the Amazon header."""

    text: str
    is_spain: bool | None
    postal_code: str = ""


class DeliveryLocationError(RuntimeError):
    """Raised when the active destination cannot be changed/verified."""


def _clean(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _fold(text: str) -> str:
    """Case/diacritic-insensitive text used only for marker matching."""
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def extract_delivery_location_text(html: str) -> str:
    """Extract the active header destination from saved page HTML.

    The first matching selector wins.  In particular, we intentionally do not
    fall back to the document-wide text because the footer's ``España`` label
    is not evidence that delivery is configured for Spain.
    """
    soup = BeautifulSoup(html or "", "lxml")
    for selector in DELIVERY_LOCATION_SELECTORS:
        element = soup.select_one(selector)
        if element is not None:
            text = _clean(element.get_text(" ", strip=True))
            if text:
                return text
    return ""


def classify_delivery_location(text: str) -> DeliveryLocation:
    """Classify header text as Spain, non-Spain, or unknown.

    Amazon localizes country names, so common English and Spanish forms are
    recognized.  A five-digit postal code in the ``Enviar a`` header is also a
    positive Spain signal when no conflicting country marker is present.
    """
    clean = _clean(text)
    folded = _fold(clean)
    postal_match = re.search(r"\b(\d{5})\b", clean)
    postal_code = postal_match.group(1) if postal_match else ""

    non_spain_markers = (
        "estados unidos", "united states", "usa", "france", "francia",
        "italia", "italy", "alemania", "germany", "portugal", "reino unido",
        "united kingdom", "mexico", "méxico", "canada",
    )
    if any(marker in folded for marker in (_fold(m) for m in non_spain_markers)):
        return DeliveryLocation(clean, False, postal_code)

    spain_markers = ("espana", "spain", "madrid", "barcelona", "valencia", "sevilla")
    if any(marker in folded for marker in spain_markers):
        return DeliveryLocation(clean, True, postal_code)
    if postal_code and ("enviar a" in folded or "deliver to" in folded):
        return DeliveryLocation(clean, True, postal_code)
    return DeliveryLocation(clean, None, postal_code)


def inspect_delivery_location(html: str) -> DeliveryLocation:
    """Extract and classify the active delivery destination from HTML."""
    return classify_delivery_location(extract_delivery_location_text(html))


def ensure_spain_delivery(session, postal_code: str = DEFAULT_SPAIN_POSTAL_CODE):
    """Call a session's browser-backed Spain-location check when available.

    This small adapter lets collectors remain compatible with offline fake
    sessions used by tests while real ``BrowserSession`` instances enforce the
    location invariant.
    """
    checker = getattr(session, "ensure_spain_delivery", None)
    if callable(checker):
        return checker(postal_code)
    return None
