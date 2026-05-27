"""Integration tests for the webhook handler end-to-end flow.

These tests exercise the full Lambda handler function with mocked AWS
services (SSM for secret retrieval, ECS for task launching). They validate
the complete request lifecycle: signature validation → payload parsing →
label routing → ECS task launch → response.

Validates: Requirements 4.2, 4.5, 4.6, 4.7
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


# --- Constants ---

WEBHOOK_SECRET = "integration-test-secret"

ENV_VARS = {
    "CLUSTER_ARN": "arn:aws:ecs:us-east-1:123456789012:cluster/github-runners",
    "TASK_DEF_X86_ARN": "arn:aws:ecs:us-east-1:123456789012:task-definition/github-runner-x86:3",
    "TASK_DEF_ARM64_ARN": "arn:aws:ecs:us-east-1:123456789012:task-definition/github-runner-arm64:3",
    "SUBNETS": "subnet-pub-a,subnet-pub-b",
    "SECURITY_GROUP": "sg-runners-001",
    "WEBHOOK_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:/github-runner/webhook-secret-AbCdEf",
}


# --- Helpers ---


def _compute_signature(payload_bytes: bytes, secret: str = WEBHOOK_SECRET) -> str:
    """Compute a valid HMAC-SHA256 signature for a payload."""
    mac = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"sha256={mac}"


def _build_webhook_event(
    payload: dict,
    secret: str = WEBHOOK_SECRET,
    override_signature: str | None = None,
    override_headers: dict | None = None,
) -> dict:
    """Build a complete API Gateway proxy event simulating a GitHub webhook."""
    body = json.dumps(payload)
    signature = override_signature or _compute_signature(body.encode("utf-8"), secret)
    headers = {"x-hub-signature-256": signature}
    if override_headers is not None:
        headers.update(override_headers)
    return {
        "body": body,
        "headers": headers,
    }


def _queued_payload(labels: list[str] | None = None) -> dict:
    """Build a valid workflow_job queued payload with configurable labels."""
    return {
        "action": "queued",
        "workflow_job": {
            "id": 99887766,
            "run_id": 11223344,
            "name": "build-and-test",
            "labels": labels or ["self-hosted", "linux", "x64"],
            "runner_group_name": "Default",
        },
        "repository": {"full_name": "myorg/myrepo"},
        "organization": {"login": "myorg"},
    }


# --- Fixtures ---


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    """Set all required environment variables."""
    for key, value in ENV_VARS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def mock_secretsmanager():
    """Mock Secrets Manager to return the webhook secret."""
    with patch.object(_webhook_handler, "secretsmanager_client") as mock:
        mock.get_secret_value.return_value = {
            "SecretString": WEBHOOK_SECRET
        }
        yield mock


@pytest.fixture()
def mock_ecs():
    """Mock ECS client returning a successful RunTask response."""
    with patch.object(_webhook_handler, "ecs_client") as mock:
        mock.run_task.return_value = {
            "tasks": [
                {
                    "taskArn": "arn:aws:ecs:us-east-1:123456789012:task/github-runners/task-id-001",
                    "lastStatus": "PROVISIONING",
                }
            ],
            "failures": [],
        }
        yield mock


# =============================================================================
# Integration Test: End-to-end webhook → task launch flow (Req 4.2)
# =============================================================================


class TestEndToEndWebhookFlow:
    """Test the complete webhook → task launch flow with mocked ECS.

    Validates: Requirement 4.2 - When a workflow_job event with action
    "queued" is received, the handler SHALL launch a Runner_Task in the
    ECS_Cluster.
    """

    def test_valid_webhook_launches_task_and_returns_200(self, mock_ecs):
        """Full flow: valid signature + valid payload → ECS task launched → 200."""
        payload = _queued_payload(labels=["self-hosted", "linux", "x64"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["message"] == "Task launched successfully"
        assert body["architecture"] == "X86_64"
        assert len(body["taskArns"]) == 1
        assert "task-id-001" in body["taskArns"][0]

    def test_ecs_run_task_called_with_correct_network_config(self, mock_ecs):
        """ECS RunTask is called with the correct subnets, SG, and public IP."""
        payload = _queued_payload(labels=["self-hosted", "linux", "x64"])
        event = _build_webhook_event(payload)

        handler(event, None)

        mock_ecs.run_task.assert_called_once()
        call_kwargs = mock_ecs.run_task.call_args[1]
        assert call_kwargs["cluster"] == ENV_VARS["CLUSTER_ARN"]
        assert call_kwargs["launchType"] == "FARGATE"
        assert call_kwargs["count"] == 1
        net_config = call_kwargs["networkConfiguration"]["awsvpcConfiguration"]
        assert net_config["subnets"] == ["subnet-pub-a", "subnet-pub-b"]
        assert net_config["securityGroups"] == ["sg-runners-001"]
        assert net_config["assignPublicIp"] == "ENABLED"

    def test_secretsmanager_called_to_retrieve_webhook_secret(self, mock_secretsmanager, mock_ecs):
        """The handler retrieves the webhook secret from Secrets Manager before validation."""
        payload = _queued_payload()
        event = _build_webhook_event(payload)

        handler(event, None)

        mock_secretsmanager.get_secret_value.assert_called_once()

    def test_handler_processes_full_github_payload(self, mock_ecs):
        """Handler works with a realistic full GitHub webhook payload."""
        payload = {
            "action": "queued",
            "workflow_job": {
                "id": 123456789,
                "run_id": 987654321,
                "name": "CI / Build",
                "labels": ["self-hosted", "linux", "x64"],
                "runner_group_name": "Default",
                "workflow_name": "CI",
                "head_branch": "main",
                "head_sha": "abc123def456",
            },
            "repository": {
                "full_name": "myorg/myrepo",
                "private": True,
            },
            "organization": {"login": "myorg"},
            "sender": {"login": "developer1"},
        }
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 200
        mock_ecs.run_task.assert_called_once()


# =============================================================================
# Integration Test: Label routing selects correct task definition (Req 4.5)
# =============================================================================


class TestLabelRouting:
    """Test that runner labels route to the correct task definition.

    Validates: Requirement 4.5 - The handler SHALL match runner labels to
    the corresponding Task_Definition by architecture (x86_64 labels to
    x86_64 Task_Definition, arm64 labels to arm64 Task_Definition).
    """

    def test_x64_label_routes_to_x86_task_definition(self, mock_ecs):
        """Labels containing 'x64' route to the x86_64 task definition."""
        payload = _queued_payload(labels=["self-hosted", "linux", "x64"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 200
        call_kwargs = mock_ecs.run_task.call_args[1]
        assert call_kwargs["taskDefinition"] == ENV_VARS["TASK_DEF_X86_ARN"]

    def test_x86_64_label_routes_to_x86_task_definition(self, mock_ecs):
        """Labels containing 'x86_64' route to the x86_64 task definition."""
        payload = _queued_payload(labels=["self-hosted", "linux", "x86_64"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 200
        call_kwargs = mock_ecs.run_task.call_args[1]
        assert call_kwargs["taskDefinition"] == ENV_VARS["TASK_DEF_X86_ARN"]
        body = json.loads(result["body"])
        assert body["architecture"] == "X86_64"

    def test_arm64_label_routes_to_arm64_task_definition(self, mock_ecs):
        """Labels containing 'arm64' route to the arm64 task definition."""
        payload = _queued_payload(labels=["self-hosted", "linux", "arm64"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 200
        call_kwargs = mock_ecs.run_task.call_args[1]
        assert call_kwargs["taskDefinition"] == ENV_VARS["TASK_DEF_ARM64_ARN"]
        body = json.loads(result["body"])
        assert body["architecture"] == "ARM64"

    def test_arm_label_routes_to_arm64_task_definition(self, mock_ecs):
        """Labels containing 'arm' route to the arm64 task definition."""
        payload = _queued_payload(labels=["self-hosted", "linux", "arm"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 200
        call_kwargs = mock_ecs.run_task.call_args[1]
        assert call_kwargs["taskDefinition"] == ENV_VARS["TASK_DEF_ARM64_ARN"]

    def test_conflicting_labels_x86_takes_precedence(self, mock_ecs):
        """When both x86 and arm labels are present, x86_64 takes precedence."""
        payload = _queued_payload(labels=["self-hosted", "linux", "x64", "arm64"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 200
        call_kwargs = mock_ecs.run_task.call_args[1]
        assert call_kwargs["taskDefinition"] == ENV_VARS["TASK_DEF_X86_ARN"]
        body = json.loads(result["body"])
        assert body["architecture"] == "X86_64"

    def test_case_insensitive_label_matching(self, mock_ecs):
        """Label matching is case-insensitive."""
        payload = _queued_payload(labels=["self-hosted", "linux", "ARM64"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["architecture"] == "ARM64"


# =============================================================================
# Integration Test: Full handler response codes (Reqs 4.2, 4.5, 4.6, 4.7)
# =============================================================================


class TestHandlerResponseCodes:
    """Test all handler response codes for the complete flow.

    Validates:
    - 200: Task launched successfully (Req 4.2)
    - 400: Malformed payload (Req 4.8)
    - 401: Invalid signature (Req 4.4)
    - 422: Unrecognized labels (Req 4.6)
    - 500: ECS failure (Req 4.7)
    """

    # --- 200 Success ---

    def test_200_on_successful_task_launch(self, mock_ecs):
        """Returns 200 with task details on successful launch."""
        payload = _queued_payload(labels=["self-hosted", "linux", "x64"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "taskArns" in body
        assert body["message"] == "Task launched successfully"

    # --- 400 Malformed Payload ---

    def test_400_on_invalid_json_body(self, mock_ecs):
        """Returns 400 when the body is not valid JSON."""
        raw_body = "this is not json {{"
        sig = _compute_signature(raw_body.encode("utf-8"))
        event = {
            "body": raw_body,
            "headers": {"x-hub-signature-256": sig},
        }

        result = handler(event, None)

        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "Malformed JSON" in body["error"]

    def test_400_on_missing_action_field(self, mock_ecs):
        """Returns 400 when the payload is missing the 'action' field."""
        payload = {
            "workflow_job": {"labels": ["self-hosted", "x64"]},
            "repository": {"full_name": "org/repo"},
        }
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 400

    def test_400_on_wrong_action_value(self, mock_ecs):
        """Returns 400 when action is not 'queued'."""
        payload = _queued_payload()
        payload["action"] = "completed"
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 400

    def test_400_on_missing_workflow_job(self, mock_ecs):
        """Returns 400 when workflow_job is missing."""
        payload = {"action": "queued", "repository": {"full_name": "org/repo"}}
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 400

    def test_400_on_missing_labels(self, mock_ecs):
        """Returns 400 when workflow_job.labels is missing."""
        payload = {
            "action": "queued",
            "workflow_job": {"id": 123},
            "repository": {"full_name": "org/repo"},
        }
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 400

    def test_400_on_empty_labels_array(self, mock_ecs):
        """Returns 400 when labels is an empty array."""
        payload = _queued_payload(labels=[])
        # Need to manually set labels since helper won't allow empty
        payload["workflow_job"]["labels"] = []
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 400

    def test_400_on_missing_repository(self, mock_ecs):
        """Returns 400 when repository is missing."""
        payload = {
            "action": "queued",
            "workflow_job": {"labels": ["self-hosted", "x64"]},
        }
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 400

    # --- 401 Invalid Signature ---

    def test_401_on_wrong_signature(self, mock_ecs):
        """Returns 401 when the signature doesn't match the payload."""
        payload = _queued_payload()
        event = _build_webhook_event(payload, override_signature="sha256=0000000000abcdef")

        result = handler(event, None)

        assert result["statusCode"] == 401
        body = json.loads(result["body"])
        assert "Invalid signature" in body["error"]

    def test_401_on_missing_signature_header(self, mock_ecs):
        """Returns 401 when the signature header is absent."""
        payload = _queued_payload()
        body_str = json.dumps(payload)
        event = {
            "body": body_str,
            "headers": {},
        }

        result = handler(event, None)

        assert result["statusCode"] == 401

    def test_401_on_signature_without_sha256_prefix(self, mock_ecs):
        """Returns 401 when signature lacks the 'sha256=' prefix."""
        payload = _queued_payload()
        body_str = json.dumps(payload)
        # Compute valid hex but without prefix
        mac = hmac.new(
            key=WEBHOOK_SECRET.encode("utf-8"),
            msg=body_str.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
        event = {
            "body": body_str,
            "headers": {"x-hub-signature-256": mac},  # no sha256= prefix
        }

        result = handler(event, None)

        assert result["statusCode"] == 401

    def test_401_on_signature_from_different_secret(self, mock_ecs):
        """Returns 401 when signature was computed with a different secret."""
        payload = _queued_payload()
        event = _build_webhook_event(payload, secret="totally-wrong-secret")

        result = handler(event, None)

        assert result["statusCode"] == 401

    # --- 422 Unrecognized Labels ---

    def test_422_on_no_architecture_labels(self, mock_ecs):
        """Returns 422 when labels don't match any architecture."""
        payload = _queued_payload(labels=["self-hosted", "linux", "gpu-runner"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 422
        body = json.loads(result["body"])
        assert "Unrecognized runner labels" in body["error"]
        assert body["labels"] == ["self-hosted", "linux", "gpu-runner"]

    def test_422_on_only_self_hosted_and_linux(self, mock_ecs):
        """Returns 422 when only 'self-hosted' and 'linux' labels are present."""
        payload = _queued_payload(labels=["self-hosted", "linux"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 422

    def test_422_on_custom_labels_without_arch(self, mock_ecs):
        """Returns 422 for custom labels that don't indicate architecture."""
        payload = _queued_payload(labels=["self-hosted", "linux", "large", "gpu"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 422

    # --- 500 ECS Failure ---

    def test_500_on_ecs_run_task_exception(self, mock_ecs):
        """Returns 500 when ECS RunTask raises an exception."""
        mock_ecs.run_task.side_effect = Exception(
            "An error occurred (ClusterNotFoundException)"
        )
        payload = _queued_payload(labels=["self-hosted", "linux", "x64"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 500
        body = json.loads(result["body"])
        assert "ECS task launch failed" in body["error"]
        assert "ClusterNotFoundException" in body["details"]

    def test_500_on_ecs_capacity_error(self, mock_ecs):
        """Returns 500 when ECS has no capacity."""
        mock_ecs.run_task.side_effect = Exception(
            "An error occurred (ServiceException): Unable to place task, "
            "no container instances available"
        )
        payload = _queued_payload(labels=["self-hosted", "linux", "arm64"])
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 500
        body = json.loads(result["body"])
        assert "details" in body

    def test_500_on_secretsmanager_retrieval_failure(self, mock_secretsmanager):
        """Returns 500 when Secrets Manager fails to retrieve the webhook secret."""
        mock_secretsmanager.get_secret_value.side_effect = Exception(
            "ResourceNotFoundException: /github-runner/webhook-secret"
        )
        payload = _queued_payload()
        event = _build_webhook_event(payload)

        result = handler(event, None)

        assert result["statusCode"] == 500
        body = json.loads(result["body"])
        assert "error" in body
