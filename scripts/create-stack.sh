#!/bin/bash
# Deploy the GitHub Runners ECS CloudFormation stack.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_FILE="${SCRIPT_DIR}/../cloudformation/template.yaml"

usage() {
    cat <<EOF
Usage: $(basename "$0") --stack-name <name> --region <region> --pat <pat-value> --webhook-secret <secret> --github-org <org-or-username> [options]

Required parameters:
  --stack-name       Name of the CloudFormation stack
  --region           AWS region to deploy the stack in
  --pat              GitHub Personal Access Token value
  --webhook-secret   GitHub webhook shared secret
  --github-org       GitHub organization or username

Optional parameters:
  --github-repo      GitHub repository name (for repo-level runners; omit for org-level)
  --cpu              Fargate CPU units (default: 2048)
  --memory           Fargate memory in MiB (default: 4096)
  --storage          Ephemeral storage in GiB (default: 30, min: 21)
  --help             Show this help message

Examples:
  # Org-level runners
  $(basename "$0") --stack-name my-runners --region us-east-1 --pat ghp_xxxx --webhook-secret mysecret --github-org my-org

  # Repo-level runners (personal account)
  $(basename "$0") --stack-name my-runners --region us-east-1 --pat ghp_xxxx --webhook-secret mysecret --github-org my-username --github-repo my-repo
EOF
    exit 1
}

# Parse arguments
STACK_NAME=""
REGION=""
PAT_VALUE=""
WEBHOOK_SECRET=""
GITHUB_ORG=""
GITHUB_REPO=""
CPU="2048"
MEMORY="4096"
STORAGE="30"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stack-name)
            STACK_NAME="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --pat)
            PAT_VALUE="$2"
            shift 2
            ;;
        --webhook-secret)
            WEBHOOK_SECRET="$2"
            shift 2
            ;;
        --github-org)
            GITHUB_ORG="$2"
            shift 2
            ;;
        --github-repo)
            GITHUB_REPO="$2"
            shift 2
            ;;
        --cpu)
            CPU="$2"
            shift 2
            ;;
        --memory)
            MEMORY="$2"
            shift 2
            ;;
        --storage)
            STORAGE="$2"
            shift 2
            ;;
        --help)
            usage
            ;;
        *)
            echo "Error: Unknown parameter '$1'"
            usage
            ;;
    esac
done

# Validate required parameters
MISSING_PARAMS=()
if [[ -z "$STACK_NAME" ]]; then
    MISSING_PARAMS+=("--stack-name")
fi
if [[ -z "$REGION" ]]; then
    MISSING_PARAMS+=("--region")
fi
if [[ -z "$PAT_VALUE" ]]; then
    MISSING_PARAMS+=("--pat")
fi
if [[ -z "$WEBHOOK_SECRET" ]]; then
    MISSING_PARAMS+=("--webhook-secret")
fi
if [[ -z "$GITHUB_ORG" ]]; then
    MISSING_PARAMS+=("--github-org")
fi

if [[ ${#MISSING_PARAMS[@]} -gt 0 ]]; then
    echo "Error: Missing required parameters: ${MISSING_PARAMS[*]}"
    echo ""
    usage
fi

# Validate template file exists
if [[ ! -f "$TEMPLATE_FILE" ]]; then
    echo "Error: CloudFormation template not found at: $TEMPLATE_FILE"
    exit 1
fi

echo "=== GitHub Runners ECS Stack Deployment ==="
echo "Stack Name:  $STACK_NAME"
echo "Region:      $REGION"
echo "Template:    $TEMPLATE_FILE"
echo ""

# Step 1: Validate CloudFormation template
echo "Validating CloudFormation template..."
if ! aws cloudformation validate-template \
    --template-body "file://${TEMPLATE_FILE}" \
    --region "$REGION" > /dev/null 2>&1; then

    echo "Error: Template validation failed."
    echo "Details:"
    aws cloudformation validate-template \
        --template-body "file://${TEMPLATE_FILE}" \
        --region "$REGION" 2>&1 || true
    exit 1
fi
echo "Template validation successful."
echo ""

# Step 2: Package the template (uploads Lambda code to S3)
echo "Packaging CloudFormation template (uploading Lambda code)..."
PACKAGED_TEMPLATE="/tmp/${STACK_NAME}-packaged-template.yaml"
S3_BUCKET="${STACK_NAME}-lambda-artifacts-${REGION}"

# Create the S3 bucket for Lambda artifacts if it doesn't exist
if ! aws s3 ls "s3://${S3_BUCKET}" --region "$REGION" > /dev/null 2>&1; then
    echo "Creating S3 bucket for Lambda artifacts: ${S3_BUCKET}"
    if [[ "$REGION" == "us-east-1" ]]; then
        aws s3 mb "s3://${S3_BUCKET}" --region "$REGION" > /dev/null
    else
        aws s3 mb "s3://${S3_BUCKET}" --region "$REGION" > /dev/null
    fi
fi

aws cloudformation package \
    --template-file "$TEMPLATE_FILE" \
    --s3-bucket "$S3_BUCKET" \
    --output-template-file "$PACKAGED_TEMPLATE" \
    --region "$REGION" > /dev/null

echo "Template packaged successfully."
echo ""

# Step 3: Build parameter list
PARAMS="ParameterKey=PATValue,ParameterValue=${PAT_VALUE}"
PARAMS="${PARAMS} ParameterKey=WebhookSecret,ParameterValue=${WEBHOOK_SECRET}"
PARAMS="${PARAMS} ParameterKey=GitHubOrg,ParameterValue=${GITHUB_ORG}"

if [[ -n "$GITHUB_REPO" ]]; then
    PARAMS="${PARAMS} ParameterKey=GitHubRepo,ParameterValue=${GITHUB_REPO}"
fi
if [[ "$CPU" != "2048" ]]; then
    PARAMS="${PARAMS} ParameterKey=CpuAllocation,ParameterValue=${CPU}"
fi
if [[ "$MEMORY" != "4096" ]]; then
    PARAMS="${PARAMS} ParameterKey=MemoryAllocation,ParameterValue=${MEMORY}"
fi
if [[ "$STORAGE" != "30" ]]; then
    PARAMS="${PARAMS} ParameterKey=EphemeralStorage,ParameterValue=${STORAGE}"
fi

# Step 4: Deploy the stack
echo "Deploying CloudFormation stack '${STACK_NAME}'..."
aws cloudformation create-stack \
    --stack-name "$STACK_NAME" \
    --template-body "file://${PACKAGED_TEMPLATE}" \
    --parameters $PARAMS \
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
    --region "$REGION" > /dev/null

echo "Stack creation initiated. Waiting for completion..."
echo ""

# Step 5: Wait for stack to reach terminal state
while true; do
    STACK_STATUS=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].StackStatus' \
        --output text 2>/dev/null || echo "DESCRIBE_FAILED")

    case "$STACK_STATUS" in
        CREATE_COMPLETE)
            echo "Stack creation completed successfully!"
            echo ""
            break
            ;;
        CREATE_FAILED|ROLLBACK_COMPLETE|ROLLBACK_FAILED)
            echo "Stack creation failed with status: $STACK_STATUS"
            echo ""
            echo "=== Stack Events (failure details) ==="
            aws cloudformation describe-stack-events \
                --stack-name "$STACK_NAME" \
                --region "$REGION" \
                --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
                --output table 2>/dev/null || true
            echo ""
            exit 1
            ;;
        DESCRIBE_FAILED)
            echo "Error: Unable to describe stack '$STACK_NAME'. It may have been deleted during rollback."
            exit 1
            ;;
        *)
            # Still in progress (CREATE_IN_PROGRESS, etc.)
            printf "  Status: %s\r" "$STACK_STATUS"
            sleep 10
            ;;
    esac
done

# Step 6: Display stack outputs
echo "=== Stack Outputs ==="
aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
    --output table 2>/dev/null || echo "No outputs available."

echo ""
echo "Deployment complete. Configure your GitHub webhook to point to the API Gateway URL above."
