import argparse
import csv
from pathlib import Path

from amazon_es_bestseller.image_downloader import download_product_images
from amazon_es_bestseller.models import ProductSummary


def main() -> int:
    parser = argparse.ArgumentParser(description="Serially download observed product main images")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--delay", type=float, default=3.0)
    args = parser.parse_args()
    products_path = args.run_dir / "products.csv"
    with products_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    products = [ProductSummary(asin=row["asin"], image_url=row.get("image_url") or None) for row in rows]
    results = download_product_images(products, args.run_dir / "images", delay_seconds=args.delay)
    by_asin = {product.asin: product for product in products}
    new_columns = ["image_path", "image_download_status", "image_download_error"]
    fields = list(rows[0]) + [column for column in new_columns if column not in rows[0]]
    for row in rows:
        product = by_asin[row["asin"]]
        for column in new_columns:
            row[column] = getattr(product, column) or ""
    with products_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with (args.run_dir / "image_downloads.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["asin", "status", "path", "error"])
        writer.writeheader()
        writer.writerows(
            {"asin": item.asin, "status": item.status, "path": item.path or "", "error": item.error or ""}
            for item in results
        )
    print({"attempted": len(results), "downloaded": sum(item.status == "downloaded" for item in results)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
