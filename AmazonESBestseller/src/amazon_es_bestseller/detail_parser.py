import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup


_MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


@dataclass(frozen=True)
class ProductDetail:
    asin: str | None
    parent_asin: str | None
    details_json: dict[str, object]
    details: str | None
    specification: str | None
    date_first_available: str | None
    date_first_available_raw: str | None
    candidate_fields: dict[str, str | None]


def _text(node) -> str | None:
    if node is None:
        return None
    value = " ".join(node.get_text(" ", strip=True).split())
    return value or None


def _key(label: str) -> str:
    lowered = label.lower().strip().rstrip(":")
    aliases = {
        "marca": "brand",
        "brand": "brand",
        "material": "material",
        "país de origen": "country_of_origin",
        "pais de origen": "country_of_origin",
    }
    if lowered in aliases:
        return aliases[lowered]
    ascii_label = "".join(
        character
        for character in unicodedata.normalize("NFKD", lowered)
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", "_", ascii_label).strip("_")


def _is_first_available_label(label: str) -> bool:
    normalized = _key(label)
    return normalized in {
        "fecha_de_disponibilidad",
        "first_available_date",
        "producto_en_amazon_es_desde",
    }


def _detail_label_values(soup: BeautifulSoup):
    for item in soup.select("#detailBullets_feature_div li"):
        label_node = item.select_one(".a-text-bold")
        label = _text(label_node)
        value = None
        if label_node:
            sibling_values = [
                _text(node)
                for node in label_node.find_all_next("span", limit=1)
                if node is not label_node
            ]
            value = next((item for item in sibling_values if item), None)
        else:
            direct_spans = item.find_all("span", recursive=False)
            if len(direct_spans) == 2:
                label, value = (_text(direct_spans[0]), _text(direct_spans[1]))
        if value:
            yield label, value
    for item in soup.select("[id^='productDetails_'] tr"):
        label = _text(item.find("th"))
        value = _text(item.find("td"))
        if label and value:
            yield label, value


def _parse_spanish_date(raw: str | None) -> str | None:
    if not raw:
        return None
    match = re.search(r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})", raw.lower())
    if not match:
        return None
    day, month_name, year = match.groups()
    month = _MONTHS_ES.get(month_name)
    if month is None:
        return None
    try:
        return date(int(year), month, int(day)).isoformat()
    except ValueError:
        return None


def _readable_details(values: dict[str, object]) -> str | None:
    if not values:
        return None
    parts = []
    for key, value in values.items():
        rendered = ", ".join(value) if isinstance(value, list) else str(value)
        parts.append(f"{key}: {rendered}")
    return "; ".join(parts)


def parse_detail_page(html: str, asin: str | None) -> ProductDetail:
    """Extract visible product facts from saved detail HTML without network calls."""
    soup = BeautifulSoup(html, "lxml")
    details: dict[str, object] = {}
    brand = _text(soup.select_one("#bylineInfo, [id*='brand']"))
    if brand:
        details["brand"] = brand

    date_raw = None
    for label, value in _detail_label_values(soup):
        normalized = _key(label)
        if _is_first_available_label(label):
            date_raw = value
            continue
        details[normalized] = value

    features = [
        value
        for value in (_text(item) for item in soup.select("#feature-bullets li"))
        if value
    ]
    if features:
        details["features"] = features

    parent_node = soup.select_one("[data-parent-asin], [id*='parentasin'], [id*='parent_asin']")
    parent_asin = parent_node.get("data-parent-asin") if parent_node else None
    if not parent_asin and parent_node:
        parent_asin = _text(parent_node)
    if not parent_asin:
        embedded_parent = re.search(
            r'["\']parentAsin["\']\s*:\s*["\']([A-Z0-9]{10})["\']',
            html,
            re.IGNORECASE,
        )
        parent_asin = embedded_parent.group(1) if embedded_parent else None
    if parent_asin:
        match = re.search(r"\b([A-Z0-9]{10})\b", parent_asin, re.IGNORECASE)
        parent_asin = match.group(1).upper() if match else None

    specification = _text(soup.select_one("[id*='variation'] .selection, [id*='size'] .selection"))
    candidate_fields = {
        "rating": _text(soup.select_one("#acrPopover .a-icon-alt, [class*='rating']")),
        "review_count": _text(soup.select_one("#acrCustomerReviewText, [id*='review']")),
        "seller": _text(soup.select_one("#sellerProfileTriggerId, [id*='seller']")),
        "fulfilled_by": _text(soup.select_one("[id*='merchant-info'], [id*='fulfiller']")),
        "ean": _text(soup.find(string=re.compile(r"EAN", re.IGNORECASE))),
        "gtin": _text(soup.find(string=re.compile(r"GTIN", re.IGNORECASE))),
        "upc": _text(soup.find(string=re.compile(r"UPC", re.IGNORECASE))),
    }
    return ProductDetail(
        asin=asin,
        parent_asin=parent_asin,
        details_json=details,
        details=_readable_details(details),
        specification=specification,
        date_first_available=_parse_spanish_date(date_raw),
        date_first_available_raw=date_raw,
        candidate_fields=candidate_fields,
    )
