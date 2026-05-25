# Implementation Plan: GitHub Self-Hosted Runners on ECS

## Overview

This plan implements a system for hosting ephemeral GitHub self-hosted runners on AWS ECS with Fargate. The implementation covers CloudFormation infrastructure, a Python Lambda webhook handler, a Docker runner image with entrypoint script, and supporting shell scripts for image and stack lifecycle management.

## Tasks

- [x] 1. Set up project structure and core interfaces
  - [x] 1.1 Create directory structure and placeholder files
    - Create directories: `cloudformation/`, `lambda/`, `docker/`, `scripts/`, `tests/unit/`, `tests/property/`, `tests/smoke/`
    - Create placeholder files: `lambda/__init__.py`, `lambda/webhook_handler.py`, `docker/Dockerfile`, `docker/entrypoint.sh`, `scripts/build-image.sh`, `scripts/push-image.sh`, `scripts/create-stack.sh`, `scripts/destroy-stack.sh`
    - Create `requirements.txt` for Lambda dependencies (boto3) and `requirements-dev.txt` for test dependencies (pytest, hypothesis, moto)
    - _Requirements: 1.1, 4.1, 6.1, 7.1_

- [x] 2. Implement Lambda webhook handler
  - [x] 2.1 Implement webhook signature validation
    - Write `validate_signature(payload: bytes, signature: str, secret: str) -> bool` function
    - Use `hmac` and `hashlib` for HMAC-SHA256 computation
    - Compare signatures using `hmac.compare_digest` for timing-safe comparison
    - _Requirements: 4.3, 4.4_

  - [x] 2.2 Write property test for webhook signature validation
    - **Property 2: Webhook signature validation round-trip**
    - **Validates: Requirements 4.3, 4.4**

  - [x] 2.3 Implement label-to-architecture parser
    - Write `parse_labels(labels: list[str]) -> str | None` function
    - Implement case-insensitive matching: "x64"/"x86_64" → "X86_64", "arm64"/"arm" → "ARM64"
    - x86_64 takes precedence if conflicting labels exist
    - Ignore "self-hosted" and "linux" labels for architecture matching
    - Return None if no architecture label matches
    - _Requirements: 2.3, 2.4, 2.6, 4.5, 4.6_

  - [x] 2.4 Write property test for label-to-architecture mapping
    - **Property 1: Label-to-architecture mapping correctness**
    - **Validates: Requirements 2.3, 2.4, 2.6, 4.5, 4.6**

  - [x] 2.5 Implement payload validation
    - Write payload validation function that checks for required fields: `action` = "queued", `workflow_job.labels` as non-empty array, `repository.full_name` as non-empty string
    - Return structured error messages for missing or invalid fields
    - _Requirements: 4.8_

  - [x] 2.6 Write property test for payload validation
    - **Property 4: Payload validation accepts valid and rejects invalid payloads**
    - **Validates: Requirements 4.8**

  - [x] 2.7 Implement ECS task launcher
    - Write `launch_task(cluster, task_def_arn, subnets, security_group) -> dict` function
    - Use boto3 ECS client to call `run_task` with Fargate launch type, public IP assignment, and network configuration
    - _Requirements: 4.2, 4.7_

  - [x] 2.8 Implement main Lambda handler function
    - Write `handler(event, context)` that orchestrates: signature validation → payload parsing → label matching → task launching
    - Read environment variables: `CLUSTER_ARN`, `TASK_DEF_X86_ARN`, `TASK_DEF_ARM64_ARN`, `SUBNETS`, `SECURITY_GROUP`, `WEBHOOK_SECRET_PARAM`
    - Return appropriate HTTP status codes: 200, 400, 401, 422, 500
    - _Requirements: 4.1, 4.2, 4.3, 4.5, 4.6, 4.7, 4.8_

  - [x] 2.9 Write unit tests for Lambda handler
    - Test valid queued event triggers RunTask call
    - Test ECS failure returns 500 with error details
    - Test invalid signature returns 401
    - Test malformed payload returns 400
    - Test unrecognized labels returns 422
    - _Requirements: 4.2, 4.4, 4.6, 4.7, 4.8_

- [x] 3. Checkpoint - Ensure webhook handler tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement runner container image and entrypoint
  - [x] 4.1 Create Dockerfile for runner image
    - Use Ubuntu 22.04 as base image
    - Install tools: git, curl, jq, Docker CLI, buildx plugin
    - Download and install latest GitHub Actions runner agent
    - Set up non-root user for runner execution
    - Configure entrypoint to `docker/entrypoint.sh`
    - Support multi-arch build (linux/amd64 and linux/arm64)
    - _Requirements: 2.5, 5.1, 5.2, 5.3, 5.5_

  - [x] 4.2 Implement entrypoint script
    - Retrieve PAT from SSM Parameter Store using AWS CLI
    - Obtain registration token from GitHub API with retry logic (3 retries, 5s delay)
    - Configure runner with `--ephemeral` flag for single-job execution
    - Start runner agent and wait for completion
    - Implement 5-minute idle timeout (deregister and exit if no job assigned)
    - Implement deregistration on completion/timeout
    - Handle error cases: PAT retrieval failure (exit 1), invalid PAT (exit 1), max retries exceeded (exit 1)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 8.1, 8.2, 8.5_

  - [x] 4.3 Write property test for retry logic
    - **Property 3: Retry logic respects maximum attempts**
    - **Validates: Requirements 3.7**

  - [x] 4.4 Write unit tests for entrypoint logic
    - Test invalid PAT logs error and exits 1
    - Test SSM access denied logs error and exits 1
    - Test 5-minute idle timeout triggers deregistration
    - Test successful registration flow
    - _Requirements: 3.5, 3.6, 8.5_

- [x] 5. Checkpoint - Ensure runner container tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement CloudFormation template
  - [x] 6.1 Define networking resources in CloudFormation
    - Create VPC with CIDR block
    - Create 2 public subnets across different AZs
    - Create Internet Gateway and attach to VPC
    - Create Route Table with 0.0.0.0/0 route to IGW
    - Associate subnets with route table
    - Create Security Group: all egress allowed, no ingress rules
    - _Requirements: 1.2, 1.3, 1.4_

  - [x] 6.2 Define ECS cluster and task definitions in CloudFormation
    - Create ECS Cluster with Fargate capacity provider
    - Create x86_64 Task Definition with `runtimePlatform` set to X86_64/LINUX
    - Create arm64 Task Definition with `runtimePlatform` set to ARM64/LINUX
    - Configure CPU (default 2048), memory (default 4096), and ephemeral storage (default 30 GiB, min 21)
    - Add container definition referencing ECR image with environment variables and secrets (PAT from SSM)
    - Configure CloudWatch Logs log driver
    - Add template parameters: CpuAllocation, MemoryAllocation, EphemeralStorage
    - _Requirements: 1.1, 2.1, 2.2, 5.6, 8.3_

  - [x] 6.3 Define IAM roles in CloudFormation
    - Create Task Execution Role with policies for ECR pull and CloudWatch Logs
    - Create Task Role with SSM GetParameter permission for PAT parameter
    - _Requirements: 1.5_

  - [x] 6.4 Define supporting resources in CloudFormation
    - Create ECR Repository resource
    - Create SSM Parameter (SecureString) for PAT with NoEcho parameter input
    - Create SSM Parameter for webhook secret with NoEcho parameter input
    - Add template parameters: StackName, PATValue, WebhookSecret
    - _Requirements: 3.1_

  - [x] 6.5 Define API Gateway and Lambda resources in CloudFormation
    - Create Lambda function resource referencing webhook handler code
    - Create Lambda execution role with ECS RunTask, SSM GetParameter, and CloudWatch Logs permissions
    - Create API Gateway V2 (HTTP API) with POST route for webhook
    - Create Lambda integration and permission for API Gateway invocation
    - Pass environment variables to Lambda: cluster ARN, task definition ARNs, subnet IDs, security group ID, webhook secret parameter name
    - Output the API Gateway endpoint URL
    - _Requirements: 4.1, 4.2_

  - [x] 6.6 Write smoke tests for CloudFormation template
    - Validate template contains ECS Cluster with Fargate provider
    - Validate template contains VPC with 2+ public subnets
    - Validate template contains security group with egress-only rules
    - Validate template contains x86_64 and arm64 Task Definitions
    - Validate template contains ephemeral storage ≥ 20 GiB
    - Validate template contains SSM parameter for PAT
    - Validate entrypoint script includes --ephemeral flag
    - _Requirements: 1.1, 1.2, 1.4, 2.1, 2.2, 5.6, 3.1, 8.1_

- [x] 7. Implement build and push scripts
  - [x] 7.1 Implement build-image.sh script
    - Accept optional version tag parameter (default: "latest")
    - Build multi-arch image using `docker buildx` for linux/amd64 and linux/arm64
    - Tag image with version identifier and `latest` tag
    - Display build progress and final image tags
    - _Requirements: 6.1, 6.3_

  - [x] 7.2 Implement push-image.sh script
    - Accept ECR repository URI and optional region parameter
    - Authenticate with ECR using `aws ecr get-login-password`
    - Push image tags to ECR repository
    - Error if ECR repository does not exist (display helpful message)
    - Error if ECR authentication fails
    - _Requirements: 6.2, 6.4, 6.5_

  - [x] 7.3 Write unit tests for build and push scripts
    - Test image tagged with version and latest
    - Test ECR auth called before push
    - Test error when ECR repository not found
    - _Requirements: 6.3, 6.4, 6.5_

- [x] 8. Implement infrastructure lifecycle scripts
  - [x] 8.1 Implement create-stack.sh script
    - Accept required parameters: stack name, AWS region, PAT value, webhook secret
    - Display usage message and exit 1 if required parameters missing
    - Validate CloudFormation template using `aws cloudformation validate-template`
    - Deploy stack using `aws cloudformation create-stack` or `deploy`
    - Wait for stack to reach terminal state (CREATE_COMPLETE or CREATE_FAILED)
    - Display stack events on failure and exit 1
    - Display outputs (API Gateway URL) on success
    - _Requirements: 7.1, 7.3, 7.5, 7.6, 7.7, 7.8_

  - [x] 8.2 Implement destroy-stack.sh script
    - Accept stack name parameter
    - Check if stack exists, display error and exit 1 if not found
    - Prompt user to type stack name for confirmation
    - Abort deletion if confirmation input does not match
    - Delete stack using `aws cloudformation delete-stack`
    - Wait for deletion to complete
    - _Requirements: 7.2, 7.4, 7.9_

  - [x] 8.3 Write unit tests for lifecycle scripts
    - Test missing params shows usage and exits 1
    - Test non-existent stack shows error and exits 1
    - Test confirmation mismatch aborts deletion
    - _Requirements: 7.7, 7.9, 7.4_

- [x] 9. Integration wiring and final validation
  - [x] 9.1 Wire Lambda handler with CloudFormation template
    - Ensure Lambda function code is packaged correctly (inline or S3 reference)
    - Verify environment variable references match CloudFormation resource outputs
    - Ensure IAM permissions are complete for Lambda to call ECS RunTask
    - Verify subnet and security group references are passed correctly
    - _Requirements: 4.1, 4.2, 4.5_

  - [x] 9.2 Wire entrypoint with task definitions
    - Verify task definition environment variables match entrypoint expectations (GITHUB_ORG, RUNNER_LABELS, PAT_SSM_PARAM, AWS_REGION)
    - Verify secrets configuration passes PAT from SSM to container
    - Ensure ephemeral storage and resource allocations are consistent between template and Dockerfile
    - Verify no persistent volume mounts in task definitions
    - _Requirements: 3.1, 3.2, 3.3, 8.1, 8.3, 8.4_

  - [x] 9.3 Write integration tests
    - Test end-to-end webhook → task launch flow (mocked ECS)
    - Test label routing selects correct task definition
    - Test full handler response codes for all scenarios
    - _Requirements: 4.2, 4.5, 4.6, 4.7_

- [x] 10. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The Lambda handler is implemented in Python; scripts are implemented in Bash
- CloudFormation template is YAML format
- Test framework: pytest + hypothesis for Python, bats for shell scripts

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "2.3", "2.5", "4.1", "6.1"] },
    {
      "id": 2,
      "tasks": ["2.2", "2.4", "2.6", "2.7", "4.2", "6.2", "6.3", "6.4"]
    },
    { "id": 3, "tasks": ["2.8", "4.3", "4.4", "6.5", "7.1", "7.2"] },
    { "id": 4, "tasks": ["2.9", "6.6", "7.3", "8.1", "8.2"] },
    { "id": 5, "tasks": ["8.3", "9.1", "9.2"] },
    { "id": 6, "tasks": ["9.3"] }
  ]
}
```
