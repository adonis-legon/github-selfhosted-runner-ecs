"""
Retry logic for GitHub API calls.

This module implements the retry pattern used in docker/entrypoint.sh as a
testable Python abstraction. The behavior mirrors the bash implementation:

- MAX_RETRIES = 3
- Retryable conditions: HTTP 5xx, connection errors
- Non-retryable conditions: HTTP 401, 403, 404
- Success condition: HTTP 2xx

Requirements: 3.7
"""

from dataclasses import dataclass
from enum import Enum
from typing import List


class ResponseType(Enum):
    """Types of API responses."""
    SUCCESS = "success"           # HTTP 2xx
    TRANSIENT_ERROR = "transient" # HTTP 5xx or connection failure
    AUTH_ERROR = "auth_error"     # HTTP 401
    FORBIDDEN = "forbidden"       # HTTP 403
    NOT_FOUND = "not_found"       # HTTP 404


# Responses that should trigger a retry
RETRYABLE_RESPONSES = {ResponseType.TRANSIENT_ERROR}

# Responses that should cause immediate failure (no retry)
NON_RETRYABLE_RESPONSES = {
    ResponseType.AUTH_ERROR,
    ResponseType.FORBIDDEN,
    ResponseType.NOT_FOUND,
}

MAX_RETRIES = 3


@dataclass
class RetryResult:
    """Result of a retry operation."""
    success: bool
    attempts: int
    last_response: ResponseType


def execute_with_retry(responses: List[ResponseType]) -> RetryResult:
    """
    Execute a sequence of API responses with retry logic.

    Simulates the retry behavior from docker/entrypoint.sh:
    - Up to MAX_RETRIES (3) attempts total
    - Only transient errors (5xx, connection failures) are retried
    - Non-retryable errors (401, 403, 404) cause immediate failure
    - Success on any attempt returns success

    Args:
        responses: Ordered list of API responses to simulate.
                   Each call consumes the next response in sequence.
                   If responses are exhausted before max retries,
                   the last response is repeated.

    Returns:
        RetryResult with success status, number of attempts made,
        and the last response type encountered.
    """
    if not responses:
        return RetryResult(success=False, attempts=0, last_response=ResponseType.TRANSIENT_ERROR)

    for attempt in range(1, MAX_RETRIES + 1):
        # Get the response for this attempt (use last if exhausted)
        idx = min(attempt - 1, len(responses) - 1)
        response = responses[idx]

        if response == ResponseType.SUCCESS:
            return RetryResult(success=True, attempts=attempt, last_response=response)

        if response in NON_RETRYABLE_RESPONSES:
            return RetryResult(success=False, attempts=attempt, last_response=response)

        # Transient error - retry if we have attempts left
        if attempt == MAX_RETRIES:
            return RetryResult(success=False, attempts=attempt, last_response=response)

    # Should not reach here, but just in case
    return RetryResult(success=False, attempts=MAX_RETRIES, last_response=responses[-1])
