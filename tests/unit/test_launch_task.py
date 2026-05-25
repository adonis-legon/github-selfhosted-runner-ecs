"""Unit tests for the launch_task function."""

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 'lambda' is a Python keyword, so we cannot use a normal import.
_lambda_dir = str(Path(__file__).resolve().parents[2] / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

_webhook_handler = importlib.import_module("webhook_handler")
launch_task = _webhook_handler.launch_task


class TestLaunchTask:
    """Tests for launch_task ECS Fargate task launcher."""

    @patch("webhook_handler.ecs_client")
    def test_launch_task_calls_run_task_with_correct_params(self, mock_ecs):
        """Verify run_task is called with Fargate launch type and network config."""
        mock_ecs.run_task.return_value = {
            "tasks": [{"taskArn": "arn:aws:ecs:us-east-1:123456789:task/my-cluster/abc123"}],
            "failures": [],
        }

        result = launch_task(
            cluster="my-cluster",
            task_def_arn="arn:aws:ecs:us-east-1:123456789:task-definition/runner:1",
            subnets=["subnet-aaa", "subnet-bbb"],
            security_group="sg-12345",
        )

        mock_ecs.run_task.assert_called_once_with(
            cluster="my-cluster",
            taskDefinition="arn:aws:ecs:us-east-1:123456789:task-definition/runner:1",
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

    @patch("webhook_handler.ecs_client")
    def test_launch_task_returns_run_task_response(self, mock_ecs):
        """Verify the full RunTask response dict is returned."""
        expected_response = {
            "tasks": [
                {
                    "taskArn": "arn:aws:ecs:us-east-1:123456789:task/my-cluster/abc123",
                    "lastStatus": "PROVISIONING",
                }
            ],
            "failures": [],
        }
        mock_ecs.run_task.return_value = expected_response

        result = launch_task(
            cluster="my-cluster",
            task_def_arn="arn:aws:ecs:us-east-1:123456789:task-definition/runner:1",
            subnets=["subnet-aaa"],
            security_group="sg-12345",
        )

        assert result == expected_response

    @patch("webhook_handler.ecs_client")
    def test_launch_task_propagates_ecs_exception(self, mock_ecs):
        """Verify ECS exceptions propagate to caller for HTTP 500 handling (Req 4.7)."""
        mock_ecs.run_task.side_effect = Exception("ECS cluster not found")

        with pytest.raises(Exception, match="ECS cluster not found"):
            launch_task(
                cluster="nonexistent-cluster",
                task_def_arn="arn:aws:ecs:us-east-1:123456789:task-definition/runner:1",
                subnets=["subnet-aaa"],
                security_group="sg-12345",
            )

    @patch("webhook_handler.ecs_client")
    def test_launch_task_uses_fargate_launch_type(self, mock_ecs):
        """Verify FARGATE launch type is always used."""
        mock_ecs.run_task.return_value = {"tasks": [], "failures": []}

        launch_task(
            cluster="cluster",
            task_def_arn="arn:aws:ecs:us-east-1:123:task-definition/td:1",
            subnets=["subnet-1"],
            security_group="sg-1",
        )

        call_kwargs = mock_ecs.run_task.call_args[1]
        assert call_kwargs["launchType"] == "FARGATE"

    @patch("webhook_handler.ecs_client")
    def test_launch_task_assigns_public_ip(self, mock_ecs):
        """Verify public IP assignment is ENABLED (Req 4.2 - task in public subnet)."""
        mock_ecs.run_task.return_value = {"tasks": [], "failures": []}

        launch_task(
            cluster="cluster",
            task_def_arn="arn:aws:ecs:us-east-1:123:task-definition/td:1",
            subnets=["subnet-1"],
            security_group="sg-1",
        )

        call_kwargs = mock_ecs.run_task.call_args[1]
        vpc_config = call_kwargs["networkConfiguration"]["awsvpcConfiguration"]
        assert vpc_config["assignPublicIp"] == "ENABLED"

    @patch("webhook_handler.ecs_client")
    def test_launch_task_passes_multiple_subnets(self, mock_ecs):
        """Verify multiple subnets are passed for multi-AZ placement."""
        mock_ecs.run_task.return_value = {"tasks": [], "failures": []}

        subnets = ["subnet-az1", "subnet-az2", "subnet-az3"]
        launch_task(
            cluster="cluster",
            task_def_arn="arn:aws:ecs:us-east-1:123:task-definition/td:1",
            subnets=subnets,
            security_group="sg-1",
        )

        call_kwargs = mock_ecs.run_task.call_args[1]
        vpc_config = call_kwargs["networkConfiguration"]["awsvpcConfiguration"]
        assert vpc_config["subnets"] == subnets
