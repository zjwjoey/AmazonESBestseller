# V4 proposed T1 diagnostic scope

Status: PENDING explicit approval. No V4 LIVE capture has started.

Stage: T1 only. Maximum 50 charged requests, 20 PAGE, 1 hour; serial >=3 seconds, category cooldown 0. Approval activation must bind an absolute expiry and the final enabled plan SHA.

Exact entry URLs:

1. BEST_SELLERS / BESTSELLER_SALES_RANKING: https://www.amazon.es/gp/bestsellers
2. NEW_RELEASES / NEW_RELEASE_SALES_RANKING: https://www.amazon.es/gp/new-releases
3. DEALS / DEALS_ZONE: https://www.amazon.es/deals
4. DEALS / LIGHTNING_DEAL_FILTER: https://www.amazon.es/deals?bubble-id=deals-collection-lightning-deals
5. CUSTOMER_FAVORITES / CUSTOMERS_MOST_LOVED_CURATED_ZONE: https://www.amazon.es/gp/browse.html?node=205103589031&ref_=nav_em_nav_CML_0_2_24_1
6. CUSTOMER_FAVORITES / CUSTOMER_FAVORITES_CATEGORY_BROWSE: https://www.amazon.es/b?ie=UTF8&node=88626813031
7. CUSTOMER_FAVORITES / CUSTOMER_FAVORITES_FILTERED_SEARCH: https://www.amazon.es/s?i=kitchen&srs=88626813031&rh=n%3A88626813031&s=popularity-rank&fs=true&ref=lp_88626813031_sar
8. AMAZON_HAUL / HAUL_LOW_PRICE_ZONE: https://www.amazon.es/-/en/haul/store

Only actual native owner-bound BEST/NEW ACP is currently replayable. New-source native requests are aborted and saved for protocol review; no guessed requests, pagination or extra source is approved. Root visits do not prove pagination or capacity. Unknown frames are retained and diagnosed offline.

Stop on 403/429, Challenge/CAPTCHA/Robot Check/access denied, UNKNOWN/NETWORK_ERROR, either non-NORMAL gate, expired approval, exhausted request/PAGE/duration/raw budget, or unreviewed redirect. Preserve all evidence. No access-control circumvention or automatic gate reset.

Approval is separate from prior V3 tests. T2 capacity validation and T3 fresh 30000-candidate collection remain unapproved and disabled. Final LIVE acceptance requires central full-DOM/receipt review and actual native protocol evidence, not summary booleans.

Pending generated task proposal: run v4_t1_056014fcc8b9; isolated runtime parent F:/AmazonESBestseller/runtime/highspeed_v4_fresh_batches. Existing 30026 and all historical origins remain outside new LIVE totals.
