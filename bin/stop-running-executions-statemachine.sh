#!/bin/bash

# Resolve state machine ARN dynamically from CloudFormation stack
STATE_MACHINE_ARN=$(aws cloudformation describe-stack-resources \
  --stack-name PDFAccessibility \
  --query "StackResources[?ResourceType=='AWS::StepFunctions::StateMachine'].PhysicalResourceId" \
  --output text)

if [ -z "$STATE_MACHINE_ARN" ]; then
    echo "ERROR: Could not discover State Machine ARN from PDFAccessibility stack"
    exit 1
fi

echo "State Machine ARN: $STATE_MACHINE_ARN"

# List running executions
echo ""
echo "=== RUNNING executions ==="
aws stepfunctions list-executions \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --status-filter RUNNING \
  --query 'executions[].executionArn' \
  --output text

# Stop all running executions
echo ""
echo "Stopping all running executions..."
aws stepfunctions list-executions \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --status-filter RUNNING \
  --query 'executions[].executionArn' \
  --output text | xargs -n1 aws stepfunctions stop-execution --execution-arn
