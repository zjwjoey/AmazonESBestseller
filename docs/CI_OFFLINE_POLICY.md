# Offline CI policy

The GitHub workflow is intentionally an offline behavior gate.  It runs on
Python 3.10, 3.11, and 3.12 because the project declares `requires-python >=3.10`.
The code uses PEP 604 unions and `match` statements, both supported by Python
3.10; CI must not silently narrow that public promise.

CI installs `.[test]` under `constraints/ci.txt`, then runs CLI help,
`compileall src tests`, Ruff, and `pytest -q -rs`.  The constraints are bounded
ranges rather than wheel pins so each runner still obtains its compatible
platform build.  Update the record only after the complete matrix passes.

`AMAZON_ES_OFFLINE=1` and `AMAZON_ES_DISABLE_NETWORK=1` are always set. Tests
must use saved fixtures, fakes, or mock transports; live Amazon collection and
translation-provider/API requests are prohibited. A test that needs live
evidence belongs outside this workflow and must be explicitly reviewed.
