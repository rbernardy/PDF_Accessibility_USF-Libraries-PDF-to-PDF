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

# Check Step Function executions - see if any are still RUNNING
echo ""
echo "=== RUNNING executions ==="
aws stepfunctions list-executions \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --status-filter RUNNING \
  --max-results 10

# Check for FAILED executions
echo ""
echo "=== FAILED executions ==="
aws stepfunctions list-executions \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --status-filter FAILED \
  --max-results 20
