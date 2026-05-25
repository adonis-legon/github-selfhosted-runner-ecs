"""
Property-based tests for retry logic.

Feature: github-runners-ecs
Property 3: Retry logic respects maximum attempts

Validates: Requirements 3.7
"""

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis.strategies import (
    integers,
    just,
    lists,
    one_of,
    sampled_from,
)

# Add the docker directory to sys.path for importing the retry module.
_docker_dir = str(Path(__file__).resolve().parents[2] / "docker")
if _docker_dir not in sys.path:
    sys.path.insert(0, _docker_dir)

from retry import (
    MAX_RETRIES,
    ResponseType,
    RetryResult,
    execute_with_retry,
)

# --- Strategies ---

# Transient error responses (retryable)
_transient_error = just(ResponseType.TRANSIENT_ERROR)

# Success response
_success = just(ResponseType.SUCCESS)

# Non-retryable error responses
_non_retryable_error = sampled_from([
    ResponseType.AUTH_ERROR,
    ResponseType.FORBIDDEN,
    ResponseType.NOT_FOUND,
])

# Any response type that could follow after the sequence we care about
_any_response = sampled_from([
    ResponseType.SUCCESS,
    ResponseType.TRANSIENT_ERROR,
    ResponseType.AUTH_ERROR,
    ResponseType.FORBIDDEN,
    ResponseType.NOT_FOUND,
])


# --- Property Tests ---


@settings(max_examples=100)
@given(
    num_errors=integers(min_value=0, max_value=2),
    trailing=lists(_any_response, min_size=0, max_size=5),
)
def test_transient_errors_followed_by_success_returns_success(
    num_errors: int, trailing: list
):
    """
    **Validates: Requirements 3.7**

    For any sequence of API responses where the first N responses are
    transient errors (N <= 3) followed by a success response, the retry
    function SHALL return success.

    We test N in [0, 1, 2] because with MAX_RETRIES=3, we have 3 attempts
    total. If N=0, success on first try. If N=1, success on second try.
    If N=2, success on third (final) try.
    """
    # Build response sequence: N transient errors then success, then trailing
    responses = (
        [ResponseType.TRANSIENT_ERROR] * num_errors
        + [ResponseType.SUCCESS]
        + trailing
    )

    result = execute_with_retry(responses)
    assert result.success is True, (
        f"Expected success with {num_errors} transient errors before success, "
        f"but got failure after {result.attempts} attempts"
    )
    assert result.attempts == num_errors + 1


@settings(max_examples=100)
@given(
    extra_errors=integers(min_value=0, max_value=5),
    trailing=lists(_any_response, min_size=0, max_size=5),
)
def test_more_than_max_retries_transient_errors_returns_failure(
    extra_errors: int, trailing: list
):
    """
    **Validates: Requirements 3.7**

    For any sequence where more than 3 consecutive responses are transient
    errors, the retry function SHALL return failure regardless of subsequent
    responses.

    With MAX_RETRIES=3, if all 3 attempts see transient errors, the function
    must fail even if later responses would have been successful.
    """
    # Build response sequence: MAX_RETRIES transient errors + more errors + trailing
    responses = (
        [ResponseType.TRANSIENT_ERROR] * (MAX_RETRIES + extra_errors)
        + trailing
    )

    result = execute_with_retry(responses)
    assert result.success is False, (
        f"Expected failure with {MAX_RETRIES + extra_errors} transient errors, "
        f"but got success after {result.attempts} attempts"
    )
    assert result.attempts == MAX_RETRIES


@settings(max_examples=100)
@given(
    num_errors=integers(min_value=0, max_value=2),
    non_retryable=_non_retryable_error,
    trailing=lists(_any_response, min_size=0, max_size=5),
)
def test_non_retryable_error_causes_immediate_failure(
    num_errors: int, non_retryable: ResponseType, trailing: list
):
    """
    **Validates: Requirements 3.7**

    For any sequence where a non-retryable error (401, 403, 404) is
    encountered, the retry function SHALL return failure immediately
    without further retries, regardless of subsequent responses.
    """
    # Build response sequence: some transient errors then a non-retryable error
    responses = (
        [ResponseType.TRANSIENT_ERROR] * num_errors
        + [non_retryable]
        + trailing
    )

    result = execute_with_retry(responses)
    assert result.success is False, (
        f"Expected failure on non-retryable {non_retryable} after "
        f"{num_errors} transient errors, but got success"
    )
    # Should stop at the non-retryable error (attempt num_errors + 1)
    assert result.attempts == num_errors + 1
    assert result.last_response == non_retryable


@settings(max_examples=100)
@given(
    responses=lists(_transient_error, min_size=MAX_RETRIES, max_size=MAX_RETRIES),
)
def test_exactly_max_retries_transient_errors_returns_failure(
    responses: list,
):
    """
    **Validates: Requirements 3.7**

    When exactly MAX_RETRIES (3) consecutive transient errors occur with
    no success response, the retry function SHALL return failure and SHALL
    have made exactly MAX_RETRIES attempts.
    """
    result = execute_with_retry(responses)
    assert result.success is False
    assert result.attempts == MAX_RETRIES
    assert result.last_response == ResponseType.TRANSIENT_ERROR
