import re

from bs4 import BeautifulSoup

from .models import AccessResult, AccessState


_CHALLENGE_MARKERS = (
    "robot check",
    "captcha",
    "type the characters",
    "resolver el captcha",
)
_BLOCK_MARKERS = (
    "access denied",
    "访问拒绝",
    "sign in to continue",
    "iniciar sesión para continuar",
    "login required",
)


def visible_text_from_html(html: str | None) -> str:
    """Return user-visible text while excluding hidden widgets and scripts."""
    soup = BeautifulSoup(html or "", "lxml")
    for node in soup.select(
        "script, style, [hidden], [aria-hidden='true'], .aok-hidden, "
        "[style*='display:none'], [style*='display: none']"
    ):
        node.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def detect_access_state(
    title: str | None,
    body: str | None,
    http_status: int | None = None,
) -> AccessResult:
    """Classify a page without attempting recovery or challenge handling."""
    if http_status == 429:
        return AccessResult(AccessState.RATE_LIMITED, "HTTP 429")
    if http_status in {401, 403}:
        return AccessResult(AccessState.BLOCKED, f"HTTP {http_status}")

    text = f"{title or ''}\n{body or ''}".lower()
    for marker in _CHALLENGE_MARKERS:
        if marker in text:
            return AccessResult(AccessState.CHALLENGE, f"marker: {marker}")
    for marker in _BLOCK_MARKERS:
        if marker in text:
            return AccessResult(AccessState.BLOCKED, f"marker: {marker}")

    if http_status is None:
        return AccessResult(AccessState.UNKNOWN, "missing HTTP status")
    if not 200 <= http_status < 400:
        return AccessResult(AccessState.UNKNOWN, f"HTTP {http_status}")
    if not re.search(r"\S", text):
        return AccessResult(AccessState.UNKNOWN, "empty page")
    return AccessResult(AccessState.NORMAL)
