"""Unit tests for the Lambda webhook handler function.

Tests the full handler orchestration: signature validation → payload parsing
→ label matching → task launching. Mocks AWS services (SSM, ECS) to isolate
handler logic.

Validates: Requirements 4.2, 4.4, 4.6, 4.7, 4.8
"""

import hashlib
import hmac
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 'lambda' is a Python keyword, so we cannot use a normal import.
_lambda_dir = str(Path(__file__).resolve().parents[2] / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

_webhook_handler = importlib.import_module("webhook_handler")
handler = _webhook_handler.handler


# --- Test Helpers ---

WEBHOOK_SECRET = "test-webhook-secret"

ENV_VARS = {
    "CLUSTER_ARN": "arn:aws:ecs:us-east-1:123456789012:cluster/runners",
    "TASK_DEF_X86_ARN": "arn:aws:ecs:us-east-1:123456789012:task-definition/runner-x86:1",
    "TASK_DEF_ARM64_ARN": "arn:aws:ecs:us-east-1:123456789012:task-definition/runner-arm64:1",
    "SUBNETS": "subnet-aaa,subnet-bbb",
    "SECURITY_GROUP": "sg-12345",
    "WEBHOOK_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:/github-runner/webhook-secret-AbCdEf",
}


def _make_signature(payload_bytes: bytes, secret: str = WEBHOOK_SECRET) -> str:
    """Compute a valid HMAC-SHA256 signature for a payload."""
    mac = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"sha256={mac}"


def _valid_payload() -> dict:
    """Return a minimal valid workflow_job queued payload."""
    return {
        "action": "queued",
        "workflow_job": {
            "id": 123456,
            "labels": ["self-hosted", "linux", "x64"],
        },
        "repository": {"full_name": "org/repo"},
    }


def _make_event(payload: dict, secret: str = WEBHOOK_SECRET) -> dict:
    """Build an API Gateway proxy event with a valid signature."""
    body = json.dumps(payload)
    signature = _make_signature(body.encode("utf-8"), secret)
    return {
        "body": body,
        "headers": {"x-hub-signature-256": signature},
    }


# --- Fixtures ---


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Set required environment variables for all tests."""
    for key, value in ENV_VARS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def mock_secretsmanager():
    """Mock Secrets Manager client to return the webhook secret."""
    with patch.object(_webhook_handler, "secretsmanager_client") as mock:
        mock.get_secret_value.return_value = {
            "SecretString": WEBHOOK_SECRET
        }
        yield mock


@pytest.fixture()
def mock_ecs():
    """Mock ECS client for task launch tests."""
    with patch.object(_webhook_handler, "ecs_client") as mock:
        mock.run_task.return_value = {
            "tasks": [
                {
                    "taskArn": "arn:aws:ecs:us-east-1:123456789012:task/runners/abc123"
                }
            ],
            "failures": [],
        }
        yield mock


# --- Test: Valid queued event triggers RunTask (Req 4.2) ---


class TestValidQueuedEvent:
    """Valid queued event triggers RunTask call (Req 4.2)."""

    def test_valid_x64_event_returns_200(self, mock_ecs):
        """A valid queued event with x64 label returns 200."""
        event = _make_event(_valid_payload())
        result = handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["message"] == "Task launched successfully"
        assert body["architecture"] == "X86_64"

    def test_valid_x64_event_calls_run_task(self, mock_ecs):
        """A valid queued event calls ECS RunTask with correct params."""
        event = _make_event(_valid_payload())
        handler(event, None)

        mock_ecs.run_task.assert_called_once_with(
            cluster="arn:aws:ecs:us-east-1:123456789012:cluster/runners",
            taskDefinition="arn:aws:ecs:us-east-1:123456789012:task-definition/runner-x86:1",
            launchType="FARGATE",
            count=1,
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": ["subnet-aaa", "subnet-bbb"],
                    "securityGroups": ["sg-12345"],
                    "assignPublicIp": "ENABLED",
                }
            },
        )

    def test_valid_arm64_event_uses_arm_task_def(self, mock_ecs):
        """A valid queued event with arm64 label uses the ARM64 task definition."""
        payload = _valid_payload()
        payload["workflow_job"]["labels"] = ["self-hosted", "linux", "arm64"]
        event = _make_event(payload)
        result = handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["architecture"] == "ARM64"
        call_kwargs = mock_ecs.run_task.call_args[1]
        assert (
            call_kwargs["taskDefinition"]
            == "arn:aws:ecs:us-east-1:123456789012:task-definition/runner-arm64:1"
        )

    def test_valid_event_returns_task_arns(self, mock_ecs):
        """Response body includes the launched task ARNs."""
        event = _make_event(_valid_payload())
        result = handler(event, None)

        body = json.loads(result["body"])
        assert "taskArns" in body
        assert len(body["taskArns"]) == 1
        assert "abc123" in body["taskArns"][0]


# --- Test: ECS failure returns 500 (Req 4.7) ---


class TestECSFailure:
    """ECS failure returns 500 with error details (Req 4.7)."""

    def test_ecs_exception_returns_500(self, mock_ecs):
        """When ECS RunTask raises an exception, handler returns 500."""
        mock_ecs.run_task.side_effect = Exception("Cluster not found")
        event = _make_event(_valid_payload())
        result = handler(event, None)

        assert result["statusCode"] == 500
        body = json.loads(result["body"])
        assert "error" in body
        assert "ECS task launch failed" in body["error"]

    def test_ecs_exception_includes_error_details(self, mock_ecs):
        """The 500 response includes the specific error message."""
        mock_ecs.run_task.side_effect = Exception(
            "ResourceNotFoundException: cluster runners not found"
        )
        event = _make_event(_valid_payload())
        result = handler(event, None)

        body = json.loads(result["body"])
        assert "details" in body
        assert "ResourceNotFoundException" in body["details"]

    def test_secretsmanager_failure_returns_500(self, mock_secretsmanager):
        """When Secrets Manager fails to retrieve the secret, handler returns 500."""
        mock_secretsmanager.get_secret_value.side_effect = Exception("Access denied")
        event = _make_event(_valid_payload())
        result = handler(event, None)

        assert result["statusCode"] == 500
        body = json.loads(result["body"])
        assert "error" in body


# --- Test: Invalid signature returns 401 (Req 4.4) ---


class TestInvalidSignature:
    """Invalid signature returns 401 (Req 4.4)."""

    def test_wrong_signature_returns_401(self):
        """A request with an incorrect signature is rejected with 401."""
        payload = _valid_payload()
        body = json.dumps(payload)
        event = {
            "body": body,
            "headers": {"x-hub-signature-256": "sha256=deadbeef0000"},
        }
        result = handler(event, None)

        assert result["statusCode"] == 401
        body_resp = json.loads(result["body"])
        assert "Invalid signature" in body_resp["error"]

    def test_missing_signature_returns_401(self):
        """A request with no signature header is rejected with 401."""
        payload = _valid_payload()
        body = json.dumps(payload)
        event = {
            "body": body,
            "headers": {},
        }
        result = handler(event, None)

        assert result["statusCode"] == 401

    def test_malformed_signature_prefix_returns_401(self):
        """A signature without the sha256= prefix is rejected with 401."""
        payload = _valid_payload()
        body = json.dumps(payload)
        event = {
            "body": body,
            "headers": {"x-hub-signature-256": "invalid-prefix-abc123"},
        }
        result = handler(event, None)

        assert result["statusCode"] == 401

    def test_signature_from_different_secret_returns_401(self):
        """A signature computed with a different secret is rejected."""
        payload = _valid_payload()
        body = json.dumps(payload)
        wrong_sig = _make_signature(body.encode("utf-8"), "wrong-secret")
        event = {
            "body": body,
            "headers": {"x-hub-signature-256": wrong_sig},
        }
        result = handler(event, None)

        assert result["statusCode"] == 401


# --- Test: Malformed payload returns 400 (Req 4.8) ---


class TestMalformedPayload:
    """Malformed payload returns 400 (Req 4.8)."""

    def test_invalid_json_returns_400(self, mock_ecs):
        """Non-JSON body returns 400."""
        body = "not valid json {"
        sig = _make_signature(body.encode("utf-8"))
        event = {
            "body": body,
            "headers": {"x-hub-signature-256": sig},
        }
        result = handler(event, None)

        assert result["statusCode"] == 400
        body_resp = json.loads(result["body"])
        assert "Malformed JSON" in body_resp["error"]

    def test_missing_action_returns_400(self, mock_ecs):
        """Payload missing the action field returns 400."""
        payload = {
            "workflow_job": {"labels": ["self-hosted", "x64"]},
            "repository": {"full_name": "org/repo"},
        }
        event = _make_event(payload)
        result = handler(event, None)

        assert result["statusCode"] == 400
        body_resp = json.loads(result["body"])
        assert "Invalid payload" in body_resp["error"]

    def test_missing_workflow_job_returns_400(self, mock_ecs):
        """Payload missing workflow_job returns 400."""
        payload = {
            "action": "queued",
            "repository": {"full_name": "org/repo"},
        }
        event = _make_event(payload)
        result = handler(event, None)

        assert result["statusCode"] == 400

    def test_missing_repository_returns_400(self, mock_ecs):
        """Payload missing repository returns 400."""
        payload = {
            "action": "queued",
            "workflow_job": {"labels": ["self-hosted", "x64"]},
        }
        event = _make_event(payload)
        result = handler(event, None)

        assert result["statusCode"] == 400

    def test_empty_labels_returns_400(self, mock_ecs):
        """Payload with empty labels array returns 400."""
        payload = {
            "action": "queued",
            "workflow_job": {"labels": []},
            "repository": {"full_name": "org/repo"},
        }
        event = _make_event(payload)
        result = handler(event, None)

        assert result["statusCode"] == 400


# --- Test: Unrecognized labels returns 422 (Req 4.6) ---


class TestUnrecognizedLabels:
    """Unrecognized labels returns 422 (Req 4.6)."""

    def test_no_arch_labels_returns_422(self, mock_ecs):
        """Labels with no architecture match return 422."""
        payload = _valid_payload()
        payload["workflow_job"]["labels"] = ["self-hosted", "linux", "custom-runner"]
        event = _make_event(payload)
        result = handler(event, None)

        assert result["statusCode"] == 422
        body = json.loads(result["body"])
        assert "Unrecognized runner labels" in body["error"]

    def test_422_response_includes_labels(self, mock_ecs):
        """The 422 response includes the unrecognized labels."""
        labels = ["self-hosted", "linux", "my-special-runner"]
        payload = _valid_payload()
        payload["workflow_job"]["labels"] = labels
        event = _make_event(payload)
        result = handler(event, None)

        body = json.loads(result["body"])
        assert body["labels"] == labels

    def test_only_self_hosted_and_linux_returns_422(self, mock_ecs):
        """Only self-hosted and linux labels (no arch) returns 422."""
        payload = _valid_payload()
        payload["workflow_job"]["labels"] = ["self-hosted", "linux"]
        event = _make_event(payload)
        result = handler(event, None)

        assert result["statusCode"] == 422
