"""
Property-based tests for webhook signature validation.

Feature: github-runners-ecs
Property 2: Webhook signature validation round-trip

Validates: Requirements 4.3, 4.4
"""

import hashlib
import hmac
import importlib
import sys
from pathlib import Path

from hypothesis import given, settings, assume
from hypothesis.strategies import binary, text

# 'lambda' is a Python keyword, so we cannot use a normal import.
# Add the lambda directory to sys.path and import the module via importlib.
_lambda_dir = str(Path(__file__).resolve().parents[2] / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

_webhook_handler = importlib.import_module("webhook_handler")
validate_signature = _webhook_handler.validate_signature


@settings(max_examples=100)
@given(
    payload=binary(),
    secret=text(min_size=1),
)
def test_signature_roundtrip_returns_true(payload: bytes, secret: str):
    """
    **Validates: Requirements 4.3, 4.4**

    For any payload (arbitrary bytes) and any secret (non-empty string),
    computing the HMAC-SHA256 signature of the payload with the secret
    and then validating that signature with validate_signature SHALL return True.
    """
    computed = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()
    github_signature = f"sha256={computed}"

    assert validate_signature(payload, github_signature, secret) is True


@settings(max_examples=100)
@given(
    payload=binary(),
    secret=text(min_size=1),
    tampered=text(min_size=1, alphabet="0123456789abcdef"),
)
def test_tampered_signature_returns_false(payload: bytes, secret: str, tampered: str):
    """
    **Validates: Requirements 4.3, 4.4**

    For any payload and any signature that was NOT computed from that payload
    with the given secret, validate_signature SHALL return False.
    """
    # Compute the correct signature
    correct = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()

    # Ensure the tampered hex string is actually different from the correct one
    assume(tampered != correct)

    github_signature = f"sha256={tampered}"

    assert validate_signature(payload, github_signature, secret) is False


@settings(max_examples=100)
@given(
    payload=binary(),
    secret=text(min_size=1),
    wrong_secret=text(min_size=1),
)
def test_wrong_secret_returns_false(payload: bytes, secret: str, wrong_secret: str):
    """
    **Validates: Requirements 4.3, 4.4**

    Computing signature with one secret and validating with a different
    secret SHALL return False.
    """
    assume(wrong_secret != secret)

    # Compute signature with the wrong secret
    wrong_sig = hmac.new(
        key=wrong_secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()
    github_signature = f"sha256={wrong_sig}"

    # Validating with the correct secret should fail
    assert validate_signature(payload, github_signature, secret) is False
