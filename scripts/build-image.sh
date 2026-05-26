#!/bin/bash
# Build the GitHub Actions runner Docker image for multiple architectures.
set -euo pipefail

# Configuration
IMAGE_NAME="github-runner"
PLATFORMS="linux/amd64,linux/arm64"
DOCKERFILE_PATH="docker/Dockerfile"
BUILD_CONTEXT="docker"

# Accept optional version tag parameter (default: "latest")
VERSION_TAG="${1:-latest}"

# Accept optional ECR repository URI for direct push (default: empty = local build)
ECR_REPO_URI="${2:-}"

# Resolve script directory to find project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "============================================"
echo "  GitHub Runner Image Builder"
echo "============================================"
echo ""
echo "Image name:    ${IMAGE_NAME}"
echo "Version tag:   ${VERSION_TAG}"
echo "Platforms:     ${PLATFORMS}"
echo "Dockerfile:    ${DOCKERFILE_PATH}"
if [ -n "${ECR_REPO_URI}" ]; then
    echo "Push target:   ${ECR_REPO_URI}"
fi
echo ""

# Ensure we're working from the project root
cd "${PROJECT_ROOT}"

# Verify Dockerfile exists
if [ ! -f "${DOCKERFILE_PATH}" ]; then
    echo "ERROR: Dockerfile not found at ${DOCKERFILE_PATH}"
    exit 1
fi

# Ensure buildx builder is available
echo ">> Setting up docker buildx builder..."
BUILDER_NAME="github-runner-builder"
if ! docker buildx inspect "${BUILDER_NAME}" >/dev/null 2>&1; then
    docker buildx create --name "${BUILDER_NAME}" --use
    echo "   Created new builder: ${BUILDER_NAME}"
else
    docker buildx use "${BUILDER_NAME}"
    echo "   Using existing builder: ${BUILDER_NAME}"
fi

# Build multi-arch image
echo ""
echo ">> Building multi-arch image..."
echo "   Platforms: ${PLATFORMS}"
echo ""

if [ -n "${ECR_REPO_URI}" ]; then
    # Build and push directly to ECR (multi-arch supported)
    ECR_REGISTRY="${ECR_REPO_URI%%/*}"
    AWS_REGION="${3:-${AWS_DEFAULT_REGION:-us-east-1}}"

    echo ">> Authenticating with ECR..."
    if ! aws ecr get-login-password --region "${AWS_REGION}" | \
        docker login --username AWS --password-stdin "${ECR_REGISTRY}" 2>/dev/null; then
        echo "ERROR: Failed to authenticate with ECR registry '${ECR_REGISTRY}'."
        exit 1
    fi
    echo "   Authentication successful."
    echo ""

    BUILD_TAGS=("-t" "${ECR_REPO_URI}:${VERSION_TAG}")
    if [ "${VERSION_TAG}" != "latest" ]; then
        BUILD_TAGS+=("-t" "${ECR_REPO_URI}:latest")
    fi

    docker buildx build \
        --platform "${PLATFORMS}" \
        "${BUILD_TAGS[@]}" \
        --push \
        -f "${DOCKERFILE_PATH}" \
        "${BUILD_CONTEXT}"

    echo ""
    echo "============================================"
    echo "  Build & Push Complete"
    echo "============================================"
    echo ""
    echo "Image pushed to ECR:"
    echo "  - ${ECR_REPO_URI}:${VERSION_TAG}"
    if [ "${VERSION_TAG}" != "latest" ]; then
        echo "  - ${ECR_REPO_URI}:latest"
    fi
else
    # Local build only (loads into local daemon, single platform)
    LOCAL_PLATFORM="linux/$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')"
    echo "   NOTE: Loading to local daemon (single platform: ${LOCAL_PLATFORM})"
    echo ""

    BUILD_TAGS=("-t" "${IMAGE_NAME}:${VERSION_TAG}")
    if [ "${VERSION_TAG}" != "latest" ]; then
        BUILD_TAGS+=("-t" "${IMAGE_NAME}:latest")
    fi

    docker buildx build \
        --platform "${LOCAL_PLATFORM}" \
        "${BUILD_TAGS[@]}" \
        --load \
        -f "${DOCKERFILE_PATH}" \
        "${BUILD_CONTEXT}"

    echo ""
    echo "============================================"
    echo "  Build Complete (local)"
    echo "============================================"
    echo ""
    echo "Image tags:"
    echo "  - ${IMAGE_NAME}:${VERSION_TAG}"
    if [ "${VERSION_TAG}" != "latest" ]; then
        echo "  - ${IMAGE_NAME}:latest"
    fi
fi

echo ""
echo "Platforms: ${PLATFORMS}"
echo ""
