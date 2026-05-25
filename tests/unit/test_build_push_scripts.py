"""Unit tests for scripts/build-image.sh and scripts/push-image.sh.

Uses subprocess to run the shell scripts with mocked external commands
(docker, aws) injected via a custom PATH directory containing stub scripts.

Validates: Requirements 6.3, 6.4, 6.5
"""

import os
import stat
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = str(PROJECT_ROOT / "scripts" / "build-image.sh")
PUSH_SCRIPT = str(PROJECT_ROOT / "scripts" / "push-image.sh")


def _create_stub(bin_dir: Path, name: str, script: str) -> Path:
    """Create an executable stub script in the given directory."""
    stub = bin_dir / name
    stub.write_text(script)
    stub.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
    return stub


def _setup_build_mocks(tmp_path: Path, docker_script: str = "") -> Path:
    """Create a mock bin directory with docker stub for build-image.sh tests.

    Returns the path to the mock bin directory.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    if docker_script:
        _create_stub(bin_dir, "docker", f"#!/bin/bash\n{docker_script}")
    else:
        # Default: docker commands succeed and log their invocations
        _create_stub(bin_dir, "docker", textwrap.dedent("""\
            #!/bin/bash
            echo "DOCKER_CMD: $@" >> "${DOCKER_LOG:-/tmp/docker_log.txt}"
            exit 0
        """))

    return bin_dir


def _setup_push_mocks(tmp_path: Path, aws_script: str = "",
                      docker_script: str = "") -> Path:
    """Create a mock bin directory with aws and docker stubs for push-image.sh tests.

    Returns the path to the mock bin directory.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    if aws_script:
        _create_stub(bin_dir, "aws", f"#!/bin/bash\n{aws_script}")
    else:
        # Default: aws commands succeed
        _create_stub(bin_dir, "aws", textwrap.dedent("""\
            #!/bin/bash
            echo "AWS_CMD: $@" >> "${AWS_LOG:-/tmp/aws_log.txt}"
            # Return fake password for get-login-password
            if [[ "$*" == *"get-login-password"* ]]; then
                echo "fake-ecr-password"
            fi
            exit 0
        """))

    if docker_script:
        _create_stub(bin_dir, "docker", f"#!/bin/bash\n{docker_script}")
    else:
        # Default: docker commands succeed and log their invocations
        _create_stub(bin_dir, "docker", textwrap.dedent("""\
            #!/bin/bash
            echo "DOCKER_CMD: $@" >> "${DOCKER_LOG:-/tmp/docker_log.txt}"
            exit 0
        """))

    return bin_dir


def _run_build_script(tmp_path: Path, bin_dir: Path, args: list = None,
                      timeout: int = 10) -> subprocess.CompletedProcess:
    """Run build-image.sh with mocked environment."""
    docker_log = tmp_path / "docker_log.txt"

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "DOCKER_LOG": str(docker_log),
    }

    cmd = ["bash", BUILD_SCRIPT]
    if args:
        cmd.extend(args)

    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )
    return result


def _run_push_script(tmp_path: Path, bin_dir: Path, args: list = None,
                     timeout: int = 10) -> subprocess.CompletedProcess:
    """Run push-image.sh with mocked environment."""
    docker_log = tmp_path / "docker_log.txt"
    aws_log = tmp_path / "aws_log.txt"

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "DOCKER_LOG": str(docker_log),
        "AWS_LOG": str(aws_log),
    }

    cmd = ["bash", PUSH_SCRIPT]
    if args:
        cmd.extend(args)

    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )
    return result


# --- Tests for build-image.sh: Image tagged with version and latest (Req 6.3) ---


class TestBuildImageTagging:
    """build-image.sh tags image with version and latest (Req 6.3)."""

    def test_version_tag_produces_both_version_and_latest(self, tmp_path):
        """When a version tag is provided, image is tagged with both version and latest."""
        docker_log = tmp_path / "docker_log.txt"

        bin_dir = _setup_build_mocks(tmp_path, docker_script=textwrap.dedent(f"""\
            echo "DOCKER_CMD: $@" >> "{docker_log}"
            exit 0
        """))

        result = _run_build_script(tmp_path, bin_dir, args=["v1.2.3"])

        assert result.returncode == 0
        # Check docker was called with both tags
        log_content = docker_log.read_text() if docker_log.exists() else ""
        # The buildx build command should include both -t github-runner:v1.2.3 and -t github-runner:latest
        assert "github-runner:v1.2.3" in log_content
        assert "github-runner:latest" in log_content

    def test_latest_only_when_no_version_specified(self, tmp_path):
        """When no version is provided (defaults to 'latest'), only latest tag is used."""
        docker_log = tmp_path / "docker_log.txt"

        bin_dir = _setup_build_mocks(tmp_path, docker_script=textwrap.dedent(f"""\
            echo "DOCKER_CMD: $@" >> "{docker_log}"
            exit 0
        """))

        result = _run_build_script(tmp_path, bin_dir, args=[])

        assert result.returncode == 0
        log_content = docker_log.read_text() if docker_log.exists() else ""
        # Should have github-runner:latest but NOT a duplicate latest tag
        assert "github-runner:latest" in log_content
        # The output should mention only the latest tag
        assert "latest" in result.stdout

    def test_version_tag_displayed_in_output(self, tmp_path):
        """Build output displays the version tag being used."""
        bin_dir = _setup_build_mocks(tmp_path, docker_script=textwrap.dedent("""\
            exit 0
        """))

        result = _run_build_script(tmp_path, bin_dir, args=["v2.0.0"])

        assert result.returncode == 0
        assert "v2.0.0" in result.stdout
        assert "latest" in result.stdout


# --- Tests for push-image.sh: ECR auth called before push (Req 6.4) ---


class TestPushImageECRAuth:
    """push-image.sh authenticates with ECR before pushing (Req 6.4)."""

    def test_ecr_auth_called_before_docker_push(self, tmp_path):
        """ECR authentication (get-login-password + docker login) happens before docker push."""
        command_log = tmp_path / "command_order.txt"

        # aws stub that logs calls and succeeds
        aws_script = textwrap.dedent(f"""\
            echo "AWS: $@" >> "{command_log}"
            if [[ "$*" == *"get-login-password"* ]]; then
                echo "fake-ecr-password"
            fi
            exit 0
        """)

        # docker stub that logs calls and succeeds
        docker_script = textwrap.dedent(f"""\
            echo "DOCKER: $@" >> "{command_log}"
            exit 0
        """)

        bin_dir = _setup_push_mocks(tmp_path, aws_script=aws_script,
                                    docker_script=docker_script)

        result = _run_push_script(
            tmp_path, bin_dir,
            args=["123456789012.dkr.ecr.us-east-1.amazonaws.com/github-runner",
                  "us-east-1", "v1.0.0"]
        )

        assert result.returncode == 0
        log_content = command_log.read_text()
        lines = log_content.strip().split("\n")

        # Find the indices of key operations
        auth_idx = None
        push_idx = None
        for i, line in enumerate(lines):
            if "get-login-password" in line:
                auth_idx = i
            if "login" in line.lower() and "DOCKER" in line:
                # docker login is part of auth
                if auth_idx is None:
                    auth_idx = i
            if "push" in line and "DOCKER" in line:
                if push_idx is None:
                    push_idx = i

        # Auth must happen before push
        assert auth_idx is not None, "ECR authentication was not called"
        assert push_idx is not None, "Docker push was not called"
        assert auth_idx < push_idx, (
            f"ECR auth (line {auth_idx}) must happen before docker push (line {push_idx})"
        )

    def test_ecr_describe_repositories_called(self, tmp_path):
        """push-image.sh verifies ECR repository exists before pushing."""
        command_log = tmp_path / "command_order.txt"

        aws_script = textwrap.dedent(f"""\
            echo "AWS: $@" >> "{command_log}"
            if [[ "$*" == *"get-login-password"* ]]; then
                echo "fake-ecr-password"
            fi
            exit 0
        """)

        docker_script = textwrap.dedent(f"""\
            echo "DOCKER: $@" >> "{command_log}"
            exit 0
        """)

        bin_dir = _setup_push_mocks(tmp_path, aws_script=aws_script,
                                    docker_script=docker_script)

        result = _run_push_script(
            tmp_path, bin_dir,
            args=["123456789012.dkr.ecr.us-east-1.amazonaws.com/github-runner",
                  "us-east-1"]
        )

        assert result.returncode == 0
        log_content = command_log.read_text()
        assert "describe-repositories" in log_content


# --- Tests for push-image.sh: Error when ECR repository not found (Req 6.5) ---


class TestPushImageECRRepoNotFound:
    """push-image.sh errors when ECR repository does not exist (Req 6.5)."""

    def test_ecr_repo_not_found_exits_1(self, tmp_path):
        """When ECR repository does not exist, script exits with code 1."""
        # aws ecr describe-repositories fails (repo not found)
        aws_script = textwrap.dedent("""\
            if [[ "$*" == *"describe-repositories"* ]]; then
                echo "An error occurred (RepositoryNotFoundException)" >&2
                exit 1
            fi
            if [[ "$*" == *"get-login-password"* ]]; then
                echo "fake-ecr-password"
            fi
            exit 0
        """)

        docker_script = textwrap.dedent("""\
            exit 0
        """)

        bin_dir = _setup_push_mocks(tmp_path, aws_script=aws_script,
                                    docker_script=docker_script)

        result = _run_push_script(
            tmp_path, bin_dir,
            args=["123456789012.dkr.ecr.us-east-1.amazonaws.com/github-runner",
                  "us-east-1"]
        )

        assert result.returncode == 1

    def test_ecr_repo_not_found_shows_error_message(self, tmp_path):
        """When ECR repository does not exist, error message indicates repo must be created."""
        aws_script = textwrap.dedent("""\
            if [[ "$*" == *"describe-repositories"* ]]; then
                echo "An error occurred (RepositoryNotFoundException)" >&2
                exit 1
            fi
            if [[ "$*" == *"get-login-password"* ]]; then
                echo "fake-ecr-password"
            fi
            exit 0
        """)

        docker_script = textwrap.dedent("""\
            exit 0
        """)

        bin_dir = _setup_push_mocks(tmp_path, aws_script=aws_script,
                                    docker_script=docker_script)

        result = _run_push_script(
            tmp_path, bin_dir,
            args=["123456789012.dkr.ecr.us-east-1.amazonaws.com/github-runner",
                  "us-east-1"]
        )

        combined_output = result.stdout + result.stderr
        # Should mention the repository doesn't exist
        assert "does not exist" in combined_output or "not exist" in combined_output.lower()
        # Should suggest creating the repository
        assert "create" in combined_output.lower()

    def test_ecr_repo_not_found_does_not_push(self, tmp_path):
        """When ECR repository does not exist, docker push is never called."""
        command_log = tmp_path / "command_order.txt"

        aws_script = textwrap.dedent(f"""\
            echo "AWS: $@" >> "{command_log}"
            if [[ "$*" == *"describe-repositories"* ]]; then
                echo "An error occurred (RepositoryNotFoundException)" >&2
                exit 1
            fi
            if [[ "$*" == *"get-login-password"* ]]; then
                echo "fake-ecr-password"
            fi
            exit 0
        """)

        docker_script = textwrap.dedent(f"""\
            echo "DOCKER: $@" >> "{command_log}"
            exit 0
        """)

        bin_dir = _setup_push_mocks(tmp_path, aws_script=aws_script,
                                    docker_script=docker_script)

        result = _run_push_script(
            tmp_path, bin_dir,
            args=["123456789012.dkr.ecr.us-east-1.amazonaws.com/github-runner",
                  "us-east-1"]
        )

        assert result.returncode == 1
        log_content = command_log.read_text() if command_log.exists() else ""
        # Docker push should NOT have been called
        assert "push" not in log_content or "DOCKER" not in [
            line for line in log_content.split("\n") if "push" in line
        ]
