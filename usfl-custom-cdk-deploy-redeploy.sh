#!/bin/bash
set -e

if [[ -z "$1" ]]; then
    echo "Usage: $0 <path to your set-[AWS_PROFILE_NAME].sh script file>"
    exit 1
fi

# Check that the specified file exists
if [[ ! -f "$1" ]]; then
    echo "ERROR: File '$1' does not exist."
    exit 1
fi

echo "Checking AWS identity..."
echo ""
aws sts get-caller-identity
echo ""

read -p "Is this the correct AWS identity? [Y/n] " response
response=${response:-Y}

if [[ ! "$response" =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled."
    exit 1
fi

echo ""
deployment_type="${AWS_DEPLOYMENT_TYPE}"
echo "deployment_type=${deployment_type}"
read -p "press any key to continue"

echo ""
echo "Proceeding with ${deployment_type} deployment..."
echo "Cleaning CDK output directory..."
rm -rf ./cdk.out/

echo "Clearing Docker build cache to force image rebuild..."
docker builder prune -af 2>/dev/null || true
docker system prune -af 2>/dev/null || true

echo ""
echo "Starting CDK deployment with forced image rebuild..."
echo "Build timestamp will be embedded in Docker image to ensure new code is deployed."

echo "destination_bucket=${AWS_DESTINATION_BUCKET_NAME}"
read -p "Press any key to continue"

# Run CDK deploy and capture exit code
set +e
cdk deploy PDFAccessibility -c destination_bucket=${AWS_DESTINATION_BUCKET_NAME} --require-approval never --force
CDK_EXIT_CODE=$?
set -e

echo ""
echo "Deployment completed at: $(date '+%Y-%m-%d %H:%M:%S')"

# Notify based on success/failure
if [ $CDK_EXIT_CODE -eq 0 ]; then
    # Discover the project bucket name from CloudFormation (with retry)
    AWS_PROJECT_S3_BUCKET_NAME=""
    for attempt in 1 2 3; do
        AWS_PROJECT_S3_BUCKET_NAME=$(aws cloudformation describe-stack-resources \
            --stack-name PDFAccessibility \
            --query "StackResources[?ResourceType=='AWS::S3::Bucket'].PhysicalResourceId" \
            --output text 2>/dev/null)
        if [ -n "$AWS_PROJECT_S3_BUCKET_NAME" ]; then
            break
        fi
        echo "Waiting for stack resources to propagate (attempt $attempt/3)..."
        sleep 30
    done

    if [ -z "$AWS_PROJECT_S3_BUCKET_NAME" ]; then
        echo "ERROR: Could not discover project S3 bucket name from stack, you'll need to manually edit your ~/set-[AWS_PROFILE_NAME].sh script and set the AWS_PROJECT_S3_BUCKET_NAME value"
    else
        echo "Discovered/rediscovered project bucket: $AWS_PROJECT_S3_BUCKET_NAME"

        # Update the value in the target script (only if a target file was provided)
        if [ -n "$1" ] && [ -f "$1" ]; then
            sed -i "s|^export AWS_PROJECT_S3_BUCKET_NAME=.*|export AWS_PROJECT_S3_BUCKET_NAME=\"${AWS_PROJECT_S3_BUCKET_NAME}\"|" "$1"
            echo "Updated $1 with project bucket name"
        else
            echo "No target script provided or file not found — skipping auto-update"
            echo "Manually set: export AWS_PROJECT_S3_BUCKET_NAME=\"${AWS_PROJECT_S3_BUCKET_NAME}\""
        fi

        # Create default S3 folder structure
        echo ""
        echo "Creating default folder structure in bucket: $AWS_PROJECT_S3_BUCKET_NAME"

        # Original software folders
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "pdf/" > /dev/null
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "temp/" > /dev/null
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "result/" > /dev/null

        # Fork-specific folders
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "queue/" > /dev/null
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "reports/" > /dev/null
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "failed/" > /dev/null
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "logs-public-access/" > /dev/null

        # Reports subfolders
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "reports/deletion_reports/" > /dev/null
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "reports/failed_summary_listings/" > /dev/null
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "reports/failure_analysis_summary/" > /dev/null
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "reports/failure_analysis/" > /dev/null
        aws s3api put-object --bucket "$AWS_PROJECT_S3_BUCKET_NAME" --key "reports/pdf_processing_reports/" > /dev/null

        echo "✓ Folder structure created successfully"
    fi

    # Set SSM parameters to defaults
    echo ""
    echo "Running ./bin/set-all-ssm-parameters.sh --defaults to configure SSM parameters..."
    ./bin/set-all-ssm-parameters.sh --defaults
    echo "✓ All other ssm parameters are set"

    echo ""
    echo "✅ ${deployment_type} deployment SUCCEEDED!"
else
    echo ""
    echo "❌ ${deployment_type} deployment FAILED with exit code $CDK_EXIT_CODE"
    exit $CDK_EXIT_CODE
fi

echo ""
echo "IMPORTANT: After deployment, verify the new image is running:"
echo "1. Check ECR for new image with recent timestamp"
echo "2. Check ECS task definition points to new image URI"
echo "3. If tasks are still using old code, stop them in ECS console"
echo ""
echo "To verify rate limiter is working, check CloudWatch logs for:"
echo "  - 'Initial jitter:' messages (proves new code is running)"
echo "  - 'RPM (global)' messages (proves combined counter is active)"
echo ""
