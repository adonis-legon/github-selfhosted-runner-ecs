"""Unit tests for scripts/create-stack.sh and scripts/destroy-stack.sh.

Uses subprocess to run the shell scripts with mocked external commands
(aws) injected via a custom PATH directory containing stub scripts.

Validates: Requirements 7.4, 7.7, 7.9
"""

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CREATE_SCRIPT = str(PROJECT_ROOT / "scripts" / "create-stack.sh")
DESTROY_SCRIPT = str(PROJECT_ROOT / "scripts" / "destroy-stack.sh")


def _create_stub(bin_dir: Path, name: str, script: str) -> Path:
    """Create an executable stub script in the given directory."""
    stub = bin_dir / name
    stub.write_text(script)
    stub.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
    return stub


def _setup_create_mocks(tmp_path: Path, aws_script: str = "") -> Path:
    """Create a mock bin directory with aws stub for create-stack.sh tests.

    Returns the path to the mock bin directory.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    if aws_script:
        _create_stub(bin_dir, "aws", f"#!/bin/bash\n{aws_script}")
    else:
        _create_stub(bin_dir, "aws", textwrap.dedent("""\
            #!/bin/bash
            exit 0
        """))

    return bin_dir


def _setup_destroy_mocks(tmp_path: Path, aws_script: str = "") -> Path:
    """Create a mock bin directory with aws stub for destroy-stack.sh tests.

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
            exit 0
        """))

    return bin_dir


def _run_create_script(tmp_path: Path, bin_dir: Path, args: list = None,
                       timeout: int = 10) -> subprocess.CompletedProcess:
    """Run create-stack.sh with mocked environment."""
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
    }

    cmd = ["bash", CREATE_SCRIPT]
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


def _run_destroy_script(tmp_path: Path, bin_dir: Path, args: list = None,
                        stdin_input: str = None,
                        timeout: int = 10) -> subprocess.CompletedProcess:
    """Run destroy-stack.sh with mocked environment."""
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
    }

    cmd = ["bash", DESTROY_SCRIPT]
    if args:
        cmd.extend(args)

    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        input=stdin_input,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )
    return result


# --- Tests for create-stack.sh: Missing params shows usage and exits 1 (Req 7.7) ---


class TestCreateStackMissingParams:
    """create-stack.sh displays usage and exits 1 when required params are missing (Req 7.7)."""

    def test_no_arguments_exits_1(self, tmp_path):
        """Running create-stack.sh with no arguments exits with code 1."""
        bin_dir = _setup_create_mocks(tmp_path)
        result = _run_create_script(tmp_path, bin_dir, args=[])

        assert result.returncode == 1

    def test_no_arguments_shows_usage(self, tmp_path):
        """Running create-stack.sh with no arguments shows usage message."""
        bin_dir = _setup_create_mocks(tmp_path)
        result = _run_create_script(tmp_path, bin_dir, args=[])

        combined_output = result.stdout + result.stderr
        assert "Usage:" in combined_output
        assert "--stack-name" in combined_output
        assert "--region" in combined_output
        assert "--pat" in combined_output

    def test_missing_some_required_params_exits_1(self, tmp_path):
        """Running with only some required params (missing others) exits with code 1."""
        bin_dir = _setup_create_mocks(tmp_path)

        # Provide only --stack-name and --region, missing --pat and --webhook-secret
        result = _run_create_script(
            tmp_path, bin_dir,
            args=["--stack-name", "test-stack", "--region", "us-east-1"]
        )

        assert result.returncode == 1
        combined_output = result.stdout + result.stderr
        assert "Missing required parameters" in combined_output


# --- Tests for destroy-stack.sh: Non-existent stack shows error and exits 1 (Req 7.9) ---


class TestDestroyStackNonExistent:
    """destroy-stack.sh errors when stack does not exist (Req 7.9)."""

    def test_non_existent_stack_exits_1(self, tmp_path):
        """When aws cloudformation describe-stacks fails (stack not found), script exits 1."""
        aws_script = textwrap.dedent("""\
            if [[ "$*" == *"describe-stacks"* ]]; then
                echo "An error occurred (ValidationError) when calling the DescribeStacks operation: Stack with id non-existent-stack does not exist" >&2
                exit 1
            fi
            exit 0
        """)

        bin_dir = _setup_destroy_mocks(tmp_path, aws_script=aws_script)

        result = _run_destroy_script(
            tmp_path, bin_dir,
            args=["--stack-name", "non-existent-stack"]
        )

        assert result.returncode == 1

    def test_non_existent_stack_shows_error_message(self, tmp_path):
        """When stack does not exist, error message indicates stack was not found."""
        aws_script = textwrap.dedent("""\
            if [[ "$*" == *"describe-stacks"* ]]; then
                echo "An error occurred (ValidationError) when calling the DescribeStacks operation: Stack with id missing-stack does not exist" >&2
                exit 1
            fi
            exit 0
        """)

        bin_dir = _setup_destroy_mocks(tmp_path, aws_script=aws_script)

        result = _run_destroy_script(
            tmp_path, bin_dir,
            args=["--stack-name", "missing-stack"]
        )

        combined_output = result.stdout + result.stderr
        assert "not found" in combined_output.lower()


# --- Tests for destroy-stack.sh: Confirmation mismatch aborts deletion (Req 7.4) ---


class TestDestroyStackConfirmationMismatch:
    """destroy-stack.sh aborts deletion when confirmation does not match (Req 7.4)."""

    def test_wrong_confirmation_exits_0(self, tmp_path):
        """When user types wrong confirmation (not matching stack name), script exits 0."""
        # aws describe-stacks succeeds (stack exists)
        aws_script = textwrap.dedent("""\
            if [[ "$*" == *"describe-stacks"* ]]; then
                exit 0
            fi
            exit 0
        """)

        bin_dir = _setup_destroy_mocks(tmp_path, aws_script=aws_script)

        # Pipe wrong confirmation via stdin
        result = _run_destroy_script(
            tmp_path, bin_dir,
            args=["--stack-name", "my-stack"],
            stdin_input="wrong-name\n"
        )

        assert result.returncode == 0

    def test_wrong_confirmation_shows_abort_message(self, tmp_path):
        """When confirmation doesn't match, abort message is displayed."""
        aws_script = textwrap.dedent("""\
            if [[ "$*" == *"describe-stacks"* ]]; then
                exit 0
            fi
            exit 0
        """)

        bin_dir = _setup_destroy_mocks(tmp_path, aws_script=aws_script)

        result = _run_destroy_script(
            tmp_path, bin_dir,
            args=["--stack-name", "my-stack"],
            stdin_input="not-my-stack\n"
        )

        combined_output = result.stdout + result.stderr
        assert "does not match" in combined_output.lower() or "aborting" in combined_output.lower()

    def test_wrong_confirmation_does_not_call_delete(self, tmp_path):
        """When confirmation doesn't match, aws cloudformation delete-stack is NOT called."""
        command_log = tmp_path / "aws_log.txt"

        aws_script = textwrap.dedent(f"""\
            echo "AWS: $@" >> "{command_log}"
            if [[ "$*" == *"describe-stacks"* ]]; then
                exit 0
            fi
            exit 0
        """)

        bin_dir = _setup_destroy_mocks(tmp_path, aws_script=aws_script)

        result = _run_destroy_script(
            tmp_path, bin_dir,
            args=["--stack-name", "my-stack"],
            stdin_input="wrong-input\n"
        )

        assert result.returncode == 0
        log_content = command_log.read_text() if command_log.exists() else ""
        assert "delete-stack" not in log_content
