import re
import json
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import ProductSummary, RankingRecord
from .page_inspector import ASIN_URL_RE


def _card_nodes(soup: BeautifulSoup):
    nodes = [
        node
        for node in soup.select('[data-testid*="product-card"], [data-asin]')
        if node.get("data-asin") or node.find("a", href=ASIN_URL_RE)
    ]
    if nodes:
        node_ids = {id(node) for node in nodes}
        return [
            node
            for node in nodes
            if not any(id(parent) in node_ids for parent in node.parents)
        ]
    return [anchor for anchor in soup.find_all("a", href=ASIN_URL_RE)]


def _text(node) -> str | None:
    if node is None:
        return None
    value = " ".join(node.get_text(" ", strip=True).split())
    return value or None


def _first_text(card, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        value = _text(card.select_one(selector))
        if value:
            return value
    return None


def _parse_eur_amount(text: str | None) -> float | None:
    if not text or "€" not in text:
        return None
    match = re.search(r"(\d[\d.]*?(?:,\d{2})?|\d+(?:\.\d{2})?)\s*€", text)
    if not match:
        return None
    value = match.group(1)
    try:
        if "," in value:
            return float(value.replace(".", "").replace(",", "."))
        return float(value)
    except ValueError:
        return None


def _canonical_product_url(asin: str | None, fallback_url: str) -> str:
    return f"https://www.amazon.es/dp/{asin}" if asin else fallback_url


def parse_product_cards(
    html: str,
    source_url: str,
    category_context: dict[str, str | None] | None = None,
) -> list[RankingRecord]:
    soup = BeautifulSoup(html, "lxml")
    context = category_context or {}
    now = datetime.now(timezone.utc)
    records: list[RankingRecord] = []
    seen_ranking_identities: set[tuple[str | None, int | None, str]] = set()
    for card in _card_nodes(soup):
        link = card if getattr(card, "name", None) == "a" else card.find("a", href=ASIN_URL_RE)
        link = link or (card.find("a", href=True) if getattr(card, "find", None) else None)
        if link is None:
            continue
        linked_url = urljoin(source_url, link.get("href", ""))
        asin_match = ASIN_URL_RE.search(linked_url)
        dom_asin = card.get("data-asin") if getattr(card, "get", None) else None
        asin = asin_match.group(1).upper() if asin_match else (dom_asin.upper() if dom_asin else None)
        product_url = _canonical_product_url(asin, linked_url)
        rank_node = card.select_one(".rank, [data-rank]")
        rank_text = _text(rank_node)
        rank_source = "visible_text" if rank_text else None
        rank_match = re.search(r"(?:#|n\.º\s*)(\d+)", rank_text or "", re.IGNORECASE)
        if rank_match is None:
            rank_match = re.search(r"(?:#|n\.º\s*)(\d+)", _text(card) or "", re.IGNORECASE)
            rank_text = rank_match.group(0) if rank_match else None
            rank_source = "visible_text" if rank_match else None
        rank = int(rank_match.group(1)) if rank_match else None
        title = _first_text(
            card,
            (".title", "[data-testid='product-title']", "div[class*='line-clamp']"),
        )
        title = title or _text(link)
        image = card.find("img")
        image_url = image.get("src") if image else None
        price = _first_text(
            card,
            (".price", ".a-price .a-offscreen", "span[class*='p13n-sc-price']", ".a-color-price"),
        )
        rating = _first_text(card, (".rating", ".a-icon-alt"))
        review_count = _first_text(
            card,
            (".review-count", "[aria-label*='opiniones']", ".a-icon-row .a-size-small"),
        )
        if review_count is None:
            rating_node = card.select_one(".a-icon-alt")
            if rating_node is not None:
                next_span = rating_node.find_next("span")
                review_count = _text(next_span)
        card_text = _text(card) or ""
        monthly = re.search(
            r"(\d[\d.,]*(?:\s+(?:mil|mill(?:ón|ones)))?\+?\s+comprados\b[^.]{0,80})",
            card_text,
            re.IGNORECASE,
        )
        monthly_text = monthly.group(1).strip() if monthly else None
        monthly_value = None
        if monthly_text:
            value_match = re.search(r"(\d[\d.,]*)", monthly_text)
            if value_match:
                try:
                    base_value = int(value_match.group(1).replace(".", "").replace(",", ""))
                    monthly_value = (
                        base_value * 1000
                        if re.search(r"\bmil\b", monthly_text, re.IGNORECASE)
                        else base_value
                    )
                except ValueError:
                    monthly_value = None
        original_price_text = _first_text(card, (".a-text-price", "[class*='original-price']"))
        current_price_value = _parse_eur_amount(price)
        original_price_value = _parse_eur_amount(original_price_text)
        discount_rate = None
        if original_price_value and current_price_value is not None:
            discount_rate = round((original_price_value - current_price_value) / original_price_value * 100, 2)
        sponsored_node = (
            card
            if card.get("data-component-type") == "s-sponsored-label"
            else card.select_one("[data-component-type='s-sponsored-label'], [class*='s-sponsored']")
        )
        ranking_identity = (asin, rank, product_url)
        if ranking_identity in seen_ranking_identities:
            continue
        seen_ranking_identities.add(ranking_identity)
        records.append(
            RankingRecord(
                index=len(records) + 1,
                snapshot_date=now.date().isoformat(),
                snapshot_time=now.time().isoformat(timespec="seconds"),
                root_category_es=context.get("root_category_es", "Hogar y cocina"),
                level2_category_es=context.get("level2_category_es"),
                level3_category_es=context.get("level3_category_es"),
                category_l1=context.get("category_l1", "Hogar y cocina"),
                category_l2=context.get("category_l2", context.get("level2_category_es")),
                category_l3=context.get("category_l3", context.get("level3_category_es")),
                leaf_category=context.get("leaf_category", context.get("level3_category_es")),
                browse_node_id=context.get("browse_node_id"),
                rank=rank,
                rank_text=rank_text,
                rank_source=rank_source,
                category_rank=rank,
                asin=asin,
                asin_source=("product_url" if asin_match else "dom_attribute") if asin else None,
                title=title,
                product_url=product_url,
                image_url=image_url,
                price=price,
                currency="EUR" if price and "€" in price else None,
                rating=rating,
                review_count=review_count,
                monthly_bought_text=monthly_text,
                monthly_bought_value=monthly_value,
                monthly_bought_raw=monthly_text,
                monthly_bought_min=monthly_value,
                prime=_first_text(card, (".a-icon-prime", "[class*='prime']")),
                discount=_first_text(card, (".savingsPercentage", "[class*='discount']")),
                original_price=original_price_value,
                current_price=current_price_value,
                discount_rate=discount_rate,
                coupon=_first_text(card, (".coupon", "[class*='coupon']")),
                deal=_first_text(card, (".deal", "[class*='deal']")),
                availability=_first_text(card, (".availability", "[class*='availability']")),
                sponsored="true" if sponsored_node is not None else None,
                badge=_first_text(card, (".a-badge-text", "[class*='badge']")),
                variant_text=_first_text(card, ("[class*='variant']",)),
                delivery_text=_first_text(card, ("[class*='delivery']", "[class*='arrives']")),
                source_url=source_url,
                source_category=context.get("level2_category_es"),
                ranking_source_url=context.get("ranking_source_url", source_url),
                collected_at=now.isoformat(),
            )
        )
    return records


def build_products(records: list[RankingRecord], details_by_asin=None) -> list[ProductSummary]:
    """Collapse ranking appearances to products and enrich only saved detail samples."""
    details_by_asin = details_by_asin or {}
    grouped: dict[str, ProductSummary] = {}
    for record in records:
        if not record.asin:
            continue
        product = grouped.get(record.asin)
        if product is None:
            product = ProductSummary(
                asin=record.asin,
                title_es=record.title,
                price=record.price,
                original_price=record.original_price,
                current_price=record.current_price,
                currency=record.currency,
                discount_rate=record.discount_rate,
                rating=record.rating,
                review_count=record.review_count,
                monthly_bought_text=record.monthly_bought_text,
                image_url=record.image_url,
                product_url=record.product_url,
                first_seen=record.collected_at,
                last_seen=record.collected_at,
                ranking_count=0,
                best_rank=record.rank,
            )
            grouped[record.asin] = product
        product.ranking_count += 1
        product.best_rank = min(
            (rank for rank in (product.best_rank, record.rank) if rank is not None),
            default=None,
        )
        if record.collected_at:
            product.first_seen = min(filter(None, (product.first_seen, record.collected_at)))
            product.last_seen = max(filter(None, (product.last_seen, record.collected_at)))
        for field, value in (
            ("title_es", record.title),
            ("price", record.price),
            ("original_price", record.original_price),
            ("current_price", record.current_price),
            ("currency", record.currency),
            ("discount_rate", record.discount_rate),
            ("rating", record.rating),
            ("review_count", record.review_count),
            ("monthly_bought_text", record.monthly_bought_text),
            ("image_url", record.image_url),
            ("product_url", record.product_url),
        ):
            if getattr(product, field) is None and value is not None:
                setattr(product, field, value)
    for asin, product in grouped.items():
        detail = details_by_asin.get(asin)
        if detail is None:
            continue
        product.parent_asin = detail.parent_asin or product.parent_asin
        product.parent_asin_status = (
            "self_reported_unconfirmed"
            if detail.parent_asin == product.asin
            else "confirmed"
            if detail.parent_asin
            else "not_observed"
        )
        detail_brand = detail.details_json.get("brand")
        if product.brand is None and isinstance(detail_brand, str):
            product.brand = detail_brand
        product.details_json = json.dumps(detail.details_json, ensure_ascii=False, sort_keys=True)
        product.details = detail.details
        product.specification = detail.specification
        product.date_first_available = detail.date_first_available
        product.date_first_available_raw = detail.date_first_available_raw
    return list(grouped.values())
