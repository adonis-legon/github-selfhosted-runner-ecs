# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.1] - 2026-05-26

### Added

- CloudFormation template defining full infrastructure: VPC, ECS cluster, Lambda, API Gateway, ECR, SSM parameters
- Multi-architecture support (x86_64 and arm64) with separate ECS task definitions
- Lambda webhook handler with HMAC-SHA256 signature validation and label-based architecture routing
- Ephemeral runner container image (Ubuntu 22.04) with GitHub Actions runner agent
- Kaniko integration for unprivileged container image builds on Fargate
- Entrypoint script with PAT retrieval, registration token retry logic, idle timeout, and deregistration
- `build-image.sh` script: multi-arch build and push to ECR in one step
- `create-stack.sh` script: template validation, packaging, and deployment with wait
- `destroy-stack.sh` script: stack deletion with confirmation prompt
- Support for both organization-level and repository-level (personal account) runner registration
- Full test suite: unit tests, property-based tests (Hypothesis), smoke tests, and integration tests
- README with architecture diagram, deployment guide, and workflow integration examples
