#!/bin/bash
# Push the GitHub Actions runner Docker image to Amazon ECR.
set -euo pipefail

# Configuration
IMAGE_NAME="github-runner"

# Accept ECR repository URI as required first parameter
if [ -z "${1:-}" ]; then
    echo "ERROR: ECR repository URI is required."
    echo ""
    echo "Usage: $0 <ecr-repository-uri> [region] [version-tag]"
    echo ""
    echo "  ecr-repository-uri  Full ECR repository URI (e.g., 123456789012.dkr.ecr.us-east-1.amazonaws.com/github-runner)"
    echo "  region              AWS region (default: AWS_DEFAULT_REGION or us-east-1)"
    echo "  version-tag         Image version tag (default: latest)"
    exit 1
fi

ECR_REPO_URI="$1"

# Accept optional region parameter (default to AWS_DEFAULT_REGION or us-east-1)
AWS_REGION="${2:-${AWS_DEFAULT_REGION:-us-east-1}}"

# Accept optional version tag parameter (default: "latest")
VERSION_TAG="${3:-latest}"

# Extract the ECR registry URI (everything before the first slash)
ECR_REGISTRY="${ECR_REPO_URI%%/*}"

# Extract the repository name (everything after the first slash)
ECR_REPO_NAME="${ECR_REPO_URI#*/}"

# Resolve script directory to find project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "============================================"
echo "  GitHub Runner Image Push to ECR"
echo "============================================"
echo ""
echo "ECR Repository:  ${ECR_REPO_URI}"
echo "Registry:        ${ECR_REGISTRY}"
echo "Repository name: ${ECR_REPO_NAME}"
echo "Region:          ${AWS_REGION}"
echo "Version tag:     ${VERSION_TAG}"
echo ""

# Ensure we're working from the project root
cd "${PROJECT_ROOT}"

# Verify ECR repository exists
echo ">> Verifying ECR repository exists..."
if ! aws ecr describe-repositories \
    --repository-names "${ECR_REPO_NAME}" \
    --region "${AWS_REGION}" >/dev/null 2>&1; then
    echo "ERROR: ECR repository '${ECR_REPO_NAME}' does not exist in region '${AWS_REGION}'."
    echo ""
    echo "Please create the repository first. If using the CloudFormation stack,"
    echo "deploy the stack before pushing images. Otherwise, create it manually:"
    echo ""
    echo "  aws ecr create-repository --repository-name ${ECR_REPO_NAME} --region ${AWS_REGION}"
    exit 1
fi
echo "   Repository verified: ${ECR_REPO_NAME}"

# Authenticate with ECR
echo ""
echo ">> Authenticating with ECR..."
if ! aws ecr get-login-password --region "${AWS_REGION}" | \
    docker login --username AWS --password-stdin "${ECR_REGISTRY}" 2>/dev/null; then
    echo "ERROR: Failed to authenticate with ECR registry '${ECR_REGISTRY}'."
    echo ""
    echo "Ensure your AWS credentials are configured and have permissions for:"
    echo "  - ecr:GetAuthorizationToken"
    echo "  - ecr:BatchGetImage"
    echo "  - ecr:InitiateLayerUpload"
    echo "  - ecr:UploadLayerPart"
    echo "  - ecr:CompleteLayerUpload"
    echo "  - ecr:PutImage"
    exit 1
fi
echo "   Authentication successful."

# Tag local image for ECR
echo ""
echo ">> Tagging image for ECR..."
docker tag "${IMAGE_NAME}:${VERSION_TAG}" "${ECR_REPO_URI}:${VERSION_TAG}"
echo "   Tagged: ${ECR_REPO_URI}:${VERSION_TAG}"

if [ "${VERSION_TAG}" != "latest" ]; then
    docker tag "${IMAGE_NAME}:latest" "${ECR_REPO_URI}:latest"
    echo "   Tagged: ${ECR_REPO_URI}:latest"
fi

# Push image tags to ECR
echo ""
echo ">> Pushing image to ECR..."
docker push "${ECR_REPO_URI}:${VERSION_TAG}"
echo "   Pushed: ${ECR_REPO_URI}:${VERSION_TAG}"

if [ "${VERSION_TAG}" != "latest" ]; then
    docker push "${ECR_REPO_URI}:latest"
    echo "   Pushed: ${ECR_REPO_URI}:latest"
fi

echo ""
echo "============================================"
echo "  Push Complete"
echo "============================================"
echo ""
echo "Image pushed to ECR:"
echo "  - ${ECR_REPO_URI}:${VERSION_TAG}"
if [ "${VERSION_TAG}" != "latest" ]; then
    echo "  - ${ECR_REPO_URI}:latest"
fi
echo ""
