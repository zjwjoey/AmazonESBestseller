# Third-party reference notices

This branch contains compatibility-oriented design work informed by these
public repositories. Their code is not vendored and no runtime dependency on
their services is added:

- [omkarcloud/amazon-scraper](https://github.com/omkarcloud/amazon-scraper)
  (MIT, Copyright Chetan Jain): reviewed `amazon/fetch.py`,
  `amazon/rankings.py`, `amazon/parsers.py`, `amazon/products.py`,
  `amazon/search.py`, `amazon/sites.py`, `amazon/shared.py`,
  `amazon/cache_config.py`, and `amazon/test_parsers.py`. Warm sessions,
  locale handling, ACP ranking hydration, and explicit parser/access failure
  boundaries informed new interfaces; its API server, routes, cache, thread
  fanout, and collapsing detail dictionary were not copied.
- [browser-act/skills](https://github.com/browser-act/skills)
  (MIT, Copyright BrowserAct): reviewed `browser-act/SKILL.md`,
  `solutions/ecommerce/amazon-bestseller-listing/SKILL.md`, and
  `solutions/ecommerce/amazon-bestseller-listing/scripts/extract-bestseller.py`.
  Browser-assisted listing extraction and manual-assist workflow informed the
  fallback/diagnostics boundary only; its Amazon.com-oriented number/price
  parser was not used.
- [asinspotlight/amazon-product-categories](https://github.com/asinspotlight/amazon-product-categories)
  (MIT, Copyright ASINSpotlight): reviewed `README.md`, `crawl.py`, and
  `pyproject.toml`. Category placement identity, resumable traversal, and tree
  validation informed the graph/state model; its API and `api.asinspotlight.com`
  are not dependencies.

The implementation in this repository remains governed by its own `AGENTS.md`,
including conservative Amazon access, no CAPTCHA/access-control bypass, no
proxy/cookie/account rotation, immutable raw evidence, and offline-by-default
tests.
