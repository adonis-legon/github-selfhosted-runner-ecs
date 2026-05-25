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

# Build multi-arch image with version tag and latest tag
echo ""
echo ">> Building multi-arch image..."
echo "   Platforms: ${PLATFORMS}"
echo ""

BUILD_TAGS=("-t" "${IMAGE_NAME}:${VERSION_TAG}")
if [ "${VERSION_TAG}" != "latest" ]; then
    BUILD_TAGS+=("-t" "${IMAGE_NAME}:latest")
fi

docker buildx build \
    --platform "${PLATFORMS}" \
    "${BUILD_TAGS[@]}" \
    -f "${DOCKERFILE_PATH}" \
    "${BUILD_CONTEXT}"

echo ""
echo "============================================"
echo "  Build Complete"
echo "============================================"
echo ""
echo "Image tags:"
echo "  - ${IMAGE_NAME}:${VERSION_TAG}"
if [ "${VERSION_TAG}" != "latest" ]; then
    echo "  - ${IMAGE_NAME}:latest"
fi
echo ""
echo "Platforms: ${PLATFORMS}"
echo ""
