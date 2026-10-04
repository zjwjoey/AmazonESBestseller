from amazon_es_bestseller.transport.failures import FailureKind, classify_failure
from amazon_es_bestseller.transport.fallback import (
    BrowserFallbackAdapter, FallbackState,
)


def test_transport_failure_taxonomy_stops_access_restrictions():
    assert classify_failure(status_code=429, body="").kind is FailureKind.RATE_LIMIT
    assert classify_failure(status_code=403, body="Robot Check").kind is FailureKind.BOT_BLOCK
    assert classify_failure(error=TimeoutError("timeout")).should_retry() is True
    assert classify_failure(status_code=429, body="").should_retry() is False


def test_browser_fallback_is_manual_contract_only():
    adapter = BrowserFallbackAdapter()
    decision = adapter.request_assistance("challenge")
    assert decision.state is FallbackState.BROWSER_ASSIST_REQUIRED
    assert decision.manual_action_required is True
    assert adapter.mark_recovered().state is FallbackState.RECOVERED
