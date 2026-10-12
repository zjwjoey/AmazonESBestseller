# Opt-in URL-first incremental collection

The original full run is paused, not modified or deleted. The incremental
executor creates an independent output and binds the original checkpoint,
metadata, reviewed plan, baseline ASIN scope and detail data by SHA-256.
Both output directories are process-locked while the incremental executor runs.

`tools/incremental_link_collection.py` retains the serial visible Playwright
session, conservative pacing and human-only access-stop recovery from
`LinkFirstRunner`. It reuses the original browser profile, not a new identity.
It does not call a translation provider or extract ASINs from ranking cards.

Previously completed ranking segments and detail results are copied and
hash-verified, with their original observation times. They are not requested
again. A saved, completed normal capture interrupted before parsing can be
parsed offline. Snapshot records and original evidence remain immutable.

For each unconfirmed ranking link, a CURRENT detail navigation is still
required to confirm the selected child ASIN and obtain volatile values.
Thus incremental reuse does **not** promise to eliminate all detail requests.
The current full raw page is preserved and parsed for comparison: unknown
attributes, duplicate-key order and bullet arrays cannot be dropped to make a
cache reusable. A source QA discrepancy, identity conflict, missing evidence
or changed stable content selects full CURRENT detail instead of cache reuse.
Historical HTML hash drift stops the task rather than becoming a silent miss.

Historical stable fields are eligible only after offline reparsing their saved
HTML with the current parser and checking exact stable-content correspondence.
Current price, rating, review counts, monthly-bought values, availability and
current ranking contexts are never inherited from history. Current raw arrays
keep their BSR/review entries and all new attributes. Human notes stay by ASIN.
Each decision includes provenance and distinct current/historical observations.

`incremental_progress.json` distinguishes preserved fresh results, validated
stable reuse and full current detail. Final Source Gate is not weakened;
collection completion is not a declaration of formal research release.
