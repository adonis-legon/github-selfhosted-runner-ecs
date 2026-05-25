"""
Property-based tests for label-to-architecture mapping.

Feature: github-runners-ecs
Property 1: Label-to-architecture mapping correctness

Validates: Requirements 2.3, 2.4, 2.6, 4.5, 4.6
"""

import importlib
import sys
from pathlib import Path

from hypothesis import given, settings, assume
from hypothesis.strategies import (
    lists,
    text,
    sampled_from,
    one_of,
    just,
    composite,
)

# 'lambda' is a Python keyword, so we cannot use a normal import.
_lambda_dir = str(Path(__file__).resolve().parents[2] / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

_webhook_handler = importlib.import_module("webhook_handler")
parse_labels = _webhook_handler.parse_labels

# --- Strategies ---

# Characters that won't accidentally form architecture patterns
_safe_chars = "bcdefghijklnopqstuvwyz0123456789-_"

# Labels that should never match an architecture pattern
_non_arch_label = text(alphabet=_safe_chars, min_size=1, max_size=20).filter(
    lambda s: "x64" not in s.lower()
    and "x86_64" not in s.lower()
    and "arm64" not in s.lower()
    and "arm" not in s.lower()
)

# x86 architecture indicators
_x86_indicators = sampled_from(["x64", "X64", "x86_64", "X86_64"])

# ARM architecture indicators
_arm_indicators = sampled_from(["arm64", "ARM64", "arm", "ARM"])


@composite
def label_containing(draw, indicator_strategy):
    """Generate a label that contains an architecture indicator as a substring."""
    indicator = draw(indicator_strategy)
    prefix = draw(text(alphabet="abcdefghijklmnopqrstuvwyz-_", min_size=0, max_size=5))
    suffix = draw(text(alphabet="abcdefghijklmnopqrstuvwyz-_", min_size=0, max_size=5))
    return f"{prefix}{indicator}{suffix}"


# --- Property Tests ---


@settings(max_examples=100)
@given(
    x86_label=label_containing(_x86_indicators),
    other_labels=lists(_non_arch_label, min_size=0, max_size=5),
)
def test_x86_label_returns_x86_64(x86_label: str, other_labels: list[str]):
    """
    **Validates: Requirements 2.3, 4.5**

    For any list of runner labels, if any label contains "x64" or "x86_64"
    (case-insensitive), parse_labels SHALL return "X86_64".
    """
    labels = other_labels + [x86_label]
    assert parse_labels(labels) == "X86_64"


@settings(max_examples=100)
@given(
    arm_label=label_containing(_arm_indicators),
    other_labels=lists(_non_arch_label, min_size=0, max_size=5),
)
def test_arm_label_without_x86_returns_arm64(arm_label: str, other_labels: list[str]):
    """
    **Validates: Requirements 2.4, 4.5**

    For any list of runner labels, if any label contains "arm64" or "arm"
    (case-insensitive) and no x86 label is present, parse_labels SHALL
    return "ARM64".
    """
    # Ensure no x86 pattern sneaks in via other labels
    labels = other_labels + [arm_label]
    for lbl in labels:
        assume("x64" not in lbl.lower() and "x86_64" not in lbl.lower())

    assert parse_labels(labels) == "ARM64"


@settings(max_examples=100)
@given(
    x86_label=label_containing(_x86_indicators),
    arm_label=label_containing(_arm_indicators),
    other_labels=lists(_non_arch_label, min_size=0, max_size=5),
)
def test_x86_takes_precedence_over_arm(
    x86_label: str, arm_label: str, other_labels: list[str]
):
    """
    **Validates: Requirements 2.3, 4.5**

    If conflicting labels are present (both x86 and arm patterns),
    x86_64 takes precedence. parse_labels SHALL return "X86_64".
    """
    labels = other_labels + [x86_label, arm_label]
    assert parse_labels(labels) == "X86_64"


@settings(max_examples=100)
@given(labels=lists(_non_arch_label, min_size=0, max_size=10))
def test_no_arch_labels_returns_none(labels: list[str]):
    """
    **Validates: Requirements 2.6, 4.6**

    If no label matches any architecture pattern, parse_labels SHALL
    return None.
    """
    assert parse_labels(labels) is None
