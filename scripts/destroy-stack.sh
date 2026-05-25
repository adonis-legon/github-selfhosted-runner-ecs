#!/bin/bash
# Destroy the GitHub Runners ECS CloudFormation stack.
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") --stack-name <name> [options]

Required parameters:
  --stack-name       Name of the CloudFormation stack to destroy

Optional parameters:
  --region           AWS region where the stack is deployed (default: AWS_DEFAULT_REGION or us-east-1)
  --help             Show this help message

Example:
  $(basename "$0") --stack-name my-runners --region us-east-1
EOF
    exit 1
}

# Parse arguments
STACK_NAME=""
REGION="${AWS_DEFAULT_REGION:-us-east-1}"

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
if [[ -z "$STACK_NAME" ]]; then
    echo "Error: Missing required parameter: --stack-name"
    echo ""
    usage
fi

echo "=== GitHub Runners ECS Stack Destruction ==="
echo "Stack Name:  $STACK_NAME"
echo "Region:      $REGION"
echo ""

# Step 1: Check if stack exists
echo "Checking if stack '${STACK_NAME}' exists..."
if ! aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" > /dev/null 2>&1; then
    echo "Error: Stack '${STACK_NAME}' not found in region '${REGION}'."
    exit 1
fi
echo "Stack found."
echo ""

# Step 2: Prompt user for confirmation
echo "WARNING: This will permanently delete the stack '${STACK_NAME}' and all associated resources."
echo ""
printf "Type the stack name to confirm deletion: "
read -r CONFIRMATION

if [[ "$CONFIRMATION" != "$STACK_NAME" ]]; then
    echo ""
    echo "Confirmation does not match. Aborting deletion."
    exit 0
fi

echo ""

# Step 3: Delete the stack
echo "Deleting CloudFormation stack '${STACK_NAME}'..."
aws cloudformation delete-stack \
    --stack-name "$STACK_NAME" \
    --region "$REGION"

echo "Stack deletion initiated. Waiting for completion..."
echo ""

# Step 4: Wait for deletion to complete
while true; do
    STACK_STATUS=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].StackStatus' \
        --output text 2>/dev/null || echo "DELETE_COMPLETE")

    case "$STACK_STATUS" in
        DELETE_COMPLETE)
            echo "Stack '${STACK_NAME}' has been successfully deleted."
            break
            ;;
        DELETE_FAILED)
            echo "Stack deletion failed with status: $STACK_STATUS"
            echo ""
            echo "=== Stack Events (failure details) ==="
            aws cloudformation describe-stack-events \
                --stack-name "$STACK_NAME" \
                --region "$REGION" \
                --query 'StackEvents[?ResourceStatus==`DELETE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
                --output table 2>/dev/null || true
            echo ""
            exit 1
            ;;
        *)
            # Still in progress (DELETE_IN_PROGRESS, etc.)
            printf "  Status: %s\r" "$STACK_STATUS"
            sleep 10
            ;;
    esac
done

echo ""
echo "Destruction complete."
