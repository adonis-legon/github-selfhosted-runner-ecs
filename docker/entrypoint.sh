#!/bin/bash
# GitHub Actions Runner Entrypoint
# Handles runner lifecycle: PAT retrieval, registration, execution, and cleanup.
#
# Environment Variables:
#   GITHUB_ORG       - GitHub organization name
#   GITHUB_REPO      - (Optional) Specific repository
#   PAT_SSM_PARAM    - SSM parameter name for PAT
#   RUNNER_LABELS    - Comma-separated runner labels
#   AWS_REGION       - AWS region for API calls
#
# Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 8.1, 8.2, 8.5
set -euo pipefail

# --- Configuration ---
MAX_RETRIES=3
RETRY_DELAY=5
IDLE_TIMEOUT=300  # 5 minutes in seconds
RUNNER_DIR="/home/runner"

# --- Logging ---
log() {
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
}

log_error() {
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] ERROR: $*" >&2
}

# --- Cleanup / Deregistration ---
cleanup() {
  log "Deregistering runner..."
  if [ -n "${REGISTRATION_TOKEN:-}" ]; then
    cd "${RUNNER_DIR}"
    ./config.sh remove --token "${REGISTRATION_TOKEN}" 2>/dev/null || true
  fi
  log "Runner deregistered. Exiting."
}

trap cleanup EXIT

# --- Step 1: Retrieve PAT from SSM Parameter Store (Req 3.1, 3.6) ---
log "Retrieving PAT from SSM Parameter Store: ${PAT_SSM_PARAM}"
PAT=$(aws ssm get-parameter \
  --name "${PAT_SSM_PARAM}" \
  --with-decryption \
  --query "Parameter.Value" \
  --output text \
  --region "${AWS_REGION}" 2>&1) || {
  log_error "Failed to retrieve PAT from SSM parameter '${PAT_SSM_PARAM}'. Ensure the task role has ssm:GetParameter permission."
  log_error "Details: ${PAT}"
  exit 1
}

if [ -z "${PAT}" ]; then
  log_error "PAT retrieved from SSM is empty."
  exit 1
fi

log "PAT retrieved successfully."

# --- Step 2: Obtain registration token from GitHub API (Req 3.2, 3.7) ---
# Determine the API URL based on whether GITHUB_REPO is set
if [ -n "${GITHUB_REPO:-}" ]; then
  REGISTRATION_URL="https://api.github.com/repos/${GITHUB_ORG}/${GITHUB_REPO}/actions/runners/registration-token"
  RUNNER_URL="https://github.com/${GITHUB_ORG}/${GITHUB_REPO}"
else
  REGISTRATION_URL="https://api.github.com/orgs/${GITHUB_ORG}/actions/runners/registration-token"
  RUNNER_URL="https://github.com/${GITHUB_ORG}"
fi

log "Requesting registration token from: ${REGISTRATION_URL}"

REGISTRATION_TOKEN=""
for attempt in $(seq 1 "${MAX_RETRIES}"); do
  HTTP_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    -H "Authorization: token ${PAT}" \
    -H "Accept: application/vnd.github+json" \
    "${REGISTRATION_URL}" 2>&1) || {
    # Connection failure (DNS, timeout, etc.) — retryable
    log "Attempt ${attempt}/${MAX_RETRIES}: Connection failed. Retrying in ${RETRY_DELAY}s..."
    if [ "${attempt}" -eq "${MAX_RETRIES}" ]; then
      log_error "Max retries exceeded. Unable to connect to GitHub API."
      exit 1
    fi
    sleep "${RETRY_DELAY}"
    continue
  }

  HTTP_BODY=$(echo "${HTTP_RESPONSE}" | sed '$d')
  HTTP_STATUS=$(echo "${HTTP_RESPONSE}" | tail -n1)

  case "${HTTP_STATUS}" in
    201)
      REGISTRATION_TOKEN=$(echo "${HTTP_BODY}" | jq -r '.token')
      if [ -n "${REGISTRATION_TOKEN}" ] && [ "${REGISTRATION_TOKEN}" != "null" ]; then
        log "Registration token obtained successfully."
        break
      else
        log_error "Registration token response did not contain a valid token."
        exit 1
      fi
      ;;
    401)
      log_error "PAT is invalid or expired (HTTP 401). Check your GitHub Personal Access Token."
      exit 1
      ;;
    403)
      log_error "Access forbidden (HTTP 403). PAT may lack required scopes."
      exit 1
      ;;
    404)
      log_error "Resource not found (HTTP 404). Check GITHUB_ORG='${GITHUB_ORG}' and GITHUB_REPO='${GITHUB_REPO:-}'."
      exit 1
      ;;
    5*)
      log "Attempt ${attempt}/${MAX_RETRIES}: Server error (HTTP ${HTTP_STATUS}). Retrying in ${RETRY_DELAY}s..."
      if [ "${attempt}" -eq "${MAX_RETRIES}" ]; then
        log_error "Max retries exceeded. Last response: HTTP ${HTTP_STATUS}"
        exit 1
      fi
      sleep "${RETRY_DELAY}"
      ;;
    *)
      log_error "Unexpected HTTP status: ${HTTP_STATUS}. Response: ${HTTP_BODY}"
      exit 1
      ;;
  esac
done

# --- Step 3: Configure runner in ephemeral mode (Req 3.3, 8.1) ---
RUNNER_NAME="ecs-runner-$(hostname)-$(date +%s)"

log "Configuring runner: name=${RUNNER_NAME}, labels=${RUNNER_LABELS:-self-hosted}"

cd "${RUNNER_DIR}"
./config.sh \
  --url "${RUNNER_URL}" \
  --token "${REGISTRATION_TOKEN}" \
  --name "${RUNNER_NAME}" \
  --labels "${RUNNER_LABELS:-self-hosted}" \
  --ephemeral \
  --unattended \
  --disableupdate \
  --replace

log "Runner configured successfully in ephemeral mode."

# --- Step 4: Start runner agent with idle timeout (Req 8.2, 8.5) ---
log "Starting runner agent..."

# Start the runner in the background so we can monitor for idle timeout
./run.sh &
RUNNER_PID=$!

# Monitor for idle timeout — if no job is picked up within 5 minutes, exit
ELAPSED=0
POLL_INTERVAL=10

while kill -0 "${RUNNER_PID}" 2>/dev/null; do
  # Check if the runner has picked up a job by looking for the _diag directory
  # containing Worker logs (indicates a job is running)
  if ls "${RUNNER_DIR}/_diag/Worker_"* 1>/dev/null 2>&1; then
    # Job has been assigned, wait for runner to complete
    log "Job assigned. Waiting for completion..."
    wait "${RUNNER_PID}" || true
    EXIT_CODE=$?
    log "Runner agent exited with code: ${EXIT_CODE}"
    exit 0
  fi

  sleep "${POLL_INTERVAL}"
  ELAPSED=$((ELAPSED + POLL_INTERVAL))

  if [ "${ELAPSED}" -ge "${IDLE_TIMEOUT}" ]; then
    log "Idle timeout reached (${IDLE_TIMEOUT}s). No job assigned."
    kill "${RUNNER_PID}" 2>/dev/null || true
    wait "${RUNNER_PID}" 2>/dev/null || true
    log "Runner terminated due to idle timeout."
    exit 0
  fi
done

# Runner exited on its own (job completed or error)
wait "${RUNNER_PID}" || true
EXIT_CODE=$?
log "Runner agent exited with code: ${EXIT_CODE}"
exit 0
