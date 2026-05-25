"""Unit tests for payload validation function."""

import importlib
import sys
from pathlib import Path

# 'lambda' is a Python keyword, so we cannot use a normal import.
_lambda_dir = str(Path(__file__).resolve().parents[2] / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

_webhook_handler = importlib.import_module("webhook_handler")
validate_payload = _webhook_handler.validate_payload


class TestValidatePayloadValid:
    """Tests for valid payloads that should pass validation."""

    def test_valid_minimal_payload(self):
        payload = {
            "action": "queued",
            "workflow_job": {"labels": ["self-hosted", "linux", "x64"]},
            "repository": {"full_name": "org/repo"},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is True
        assert errors == []

    def test_valid_payload_with_extra_fields(self):
        payload = {
            "action": "queued",
            "workflow_job": {
                "id": 123,
                "labels": ["self-hosted"],
                "run_id": 456,
            },
            "repository": {"full_name": "org/repo", "private": True},
            "organization": {"login": "org"},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is True
        assert errors == []

    def test_valid_payload_single_label(self):
        payload = {
            "action": "queued",
            "workflow_job": {"labels": ["arm64"]},
            "repository": {"full_name": "myorg/myrepo"},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is True
        assert errors == []


class TestValidatePayloadAction:
    """Tests for action field validation."""

    def test_missing_action(self):
        payload = {
            "workflow_job": {"labels": ["self-hosted"]},
            "repository": {"full_name": "org/repo"},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("'action'" in e for e in errors)

    def test_wrong_action(self):
        payload = {
            "action": "completed",
            "workflow_job": {"labels": ["self-hosted"]},
            "repository": {"full_name": "org/repo"},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("'queued'" in e and "'completed'" in e for e in errors)


class TestValidatePayloadWorkflowJob:
    """Tests for workflow_job field validation."""

    def test_missing_workflow_job(self):
        payload = {
            "action": "queued",
            "repository": {"full_name": "org/repo"},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("'workflow_job'" in e for e in errors)

    def test_workflow_job_not_dict(self):
        payload = {
            "action": "queued",
            "workflow_job": "not a dict",
            "repository": {"full_name": "org/repo"},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("'workflow_job'" in e and "object" in e for e in errors)

    def test_missing_labels(self):
        payload = {
            "action": "queued",
            "workflow_job": {"id": 123},
            "repository": {"full_name": "org/repo"},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("'workflow_job.labels'" in e for e in errors)

    def test_labels_not_list(self):
        payload = {
            "action": "queued",
            "workflow_job": {"labels": "not a list"},
            "repository": {"full_name": "org/repo"},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("'workflow_job.labels'" in e and "array" in e for e in errors)

    def test_labels_empty_list(self):
        payload = {
            "action": "queued",
            "workflow_job": {"labels": []},
            "repository": {"full_name": "org/repo"},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("non-empty" in e and "labels" in e for e in errors)


class TestValidatePayloadRepository:
    """Tests for repository field validation."""

    def test_missing_repository(self):
        payload = {
            "action": "queued",
            "workflow_job": {"labels": ["self-hosted"]},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("'repository'" in e for e in errors)

    def test_repository_not_dict(self):
        payload = {
            "action": "queued",
            "workflow_job": {"labels": ["self-hosted"]},
            "repository": 42,
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("'repository'" in e and "object" in e for e in errors)

    def test_missing_full_name(self):
        payload = {
            "action": "queued",
            "workflow_job": {"labels": ["self-hosted"]},
            "repository": {"id": 123},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("'repository.full_name'" in e for e in errors)

    def test_full_name_not_string(self):
        payload = {
            "action": "queued",
            "workflow_job": {"labels": ["self-hosted"]},
            "repository": {"full_name": 123},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("'repository.full_name'" in e and "string" in e for e in errors)

    def test_full_name_empty_string(self):
        payload = {
            "action": "queued",
            "workflow_job": {"labels": ["self-hosted"]},
            "repository": {"full_name": ""},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert any("non-empty" in e and "full_name" in e for e in errors)


class TestValidatePayloadMultipleErrors:
    """Tests for payloads with multiple validation failures."""

    def test_empty_payload(self):
        payload = {}
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert len(errors) == 3  # action, workflow_job, repository

    def test_all_fields_invalid(self):
        payload = {
            "action": "in_progress",
            "workflow_job": {"labels": []},
            "repository": {"full_name": ""},
        }
        is_valid, errors = validate_payload(payload)
        assert is_valid is False
        assert len(errors) == 3
