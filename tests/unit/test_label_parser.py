"""Unit tests for the parse_labels function."""

import importlib
import sys
from pathlib import Path

# 'lambda' is a Python keyword, so we cannot use a normal import.
_lambda_dir = str(Path(__file__).resolve().parents[2] / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

_webhook_handler = importlib.import_module("webhook_handler")
parse_labels = _webhook_handler.parse_labels


class TestParseLabelsx86:
    """Tests for x86_64 architecture detection."""

    def test_x64_label(self):
        assert parse_labels(["self-hosted", "linux", "x64"]) == "X86_64"

    def test_x86_64_label(self):
        assert parse_labels(["self-hosted", "x86_64"]) == "X86_64"

    def test_x64_case_insensitive(self):
        assert parse_labels(["X64"]) == "X86_64"

    def test_x86_64_case_insensitive(self):
        assert parse_labels(["X86_64"]) == "X86_64"

    def test_x64_substring_in_label(self):
        assert parse_labels(["my-x64-runner"]) == "X86_64"


class TestParseLabelsArm:
    """Tests for ARM64 architecture detection."""

    def test_arm64_label(self):
        assert parse_labels(["self-hosted", "linux", "arm64"]) == "ARM64"

    def test_arm_label(self):
        assert parse_labels(["self-hosted", "arm"]) == "ARM64"

    def test_arm64_case_insensitive(self):
        assert parse_labels(["ARM64"]) == "ARM64"

    def test_arm_case_insensitive(self):
        assert parse_labels(["ARM"]) == "ARM64"

    def test_arm_substring_in_label(self):
        assert parse_labels(["my-arm-runner"]) == "ARM64"


class TestParseLabelsNone:
    """Tests for no architecture match."""

    def test_empty_labels(self):
        assert parse_labels([]) is None

    def test_only_self_hosted_and_linux(self):
        assert parse_labels(["self-hosted", "linux"]) is None

    def test_unrecognized_labels(self):
        assert parse_labels(["self-hosted", "linux", "custom-runner"]) is None


class TestParseLabelsPrecedence:
    """Tests for x86_64 precedence over ARM64."""

    def test_x64_takes_precedence_over_arm64(self):
        assert parse_labels(["x64", "arm64"]) == "X86_64"

    def test_x86_64_takes_precedence_over_arm(self):
        assert parse_labels(["arm", "x86_64"]) == "X86_64"

    def test_x64_precedence_with_other_labels(self):
        assert parse_labels(["self-hosted", "linux", "arm64", "x64"]) == "X86_64"
