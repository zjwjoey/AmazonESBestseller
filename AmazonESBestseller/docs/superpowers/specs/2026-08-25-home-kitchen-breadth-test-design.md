# Home and Kitchen Breadth-Test Design

## Goal

Run one bounded Amazon.es field-validation test for `Hogar y cocina`, then stop and report whether a later collector is justified.

## Fixed scope

- Discover and sample 5–10 diverse leaf categories, favouring storage, cleaning, bedding, kitchen tools, bathroom, and other distinct product types when they are present.
- Preserve each ranking page's natural visible depth. Do not request pagination or manufacture a Top 100.
- Select 5–10 detail pages distributed across different sampled leaf categories.
- Use serial, low-frequency navigation. Stop the run immediately on 403, 429, captcha, Robot Check, sign-in requirement, challenge, unknown redirect, or other non-normal access state.
- Do not add retries, concurrency, proxying, stealth, cookie/account rotation, CAPTCHA handling, or private API calls.

## Data contract

### Ranking records

One row per ASIN appearance in a leaf-category Best Sellers ranking. Duplicates across rankings are retained.

`index`, `asin`, `title`, `image_url`, `product_url`, `original_price`, `current_price`, `currency`, `discount_rate`, `category_l1`, `category_l2`, `category_l3`, `leaf_category`, `browse_node_id`, `monthly_bought_raw`, `monthly_bought_min`, `category_rank`, `collected_at`, `ranking_source_url`.

`index` restarts from one for every leaf category and is independent from `category_rank`, which is populated only from an explicitly visible Amazon rank.

`product_url` is canonicalized to `https://www.amazon.es/dp/{asin}` when an ASIN is known. `image_url` is the best page-exposed main-image URL; images are not downloaded.

### Products

One ASIN-keyed product profile, enriched only for selected detail samples: `asin`, `parent_asin`, `title`, `image_url`, `product_url`, `original_price`, `current_price`, `details_json`, `details`, `specification`, `date_first_available`, `date_first_available_raw`, and `collected_at`.

`details_json` preserves structured source-language attributes. `details` is a readable rendering of the same observed attributes. Neither is translated.

### Price and sales rules

`current_price` is the directly displayed sale price. `original_price` is the directly displayed struck-through price. `discount_rate` is computed only from those two prices. Coupon, Prime-exclusive and deal text are observed as candidate fields but never used to calculate price or discount.

`monthly_bought_raw` preserves Amazon's text. `monthly_bought_min` is a lower-bound interpretation only, including `1 mil+ -> 1000`; it is never treated as exact sales volume.

### Field reconnaissance

`field_availability.csv` reports required-field success rates, plus separate candidate-field availability for rating, review count, brand, seller, fulfilled by, Prime, coupon, deal, EAN, GTIN, and UPC. Missing values are null rather than inferred.

## Required artifacts

- `ranking_records.csv`
- `products.csv`
- `field_availability.csv`
- `category_tree.csv` and `category_tree.json`
- `access_events.csv`
- sampled HTML and screenshots for root, category, leaf, and detail pages
- report covering field success, natural ranking depth, pagination observation, ASIN overlap, access stability, and GO / CONDITIONAL GO / NO-GO.

## Completion rule

After one run, stop. Report the observed facts and decision; do not broaden to DIY/tools or a larger collection without a new user decision.
