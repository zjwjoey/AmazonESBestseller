import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from .models import ProductSummary


@dataclass(frozen=True)
class ImageDownloadResult:
    asin: str
    status: str
    path: str | None
    error: str | None


def _fetch_image(url: str) -> tuple[bytes, str | None]:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=45) as response:  # no retry by design
        return response.read(), response.headers.get_content_type()


def _extension(content_type: str | None) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get((content_type or "").lower(), ".img")


def download_product_images(
    products: list[ProductSummary],
    image_dir: Path,
    *,
    delay_seconds: float = 3.0,
    fetch=_fetch_image,
) -> list[ImageDownloadResult]:
    """Download observed main images serially once, recording every result."""
    image_dir.mkdir(parents=True, exist_ok=True)
    results: list[ImageDownloadResult] = []
    for offset, product in enumerate(products):
        if not product.image_url:
            product.image_download_status = "missing_source_url"
            product.image_download_error = "image_url is empty"
            results.append(ImageDownloadResult(product.asin, "missing_source_url", None, product.image_download_error))
            continue
        if offset and delay_seconds > 0:
            time.sleep(delay_seconds)
        try:
            body, content_type = fetch(product.image_url)
            path = image_dir / f"{product.asin}{_extension(content_type)}"
            path.write_bytes(body)
            product.image_path = f"images/{path.name}"
            product.image_download_status = "downloaded"
            product.image_download_error = None
            results.append(ImageDownloadResult(product.asin, "downloaded", product.image_path, None))
        except Exception as exc:  # One attempt only; preserve the reason for audit.
            product.image_download_status = "failed"
            product.image_download_error = str(exc)[:300]
            results.append(ImageDownloadResult(product.asin, "failed", None, product.image_download_error))
            if "HTTP 403" in product.image_download_error or "HTTP 429" in product.image_download_error:
                break
    return results
