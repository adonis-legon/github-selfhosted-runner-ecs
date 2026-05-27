"""
GitHub Webhook Handler for ECS Runner System.

This Lambda function processes GitHub workflow_job webhook events,
validates the webhook signature, determines the target architecture
from runner labels, and launches ephemeral Fargate tasks as
GitHub self-hosted runners.
"""

import hashlib
import hmac
import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)

ecs_client = boto3.client("ecs")
secretsmanager_client = boto3.client("secretsmanager")


def validate_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Validate GitHub webhook HMAC-SHA256 signature.

    GitHub sends the signature in the X-Hub-Signature-256 header in the
    format 'sha256=<hex_digest>'. This function computes the expected
    HMAC-SHA256 of the payload using the shared secret and compares it
    to the provided signature using a timing-safe comparison.

    Args:
        payload: The raw request body bytes.
        signature: The signature from the X-Hub-Signature-256 header
                   (e.g. 'sha256=abc123...').
        secret: The shared webhook secret string.

    Returns:
        True if the signature is valid, False otherwise.
    """
    if not signature.startswith("sha256="):
        return False

    expected_mac = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()
    

    provided_signature = signature[len("sha256="):]

    return hmac.compare_digest(expected_mac, provided_signature)


def parse_labels(labels: list[str]) -> str | None:
    """Determine architecture from runner labels.

    Inspects the provided list of runner labels and returns the
    corresponding ECS CPU architecture string. Labels are matched
    case-insensitively using substring containment:
      - A label containing "x64" or "x86_64" maps to "X86_64"
      - A label containing "arm64" or "arm" maps to "ARM64"

    If conflicting labels are present (both x86 and arm patterns),
    x86_64 takes precedence. The labels "self-hosted" and "linux"
    are ignored for architecture matching.

    Args:
        labels: List of runner labels from the workflow_job event
                (e.g. ["self-hosted", "linux", "x64"]).

    Returns:
        "X86_64" if an x86 label is found, "ARM64" if an arm label
        is found (and no x86 label is present), or None if no
        architecture label matches.
    """
    found_x86 = False
    found_arm = False

    for label in labels:
        lower = label.lower()

        # Skip non-architecture labels
        if lower in ("self-hosted", "linux"):
            continue

        if "x64" in lower or "x86_64" in lower:
            found_x86 = True
        elif "arm64" in lower or "arm" in lower:
            found_arm = True

    # x86_64 takes precedence if conflicting labels exist
    if found_x86:
        return "X86_64"
    if found_arm:
        return "ARM64"
    return None


def validate_payload(payload: dict) -> tuple[bool, list[str]]:
    """Validate a GitHub webhook payload for required fields.

    Checks that the payload contains the required structure for a
    workflow_job event:
      - `action` field is present, is a string, and equals "queued"
      - `workflow_job` exists and contains a `labels` field that is
        a non-empty list
      - `repository` exists and contains a `full_name` field that is
        a non-empty string

    Args:
        payload: Parsed JSON payload as a dictionary.

    Returns:
        A tuple of (is_valid, errors) where is_valid is True if all
        validations pass, and errors is a list of human-readable error
        messages describing each validation failure. When is_valid is
        True, errors will be an empty list.
    """
    errors: list[str] = []

    # Validate action field exists and equals "queued"
    action = payload.get("action")
    if action is None:
        errors.append("Missing required field: 'action'")
    elif not isinstance(action, str):
        errors.append("Invalid type for 'action': expected string")
    elif action != "queued":
        errors.append(
            f"Invalid value for 'action': expected 'queued', got '{action}'"
        )

    # Validate workflow_job.labels
    workflow_job = payload.get("workflow_job")
    if workflow_job is None:
        errors.append("Missing required field: 'workflow_job'")
    elif not isinstance(workflow_job, dict):
        errors.append("Invalid type for 'workflow_job': expected object")
    else:
        labels = workflow_job.get("labels")
        if labels is None:
            errors.append("Missing required field: 'workflow_job.labels'")
        elif not isinstance(labels, list):
            errors.append(
                "Invalid type for 'workflow_job.labels': expected array"
            )
        elif len(labels) == 0:
            errors.append("'workflow_job.labels' must be a non-empty array")

    # Validate repository.full_name
    repository = payload.get("repository")
    if repository is None:
        errors.append("Missing required field: 'repository'")
    elif not isinstance(repository, dict):
        errors.append("Invalid type for 'repository': expected object")
    else:
        full_name = repository.get("full_name")
        if full_name is None:
            errors.append("Missing required field: 'repository.full_name'")
        elif not isinstance(full_name, str):
            errors.append(
                "Invalid type for 'repository.full_name': expected string"
            )
        elif len(full_name) == 0:
            errors.append("'repository.full_name' must be a non-empty string")

    return (len(errors) == 0, errors)


def launch_task(
    cluster: str,
    task_def_arn: str,
    subnets: list[str],
    security_group: str,
) -> dict:
    """Launch an ECS Fargate task. Returns RunTask response.

    Calls the ECS RunTask API to launch a single Fargate task in the
    specified cluster with the given task definition. The task is placed
    in the provided subnets with a public IP assigned and the specified
    security group attached.

    Args:
        cluster: The ECS cluster name or ARN to launch the task in.
        task_def_arn: The ARN of the task definition to use.
        subnets: List of subnet IDs for task placement.
        security_group: The security group ID to attach to the task.

    Returns:
        The full RunTask API response dictionary.

    Raises:
        Exception: If the ECS RunTask API call fails. The caller should
            handle this and return HTTP 500 with the failure reason
            (Requirement 4.7).
    """
    response = ecs_client.run_task(
        cluster=cluster,
        taskDefinition=task_def_arn,
        launchType="FARGATE",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnets,
                "securityGroups": [security_group],
                "assignPublicIp": "ENABLED",
            }
        },
    )
    return response


def handler(event, context):
    """Process GitHub workflow_job webhook events.

    Orchestrates: signature validation → payload parsing → label matching
    → task launching.

    Returns:
        200 - Task launched successfully
        400 - Malformed payload
        401 - Invalid signature
        422 - Unrecognized runner labels
        500 - ECS launch failure
    """
    # Read environment variables
    cluster_arn = os.environ.get("CLUSTER_ARN", "")
    task_def_x86_arn = os.environ.get("TASK_DEF_X86_ARN", "")
    task_def_arm64_arn = os.environ.get("TASK_DEF_ARM64_ARN", "")
    subnets_str = os.environ.get("SUBNETS", "")
    security_group = os.environ.get("SECURITY_GROUP", "")
    webhook_secret_arn = os.environ.get("WEBHOOK_SECRET_ARN", "")

    subnets = [s.strip() for s in subnets_str.split(",") if s.strip()]

    # Step 1: Retrieve webhook secret from Secrets Manager
    try:
        sm_response = secretsmanager_client.get_secret_value(
            SecretId=webhook_secret_arn
        )
        webhook_secret = sm_response["SecretString"]
    except Exception as e:
        logger.error("Failed to retrieve webhook secret from Secrets Manager: %s", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"}),
        }

    # Step 2: Validate webhook signature
    body = event.get("body", "")
    if isinstance(body, str):
        payload_bytes = body.encode("utf-8")
    else:
        payload_bytes = body

    signature = event.get("headers", {}).get("x-hub-signature-256", "")

    if not validate_signature(payload_bytes, signature, webhook_secret):
        logger.warning("Invalid webhook signature")
        return {
            "statusCode": 401,
            "body": json.dumps({"error": "Invalid signature"}),
        }

    # Step 3: Parse and validate payload
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError) as e:
        logger.error("Failed to parse JSON payload: %s", str(e))
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Malformed JSON payload"}),
        }

    is_valid, errors = validate_payload(payload)
    if not is_valid:
        logger.error("Payload validation failed: %s", errors)
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid payload", "details": errors}),
        }

    # Step 4: Extract labels and determine architecture
    labels = payload["workflow_job"]["labels"]
    architecture = parse_labels(labels)

    if architecture is None:
        logger.error("Unrecognized runner labels: %s", labels)
        return {
            "statusCode": 422,
            "body": json.dumps(
                {"error": "Unrecognized runner labels", "labels": labels}
            ),
        }

    # Step 5: Select appropriate task definition
    if architecture == "X86_64":
        task_def_arn = task_def_x86_arn
    else:
        task_def_arn = task_def_arm64_arn

    # Step 6: Launch ECS task
    try:
        response = launch_task(cluster_arn, task_def_arn, subnets, security_group)
        logger.info(
            "Successfully launched ECS task for %s architecture",
            architecture,
        )
        task_arns = [
            task["taskArn"] for task in response.get("tasks", [])
        ]
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Task launched successfully",
                    "architecture": architecture,
                    "taskArns": task_arns,
                }
            ),
        }
    except Exception as e:
        logger.error("Failed to launch ECS task: %s", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps(
                {"error": "ECS task launch failed", "details": str(e)}
            ),
        }
