# GitHub Actions Self-Hosted Runner Image
# Multi-arch support: linux/amd64 and linux/arm64
# Base: Ubuntu 22.04 with runner agent and build tools
#
# Build with: docker buildx build --platform linux/amd64,linux/arm64 -t github-runner .
#
# Requirements: 2.5, 5.1, 5.2, 5.3, 5.5

# Stage 1: Get Kaniko executor from the official image
FROM gcr.io/kaniko-project/executor:v1.23.2 AS kaniko

FROM ubuntu:22.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# TARGETARCH is set automatically by docker buildx (amd64 or arm64)
ARG TARGETARCH

# -----------------------------------------------------------
# Install base dependencies (Req 5.3: git, curl, jq)
# -----------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    apt-transport-https \
    ca-certificates \
    curl \
    git \
    gnupg \
    iptables \
    jq \
    lsb-release \
    software-properties-common \
    sudo \
    unzip \
    wget \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------
# Install Docker CLI and buildx plugin (Req 5.1, 5.2, 5.5)
# Provides container build capability within Fargate (no --privileged)
# Uses Docker CLI + buildx which can work with remote builders
# -----------------------------------------------------------
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
    https://download.docker.com/linux/ubuntu jammy stable" \
    > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    docker-ce-cli \
    docker-buildx-plugin \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------
# Install Kaniko for unprivileged image builds on Fargate
# Copied from the official Kaniko image (multi-stage build)
# Kaniko builds container images from Dockerfiles without a Docker
# daemon or privileged mode.
# -----------------------------------------------------------
COPY --from=kaniko /kaniko/executor /kaniko/executor
COPY --from=kaniko /kaniko/docker-credential-gcr /kaniko/docker-credential-gcr
COPY --from=kaniko /kaniko/docker-credential-ecr-login /kaniko/docker-credential-ecr-login
RUN ln -s /kaniko/executor /usr/local/bin/executor \
    && ln -s /kaniko/executor /usr/local/bin/kaniko \
    && ln -s /kaniko/docker-credential-ecr-login /usr/local/bin/docker-credential-ecr-login

# Configure Kaniko for ECR credential helper
RUN mkdir -p /kaniko/.docker \
    && echo '{"credsStore":"ecr-login"}' > /kaniko/.docker/config.json

# -----------------------------------------------------------
# Install AWS CLI for SSM Parameter Store access (needed by entrypoint)
# -----------------------------------------------------------
RUN if [ "$TARGETARCH" = "arm64" ]; then AWSARCH="aarch64"; else AWSARCH="x86_64"; fi \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWSARCH}.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install \
    && rm -rf /tmp/aws /tmp/awscliv2.zip

# -----------------------------------------------------------
# Create non-root user for runner execution (security best practice)
# -----------------------------------------------------------
RUN useradd -m -s /bin/bash -G sudo runner \
    && echo "runner ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/runner \
    && chmod 0440 /etc/sudoers.d/runner

# Set up runner directory
WORKDIR /home/runner

# -----------------------------------------------------------
# Download and install GitHub Actions runner agent (latest stable)
# Uses TARGETARCH for multi-arch support (Req 2.5)
# -----------------------------------------------------------
RUN RUNNER_VERSION=$(curl -s https://api.github.com/repos/actions/runner/releases/latest \
    | jq -r '.tag_name' | sed 's/^v//') \
    && if [ "$TARGETARCH" = "arm64" ]; then RUNNER_ARCH="arm64"; else RUNNER_ARCH="x64"; fi \
    && curl -fsSL \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz" \
    -o /tmp/runner.tar.gz \
    && tar -xzf /tmp/runner.tar.gz -C /home/runner \
    && rm /tmp/runner.tar.gz \
    && /home/runner/bin/installdependencies.sh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Fix ownership for runner user
RUN chown -R runner:runner /home/runner

# Copy entrypoint script (implemented in task 4.2)
COPY entrypoint.sh /home/runner/entrypoint.sh
RUN chmod +x /home/runner/entrypoint.sh

# Switch to non-root user
USER runner

# Prepare Docker buildx directory for runner user (Req 5.5)
RUN mkdir -p /home/runner/.docker/cli-plugins

ENTRYPOINT ["/home/runner/entrypoint.sh"]
