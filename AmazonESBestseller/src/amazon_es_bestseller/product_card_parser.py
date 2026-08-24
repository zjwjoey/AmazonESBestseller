import re
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
        product_url = urljoin(source_url, link.get("href", ""))
        asin_match = ASIN_URL_RE.search(product_url)
        dom_asin = card.get("data-asin") if getattr(card, "get", None) else None
        asin = asin_match.group(1).upper() if asin_match else (dom_asin.upper() if dom_asin else None)
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
            if not re.search(r"\b(?:mil|mill(?:ón|ones))\b", monthly_text, re.IGNORECASE):
                value_match = re.search(r"(\d[\d.,]*)", monthly_text)
                if value_match:
                    try:
                        monthly_value = int(value_match.group(1).replace(".", "").replace(",", ""))
                    except ValueError:
                        monthly_value = None
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
                snapshot_date=now.date().isoformat(),
                snapshot_time=now.time().isoformat(timespec="seconds"),
                root_category_es=context.get("root_category_es", "Hogar y cocina"),
                level2_category_es=context.get("level2_category_es"),
                level3_category_es=context.get("level3_category_es"),
                browse_node_id=context.get("browse_node_id"),
                rank=rank,
                rank_text=rank_text,
                rank_source=rank_source,
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
                prime=_first_text(card, (".a-icon-prime", "[class*='prime']")),
                discount=_first_text(card, (".savingsPercentage", "[class*='discount']")),
                original_price=_first_text(card, (".a-text-price", "[class*='original-price']")),
                current_price=price,
                coupon=_first_text(card, (".coupon", "[class*='coupon']")),
                deal=_first_text(card, (".deal", "[class*='deal']")),
                availability=_first_text(card, (".availability", "[class*='availability']")),
                sponsored="true" if sponsored_node is not None else None,
                badge=_first_text(card, (".a-badge-text", "[class*='badge']")),
                variant_text=_first_text(card, ("[class*='variant']",)),
                delivery_text=_first_text(card, ("[class*='delivery']", "[class*='arrives']")),
                source_url=source_url,
                source_category=context.get("level2_category_es"),
                collected_at=now.isoformat(),
            )
        )
    return records


def build_products(records: list[RankingRecord]) -> list[ProductSummary]:
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
                currency=record.currency,
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
            ("currency", record.currency),
            ("rating", record.rating),
            ("review_count", record.review_count),
            ("monthly_bought_text", record.monthly_bought_text),
            ("image_url", record.image_url),
            ("product_url", record.product_url),
        ):
            if getattr(product, field) is None and value is not None:
                setattr(product, field, value)
    return list(grouped.values())
