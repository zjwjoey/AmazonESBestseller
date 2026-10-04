"""Transport contracts for Amazon source observations.

Playwright remains the primary transport.  The other adapters are optional
and deliberately expose failures instead of silently changing access policy.
"""

from .base import AmazonTransport, TransportResponse, locale_observation, raw_response_evidence
from .failures import FailureKind, TransportFailure, classify_failure

__all__ = [
    "AmazonTransport", "TransportResponse", "locale_observation", "FailureKind", "TransportFailure",
    "classify_failure", "raw_response_evidence",
]
