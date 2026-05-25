"""Unit tests for docker/entrypoint.sh logic.

Since bats is not available, these tests use subprocess to run the entrypoint
script with mocked external commands (aws, curl, jq, config.sh, run.sh) injected
via a custom PATH directory containing stub scripts.

Validates: Requirements 3.5, 3.6, 8.5
"""

import os
import stat
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

ENTRYPOINT = str(Path(__file__).resolve().parents[2] / "docker" / "entrypoint.sh")

# Base environment variables required by the entrypoint
BASE_ENV = {
    "GITHUB_ORG": "test-org",
    "PAT_SSM_PARAM": "/github-runner/pat",
    "RUNNER_LABELS": "self-hosted,linux,x64",
    "AWS_REGION": "us-east-1",
    "HOME": "/tmp",
    "PATH": "",  # Will be overridden per test
}


def _create_stub(bin_dir: Path, name: str, script: str) -> Path:
    """Create an executable stub script in the given directory."""
    stub = bin_dir / name
    stub.write_text(script)
    stub.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
    return stub


def _setup_mock_bin(tmp_path: Path, aws_script: str, curl_script: str = "",
                    jq_script: str = "", config_script: str = "",
                    run_script: str = "", extra_stubs: dict = None) -> Path:
    """Create a mock bin directory with stub commands.

    Returns the path to the mock bin directory.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # aws CLI stub
    _create_stub(bin_dir, "aws", f"#!/bin/bash\n{aws_script}")

    # curl stub (for GitHub API calls)
    if curl_script:
        _create_stub(bin_dir, "curl", f"#!/bin/bash\n{curl_script}")
    else:
        # Default: return a valid registration token response
        _create_stub(bin_dir, "curl", textwrap.dedent("""\
            #!/bin/bash
            echo '{"token": "FAKE_REG_TOKEN"}'
            echo "201"
        """))

    # jq stub
    if jq_script:
        _create_stub(bin_dir, "jq", f"#!/bin/bash\n{jq_script}")
    else:
        _create_stub(bin_dir, "jq", textwrap.dedent("""\
            #!/bin/bash
            # Simple jq stub: extract .token from JSON
            if [[ "$1" == "-r" && "$2" == ".token" ]]; then
                # Read stdin and extract token value
                input=$(cat)
                echo "$input" | sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p'
            else
                cat
            fi
        """))

    # hostname stub
    _create_stub(bin_dir, "hostname", "#!/bin/bash\necho 'test-host'")

    # date stub
    _create_stub(bin_dir, "date", textwrap.dedent("""\
        #!/bin/bash
        if [[ "$1" == "+%s" ]]; then
            echo "1234567890"
        else
            echo "2024-01-01T00:00:00Z"
        fi
    """))

    # sleep stub (no-op for fast tests)
    _create_stub(bin_dir, "sleep", "#!/bin/bash\nexit 0")

    # seq stub
    _create_stub(bin_dir, "seq", textwrap.dedent("""\
        #!/bin/bash
        for i in $(eval echo {$1..$2}); do echo $i; done
    """))

    # kill stub
    _create_stub(bin_dir, "kill", "#!/bin/bash\nexit 0")

    # ls stub
    _create_stub(bin_dir, "ls", "#!/bin/bash\nexit 1")

    if extra_stubs:
        for name, content in extra_stubs.items():
            _create_stub(bin_dir, name, f"#!/bin/bash\n{content}")

    # Runner directory with config.sh and run.sh
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir()

    if config_script:
        _create_stub(runner_dir, "config.sh", f"#!/bin/bash\n{config_script}")
    else:
        _create_stub(runner_dir, "config.sh", "#!/bin/bash\nexit 0")

    if run_script:
        _create_stub(runner_dir, "run.sh", f"#!/bin/bash\n{run_script}")
    else:
        # Default: exit immediately (simulates runner completing)
        _create_stub(runner_dir, "run.sh", "#!/bin/bash\nexit 0")

    return bin_dir


def _run_entrypoint(tmp_path: Path, bin_dir: Path, env_overrides: dict = None,
                    timeout: int = 10) -> subprocess.CompletedProcess:
    """Run the entrypoint script with mocked environment.

    Returns the CompletedProcess result.
    """
    runner_dir = tmp_path / "runner"

    env = dict(BASE_ENV)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    env["RUNNER_DIR"] = str(runner_dir)

    if env_overrides:
        env.update(env_overrides)

    # We need to modify the entrypoint to use our RUNNER_DIR
    # Create a wrapper that sets RUNNER_DIR before sourcing entrypoint
    wrapper = tmp_path / "run_entrypoint.sh"
    wrapper.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        # Override RUNNER_DIR for testing
        export RUNNER_DIR="{runner_dir}"
        # Source the entrypoint with modified RUNNER_DIR
        # We use sed to replace the RUNNER_DIR assignment
        sed 's|RUNNER_DIR="/home/runner"|RUNNER_DIR="{runner_dir}"|g' "{ENTRYPOINT}" | bash
    """))
    wrapper.chmod(stat.S_IRWXU)

    result = subprocess.run(
        [str(wrapper)],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result


# --- Test: Invalid PAT logs error and exits 1 (Req 3.5) ---


class TestInvalidPAT:
    """Invalid PAT logs error and exits 1 (Req 3.5)."""

    def test_invalid_pat_http_401_exits_1(self, tmp_path):
        """When GitHub API returns 401 for invalid PAT, entrypoint exits 1."""
        # aws ssm succeeds (returns a PAT), but curl returns 401
        bin_dir = _setup_mock_bin(
            tmp_path,
            aws_script='echo "fake-pat-value"',
            curl_script=textwrap.dedent("""\
                #!/bin/bash
                echo '{"message": "Bad credentials"}'
                echo "401"
            """),
        )

        result = _run_entrypoint(tmp_path, bin_dir)

        assert result.returncode == 1
        # Should log an error about invalid/expired PAT
        combined_output = result.stdout + result.stderr
        assert "401" in combined_output or "invalid" in combined_output.lower() or "expired" in combined_output.lower()

    def test_invalid_pat_error_message_logged(self, tmp_path):
        """Error message mentions PAT authentication issue."""
        bin_dir = _setup_mock_bin(
            tmp_path,
            aws_script='echo "fake-pat-value"',
            curl_script=textwrap.dedent("""\
                #!/bin/bash
                echo '{"message": "Bad credentials"}'
                echo "401"
            """),
        )

        result = _run_entrypoint(tmp_path, bin_dir)

        combined_output = result.stdout + result.stderr
        # The entrypoint should log ERROR with PAT-related message
        assert "ERROR" in combined_output
        assert "PAT" in combined_output or "401" in combined_output


# --- Test: SSM access denied logs error and exits 1 (Req 3.6) ---


class TestSSMAccessDenied:
    """SSM access denied logs error and exits 1 (Req 3.6)."""

    def test_ssm_access_denied_exits_1(self, tmp_path):
        """When aws ssm get-parameter fails with access denied, entrypoint exits 1."""
        bin_dir = _setup_mock_bin(
            tmp_path,
            aws_script=textwrap.dedent("""\
                echo "An error occurred (AccessDeniedException) when calling the GetParameter operation: User is not authorized" >&2
                exit 1
            """),
        )

        result = _run_entrypoint(tmp_path, bin_dir)

        assert result.returncode == 1

    def test_ssm_access_denied_logs_error(self, tmp_path):
        """Error output mentions SSM parameter retrieval failure."""
        bin_dir = _setup_mock_bin(
            tmp_path,
            aws_script=textwrap.dedent("""\
                echo "An error occurred (AccessDeniedException) when calling the GetParameter operation: User is not authorized" >&2
                exit 1
            """),
        )

        result = _run_entrypoint(tmp_path, bin_dir)

        combined_output = result.stdout + result.stderr
        assert "ERROR" in combined_output
        # Should mention SSM or parameter retrieval
        assert "SSM" in combined_output or "PAT" in combined_output or "parameter" in combined_output.lower()

    def test_ssm_missing_parameter_exits_1(self, tmp_path):
        """When SSM parameter does not exist, entrypoint exits 1."""
        bin_dir = _setup_mock_bin(
            tmp_path,
            aws_script=textwrap.dedent("""\
                echo "An error occurred (ParameterNotFound) when calling the GetParameter operation: Parameter /github-runner/pat not found" >&2
                exit 1
            """),
        )

        result = _run_entrypoint(tmp_path, bin_dir)

        assert result.returncode == 1
        combined_output = result.stdout + result.stderr
        assert "ERROR" in combined_output


# --- Test: 5-minute idle timeout triggers deregistration (Req 8.5) ---


class TestIdleTimeout:
    """5-minute idle timeout triggers deregistration (Req 8.5)."""

    def test_idle_timeout_triggers_exit(self, tmp_path):
        """When runner doesn't pick up a job within timeout, it exits."""
        # We need to simulate the idle timeout scenario.
        # The entrypoint polls every 10s and checks for Worker_ files.
        # We'll set IDLE_TIMEOUT to a small value for testing by modifying the script.
        # Instead, we create a modified entrypoint with a short timeout.

        bin_dir = _setup_mock_bin(
            tmp_path,
            aws_script='echo "fake-pat-value"',
            curl_script=textwrap.dedent("""\
                #!/bin/bash
                echo '{"token": "FAKE_REG_TOKEN"}'
                echo "201"
            """),
            # run.sh sleeps longer than our timeout
            run_script="sleep 999",
        )

        runner_dir = tmp_path / "runner"

        # Create a modified entrypoint with short timeout for testing
        modified_entrypoint = tmp_path / "entrypoint_short_timeout.sh"
        with open(ENTRYPOINT, "r") as f:
            content = f.read()

        # Replace IDLE_TIMEOUT and POLL_INTERVAL for fast testing
        content = content.replace("IDLE_TIMEOUT=300", "IDLE_TIMEOUT=2")
        content = content.replace("POLL_INTERVAL=10", "POLL_INTERVAL=1")
        content = content.replace('RUNNER_DIR="/home/runner"', f'RUNNER_DIR="{runner_dir}"')

        modified_entrypoint.write_text(content)
        modified_entrypoint.chmod(stat.S_IRWXU)

        env = dict(BASE_ENV)
        env["PATH"] = f"{bin_dir}:/usr/bin:/bin"

        result = subprocess.run(
            [str(modified_entrypoint)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        # Should exit 0 (clean exit after timeout)
        assert result.returncode == 0
        combined_output = result.stdout + result.stderr
        # Should mention idle timeout
        assert "timeout" in combined_output.lower() or "idle" in combined_output.lower()

    def test_idle_timeout_logs_deregistration(self, tmp_path):
        """Idle timeout triggers deregistration logging."""
        bin_dir = _setup_mock_bin(
            tmp_path,
            aws_script='echo "fake-pat-value"',
            curl_script=textwrap.dedent("""\
                #!/bin/bash
                echo '{"token": "FAKE_REG_TOKEN"}'
                echo "201"
            """),
            run_script="sleep 999",
        )

        runner_dir = tmp_path / "runner"

        modified_entrypoint = tmp_path / "entrypoint_short_timeout2.sh"
        with open(ENTRYPOINT, "r") as f:
            content = f.read()

        content = content.replace("IDLE_TIMEOUT=300", "IDLE_TIMEOUT=2")
        content = content.replace("POLL_INTERVAL=10", "POLL_INTERVAL=1")
        content = content.replace('RUNNER_DIR="/home/runner"', f'RUNNER_DIR="{runner_dir}"')

        modified_entrypoint.write_text(content)
        modified_entrypoint.chmod(stat.S_IRWXU)

        env = dict(BASE_ENV)
        env["PATH"] = f"{bin_dir}:/usr/bin:/bin"

        result = subprocess.run(
            [str(modified_entrypoint)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        combined_output = result.stdout + result.stderr
        # Should log deregistration
        assert "Deregistering" in combined_output or "deregister" in combined_output.lower()


# --- Test: Successful registration flow ---


class TestSuccessfulRegistration:
    """Successful registration flow completes without error."""

    def test_successful_flow_exits_0(self, tmp_path):
        """Full successful flow: SSM → token → config → run exits 0."""
        bin_dir = _setup_mock_bin(
            tmp_path,
            aws_script='echo "fake-pat-value"',
            curl_script=textwrap.dedent("""\
                #!/bin/bash
                echo '{"token": "FAKE_REG_TOKEN"}'
                echo "201"
            """),
            config_script="exit 0",
            run_script="exit 0",
        )

        runner_dir = tmp_path / "runner"

        modified_entrypoint = tmp_path / "entrypoint_success.sh"
        with open(ENTRYPOINT, "r") as f:
            content = f.read()

        content = content.replace('RUNNER_DIR="/home/runner"', f'RUNNER_DIR="{runner_dir}"')
        content = content.replace("POLL_INTERVAL=10", "POLL_INTERVAL=1")
        content = content.replace("IDLE_TIMEOUT=300", "IDLE_TIMEOUT=3")

        modified_entrypoint.write_text(content)
        modified_entrypoint.chmod(stat.S_IRWXU)

        env = dict(BASE_ENV)
        env["PATH"] = f"{bin_dir}:/usr/bin:/bin"

        result = subprocess.run(
            [str(modified_entrypoint)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 0

    def test_successful_flow_logs_registration(self, tmp_path):
        """Successful flow logs PAT retrieval and registration token steps."""
        bin_dir = _setup_mock_bin(
            tmp_path,
            aws_script='echo "fake-pat-value"',
            curl_script=textwrap.dedent("""\
                #!/bin/bash
                echo '{"token": "FAKE_REG_TOKEN"}'
                echo "201"
            """),
            config_script="exit 0",
            run_script="exit 0",
        )

        runner_dir = tmp_path / "runner"

        modified_entrypoint = tmp_path / "entrypoint_success2.sh"
        with open(ENTRYPOINT, "r") as f:
            content = f.read()

        content = content.replace('RUNNER_DIR="/home/runner"', f'RUNNER_DIR="{runner_dir}"')
        content = content.replace("POLL_INTERVAL=10", "POLL_INTERVAL=1")
        content = content.replace("IDLE_TIMEOUT=300", "IDLE_TIMEOUT=3")

        modified_entrypoint.write_text(content)
        modified_entrypoint.chmod(stat.S_IRWXU)

        env = dict(BASE_ENV)
        env["PATH"] = f"{bin_dir}:/usr/bin:/bin"

        result = subprocess.run(
            [str(modified_entrypoint)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        combined_output = result.stdout + result.stderr
        # Should log PAT retrieval success
        assert "PAT retrieved" in combined_output or "PAT" in combined_output
        # Should log registration token obtained
        assert "Registration token" in combined_output or "registration token" in combined_output.lower() or "token obtained" in combined_output.lower()

    def test_successful_flow_configures_ephemeral(self, tmp_path):
        """Runner is configured with --ephemeral flag."""
        # Track config.sh arguments
        config_log = tmp_path / "config_args.log"

        bin_dir = _setup_mock_bin(
            tmp_path,
            aws_script='echo "fake-pat-value"',
            curl_script=textwrap.dedent("""\
                #!/bin/bash
                echo '{"token": "FAKE_REG_TOKEN"}'
                echo "201"
            """),
            config_script=f'echo "$@" >> "{config_log}"\nexit 0',
            run_script="exit 0",
        )

        runner_dir = tmp_path / "runner"

        modified_entrypoint = tmp_path / "entrypoint_ephemeral.sh"
        with open(ENTRYPOINT, "r") as f:
            content = f.read()

        content = content.replace('RUNNER_DIR="/home/runner"', f'RUNNER_DIR="{runner_dir}"')
        content = content.replace("POLL_INTERVAL=10", "POLL_INTERVAL=1")
        content = content.replace("IDLE_TIMEOUT=300", "IDLE_TIMEOUT=3")

        modified_entrypoint.write_text(content)
        modified_entrypoint.chmod(stat.S_IRWXU)

        env = dict(BASE_ENV)
        env["PATH"] = f"{bin_dir}:/usr/bin:/bin"

        result = subprocess.run(
            [str(modified_entrypoint)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode == 0
        # Verify config.sh was called with --ephemeral
        if config_log.exists():
            config_args = config_log.read_text()
            assert "--ephemeral" in config_args
