# Third-party reference notices

This branch contains compatibility-oriented design work informed by these
public repositories. Their code is not vendored and no runtime dependency on
their services is added:

- [omkarcloud/amazon-scraper](https://github.com/omkarcloud/amazon-scraper)
  (MIT): warm sessions, locale handling, ACP ranking hydration, and explicit
  parser/access failure boundaries were reviewed as reference patterns.
- [browser-act/skills](https://github.com/browser-act/skills)
  (MIT): browser-assisted listing extraction and manual-assist workflow were
  reviewed as a fallback/diagnostics pattern only.
- [asinspotlight/amazon-product-categories](https://github.com/asinspotlight/amazon-product-categories)
  (MIT): category placement identity, resumable traversal, and tree validation
  were reviewed as reference patterns.

The implementation in this repository remains governed by its own `AGENTS.md`,
including conservative Amazon access, no CAPTCHA/access-control bypass, no
proxy/cookie/account rotation, immutable raw evidence, and offline-by-default
tests.
