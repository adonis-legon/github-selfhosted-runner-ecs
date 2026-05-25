# GitHub Self-Hosted Runners on AWS ECS (Fargate)

Ephemeral GitHub Actions self-hosted runners running on AWS ECS with Fargate. The system automatically launches a fresh runner container for each workflow job, executes it, and terminates — no persistent infrastructure, no stale state.

## Architecture

```mermaid
graph TB
    subgraph GitHub
        GH[GitHub Repository]
        WH[Webhook Event]
    end

    subgraph AWS
        subgraph "API Gateway + Lambda"
            APIGW[API Gateway HTTPS Endpoint]
            LFN[Lambda - webhook_handler.py]
        end

        subgraph "ECS Cluster - Fargate"
            TD_X86[Task Definition x86_64]
            TD_ARM[Task Definition arm64]
            TASK[Runner Task - Ephemeral]
        end

        subgraph Networking
            VPC[VPC]
            SUB_A[Public Subnet AZ-a]
            SUB_B[Public Subnet AZ-b]
            IGW[Internet Gateway]
            SG[Security Group - Egress Only]
        end

        subgraph "Storage & Secrets"
            ECR[ECR Repository]
            SSM_PAT[SSM Parameter - PAT]
            SSM_WH[SSM Parameter - Webhook Secret]
        end
    end

    GH -->|workflow_job queued| WH
    WH -->|POST /webhook| APIGW
    APIGW --> LFN
    LFN -->|Validate signature| SSM_WH
    LFN -->|ecs:RunTask| TD_X86
    LFN -->|ecs:RunTask| TD_ARM
    TD_X86 --> TASK
    TD_ARM --> TASK
    TASK -->|Pull image| ECR
    TASK -->|Get PAT| SSM_PAT
    TASK -->|Register + Run job| GH
    TASK --> SUB_A
    TASK --> SUB_B
    SUB_A --> IGW
    SUB_B --> IGW
```

### Request Flow

1. GitHub sends a `workflow_job` webhook (action: `queued`) to the API Gateway endpoint.
2. Lambda validates the HMAC-SHA256 signature, parses the payload, and determines the architecture from `runs-on` labels.
3. Lambda calls `ecs:RunTask` with the matching task definition (x86_64 or arm64).
4. The Fargate task starts, retrieves the PAT from SSM, obtains a registration token, and registers as an ephemeral runner.
5. The runner picks up the queued job, executes it, deregisters, and the task terminates.

### Runner Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Starting
    Starting --> RetrievingPAT
    RetrievingPAT --> ObtainingToken: PAT retrieved
    RetrievingPAT --> Failed: PAT retrieval error
    ObtainingToken --> Registering: Token obtained
    ObtainingToken --> Retrying: Transient error
    Retrying --> ObtainingToken: Retry (max 3)
    Retrying --> Failed: Max retries exceeded
    Registering --> Idle: Registered (ephemeral)
    Idle --> Running: Job assigned
    Idle --> TimedOut: 5 min timeout
    Running --> Completed: Job finished
    Completed --> Deregistering
    TimedOut --> Deregistering
    Deregistering --> [*]
    Failed --> [*]
```

## Prerequisites

- AWS CLI v2 configured with credentials
- Docker with buildx support (for multi-arch builds)
- A GitHub Personal Access Token (PAT) with `admin:org` scope (or `repo` scope for repo-level runners)
- Python 3.12+ (for local development/testing)

## Project Structure

```
├── cloudformation/
│   └── template.yaml          # All AWS infrastructure (single stack)
├── docker/
│   ├── Dockerfile             # Multi-arch runner image (Ubuntu 22.04)
│   └── entrypoint.sh          # Runner lifecycle: register → run → deregister
├── lambda/
│   └── webhook_handler.py     # Webhook processing Lambda function
├── scripts/
│   ├── build-image.sh         # Build multi-arch Docker image
│   ├── push-image.sh          # Push image to ECR
│   ├── create-stack.sh        # Deploy CloudFormation stack
│   └── destroy-stack.sh       # Tear down stack with confirmation
└── tests/
    ├── unit/                  # Example-based unit tests
    ├── property/              # Property-based tests (Hypothesis)
    ├── smoke/                 # CloudFormation template validation
    └── integration/           # End-to-end webhook flow tests
```

## Quick Start

### 1. Create a GitHub PAT

Generate a Personal Access Token at [github.com/settings/tokens](https://github.com/settings/tokens):

- **Classic token**: Select `admin:org` scope (for org-level runners) or `repo` scope (for repo-level runners)
- **Fine-grained token**: Grant "Self-hosted runners" read/write permission on the target org or repo

### 2. Generate a Webhook Secret

```bash
openssl rand -hex 20
```

Save this value — you'll use it for both the stack deployment and the GitHub webhook configuration.

### 3. Deploy the Infrastructure

```bash
./scripts/create-stack.sh \
  --stack-name github-runners \
  --region us-east-1 \
  --pat ghp_YOUR_PAT_HERE \
  --webhook-secret YOUR_SECRET_HERE \
  --github-org your-org-name
```

Optional parameters:

| Flag        | Default | Description                                    |
| ----------- | ------- | ---------------------------------------------- |
| `--cpu`     | 2048    | Fargate CPU units (256, 512, 1024, 2048, 4096) |
| `--memory`  | 4096    | Memory in MiB                                  |
| `--storage` | 30      | Ephemeral storage in GiB (min 21)              |

The script validates the template, packages the Lambda code, deploys the stack, and waits for completion. On success it prints the **Webhook Endpoint URL** — you'll need this next.

### 4. Build and Push the Runner Image

```bash
# Build for both amd64 and arm64
./scripts/build-image.sh v1.0.0

# Push to ECR (get the URI from stack outputs)
./scripts/push-image.sh \
  123456789012.dkr.ecr.us-east-1.amazonaws.com/github-runner \
  us-east-1 \
  v1.0.0
```

### 5. Configure the GitHub Webhook

1. Go to your GitHub organization: **Settings → Webhooks → Add webhook**
2. Set:
   - **Payload URL**: The API Gateway URL from the stack output (e.g., `https://abc123.execute-api.us-east-1.amazonaws.com/webhook`)
   - **Content type**: `application/json`
   - **Secret**: The same webhook secret you used during deployment
   - **Events**: Select "Workflow jobs" only
3. Save the webhook.

## Integrating a Repository

To use these self-hosted runners in any repository within your organization:

### Workflow Configuration

In your repository's `.github/workflows/*.yml`, set `runs-on` to target the runner labels:

```yaml
name: CI

on: [push, pull_request]

jobs:
  build-x86:
    runs-on: [self-hosted, linux, x64]
    steps:
      - uses: actions/checkout@v4
      - run: echo "Running on x86_64 ECS Fargate runner"

  build-arm:
    runs-on: [self-hosted, linux, arm64]
    steps:
      - uses: actions/checkout@v4
      - run: echo "Running on arm64 ECS Fargate runner"
```

### Supported Labels

The system routes jobs based on architecture labels in `runs-on`:

| Label             | Architecture | Task Definition         |
| ----------------- | ------------ | ----------------------- |
| `x64` or `x86_64` | X86_64       | 2 vCPU / 4 GB (default) |
| `arm64` or `arm`  | ARM64        | 2 vCPU / 4 GB (default) |

- Label matching is **case-insensitive**
- `self-hosted` and `linux` are required by GitHub but ignored for routing
- If conflicting labels exist (both x64 and arm64), **x86_64 takes precedence**
- If no architecture label matches, the webhook returns HTTP 422 and no runner is launched

### Docker Builds Inside Runners

The runner image includes Docker CLI and buildx. For container builds within your workflow:

```yaml
jobs:
  docker-build:
    runs-on: [self-hosted, linux, x64]
    steps:
      - uses: actions/checkout@v4
      - name: Build image
        run: docker buildx build -t my-app:latest .
```

### Repo-Level Runners (Optional)

By default, runners register at the organization level. To scope runners to a specific repository, set the `GITHUB_REPO` environment variable in the task definition (requires modifying the CloudFormation template).

## Configuration Reference

### CloudFormation Parameters

| Parameter          | Required | Default | Description                         |
| ------------------ | -------- | ------- | ----------------------------------- |
| `GitHubOrg`        | Yes      | —       | GitHub organization name            |
| `PATValue`         | Yes      | —       | GitHub PAT (stored in SSM)          |
| `WebhookSecret`    | Yes      | —       | Webhook HMAC secret (stored in SSM) |
| `CpuAllocation`    | No       | 2048    | Fargate CPU units                   |
| `MemoryAllocation` | No       | 4096    | Memory in MiB                       |
| `EphemeralStorage` | No       | 30      | Ephemeral disk in GiB (min 21)      |

### Runner Environment Variables

These are set automatically by the task definition:

| Variable        | Description                          |
| --------------- | ------------------------------------ |
| `GITHUB_ORG`    | Organization for runner registration |
| `GITHUB_REPO`   | (Optional) Specific repository       |
| `PAT_SSM_PARAM` | SSM parameter path for the PAT       |
| `RUNNER_LABELS` | Labels assigned to the runner        |
| `AWS_REGION`    | Region for AWS API calls             |

## Operations

### Updating the Runner Image

```bash
# Rebuild with a new version tag
./scripts/build-image.sh v1.1.0

# Push the updated image
./scripts/push-image.sh \
  123456789012.dkr.ecr.us-east-1.amazonaws.com/github-runner \
  us-east-1 \
  v1.1.0
```

New tasks will automatically pull the `latest` tag. Running tasks are unaffected (ephemeral — they terminate after one job).

### Rotating the PAT

Update the SSM parameter directly:

```bash
aws ssm put-parameter \
  --name /github-runner/pat \
  --value "ghp_NEW_TOKEN_HERE" \
  --type String \
  --overwrite \
  --region us-east-1
```

New runner tasks will pick up the updated PAT immediately.

### Monitoring

- **CloudWatch Logs**: Runner output is streamed to `/ecs/github-runner` log group
- **ECS Console**: View running/stopped tasks in the cluster
- **GitHub UI**: Check runner status at Organization → Settings → Actions → Runners

### Destroying the Stack

```bash
./scripts/destroy-stack.sh --stack-name github-runners --region us-east-1
```

You'll be prompted to type the stack name to confirm. This deletes all resources including the VPC, ECS cluster, ECR repository, and stored secrets.

## Runner Lifecycle

Each runner task follows the state machine shown in the architecture diagram above:

1. **Start** — Fargate launches the container
2. **Retrieve PAT** — Fetches the token from SSM Parameter Store
3. **Register** — Obtains a registration token from GitHub API (retries up to 3× on transient errors)
4. **Configure** — Registers as an ephemeral runner (`--ephemeral` flag)
5. **Wait** — Waits for a job assignment (5-minute idle timeout)
6. **Execute** — Runs the assigned workflow job
7. **Terminate** — Deregisters from GitHub and the Fargate task stops

If no job arrives within 5 minutes, the runner deregisters and exits cleanly.

## Development

### Running Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Test categories:

- `tests/unit/` — Unit tests for handler, scripts, and entrypoint logic
- `tests/property/` — Property-based tests (Hypothesis) for core logic invariants
- `tests/smoke/` — CloudFormation template structure validation
- `tests/integration/` — End-to-end webhook flow with mocked AWS services

### Local Lambda Testing

```bash
# Run just the webhook handler tests
python -m pytest tests/unit/test_webhook_handler.py -v

# Run property tests with verbose Hypothesis output
python -m pytest tests/property/ -v --hypothesis-show-statistics
```

## Security Considerations

- **No inbound traffic**: Security group allows egress only — runners cannot be reached from the internet
- **Ephemeral by design**: Each job gets a fresh container with no state from previous runs
- **Secrets in SSM**: PAT and webhook secret are stored in SSM Parameter Store, never in code or environment variables at rest
- **Webhook validation**: Every incoming request is verified via HMAC-SHA256 before processing
- **Non-root execution**: The runner agent runs as a non-root user inside the container
- **Minimal IAM**: Task role only has `ssm:GetParameter` for the PAT; execution role only has ECR pull and CloudWatch Logs

## License

MIT
