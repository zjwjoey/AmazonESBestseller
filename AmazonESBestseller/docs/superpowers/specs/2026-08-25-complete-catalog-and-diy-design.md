# Complete Catalog and DIY Collection Design

## Goal

Produce two independently auditable Amazon.es datasets: 150 complete Home/Kitchen SKUs and 50 complete DIY/Tools SKUs. A complete SKU has one ranking record context, a product row, parsed detail fields, and a downloaded main image or a recorded image failure.

## Scope

- Reprocess the existing `runs/20260825_215400` Home/Kitchen artifacts offline before making additional requests.
- Visit only the 50 Home/Kitchen product detail URLs that are still missing saved detail pages.
- Download each dataset's existing main image serially from the observed `image_url`; do not revisit product pages to find alternative images.
- Collect DIY/Tools from `https://www.amazon.es/gp/bestsellers/diy`: discover at most five diverse deepest observed category pages, retain 50 unique SKUs, visit every retained SKU detail page, and download every retained SKU main image.

## Data Contract

- `details_json` is valid JSON whose keys are normalized field labels and whose values are field values; labels and values must never be concatenated into a key.
- `brand`, `date_first_available`, and `date_first_available_raw` are copied from correctly parsed detail evidence into `products` when available.
- `parent_asin` stores the observed value. `parent_asin_status` is `confirmed` when it differs from the SKU, `self_reported_unconfirmed` when it equals the SKU, and `not_observed` when absent.
- `image_path`, `image_download_status`, and `image_download_error` record the result for every product row.
- Candidate fields (seller, fulfillment, EAN, GTIN, UPC) appear in `field_availability.csv` with their measured detail-page availability.

## Access and Recovery

- Use one Playwright browser/page for Amazon HTML. Maintain a delay of at least three seconds between request starts.
- Image requests are single-threaded with the same delay; an image failure is recorded once and is never retried automatically.
- Page access restrictions or uncertain navigation stop that active stage immediately. Existing saved evidence remains valid and is never discarded.
- Each continuation has its own evidence directory; outputs identify their source run and do not hide partial completion.

## Outputs

Each category run contains `ranking_records.csv`, `products.csv`, `field_availability.csv`, `images/`, access evidence, and `report.md`. The Home/Kitchen run retains its original ranking snapshot while adding continuation evidence for the 50 missing details and 150 images.
