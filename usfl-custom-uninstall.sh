#!/bin/bash
set -e

echo "========================================"
echo "  USF Libraries Fork — UNINSTALL"
echo "========================================"
echo ""

# ==========================================
# CONFIRM AWS IDENTITY
# ==========================================
echo "Checking AWS identity..."
echo ""
aws sts get-caller-identity
echo ""

read -p "Is this the correct AWS identity? [Y/n] " response
response=${response:-Y}

if [[ ! "$response" =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 1
fi

# ==========================================
# DESTRUCTIVE ACTION WARNING
# ==========================================
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  WARNING: This script will DELETE all resources created     ║"
echo "║  by the installation, including:                           ║"
echo "║                                                            ║"
echo "║  - The PDFAccessibility CloudFormation stack               ║"
echo "║  - All S3 buckets and their contents (PDFs, results, etc.) ║"
echo "║  - All SSM parameters (/pdf-processing/*)                  ║"
echo "║  - Secrets Manager secrets (/myapp/*)                      ║"
echo "║  - ECR repositories and images                             ║"
echo "║  - CloudWatch log groups                                   ║"
echo "║  - DynamoDB tables                                         ║"
echo "║  - The CDK bootstrap stack (optional)                      ║"
echo "║                                                            ║"
echo "║  THIS CANNOT BE UNDONE.                                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
read -p "Do you understand and want to proceed with deletion? (type 'yes' to confirm): " confirm

if [[ "$confirm" != "yes" ]]; then
    echo "Cancelled."
    exit 1
fi

echo ""
echo "Starting uninstall..."
echo ""

# ==========================================
# 1. DISCOVER RESOURCES
# ==========================================
echo "--- Discovering resources ---"

# Discover project S3 bucket from CloudFormation
PROJECT_BUCKET=$(aws cloudformation describe-stack-resources \
    --stack-name PDFAccessibility \
    --query "StackResources[?ResourceType=='AWS::S3::Bucket'].PhysicalResourceId" \
    --output text 2>/dev/null || echo "")

if [ -n "$PROJECT_BUCKET" ]; then
    echo "Found project bucket: $PROJECT_BUCKET"
else
    echo "Could not discover project bucket from stack (may already be deleted)"
fi

# Destination bucket from environment
DEST_BUCKET="${AWS_DESTINATION_BUCKET_NAME:-}"
if [ -n "$DEST_BUCKET" ]; then
    echo "Found destination bucket: $DEST_BUCKET"
else
    echo "AWS_DESTINATION_BUCKET_NAME not set — skipping destination bucket cleanup"
fi

echo ""

# ==========================================
# 2. EMPTY AND DELETE S3 BUCKETS
# ==========================================
echo "--- Cleaning up S3 buckets ---"

empty_and_delete_bucket() {
    local bucket="$1"
    if aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
        echo "Emptying bucket: $bucket"
        aws s3 rm "s3://$bucket" --recursive
        # Also remove any versioned objects
        echo "Removing versioned objects from: $bucket"
        aws s3api list-object-versions --bucket "$bucket" \
            --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
            --output json 2>/dev/null | \
            python3 -c "
import sys, json
data = json.load(sys.stdin)
objects = data.get('Objects') or []
if objects:
    print(json.dumps({'Objects': objects, 'Quiet': True}))
" | while read -r delete_json; do
            if [ -n "$delete_json" ]; then
                aws s3api delete-objects --bucket "$bucket" --delete "$delete_json" 2>/dev/null || true
            fi
        done
        # Remove delete markers
        aws s3api list-object-versions --bucket "$bucket" \
            --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' \
            --output json 2>/dev/null | \
            python3 -c "
import sys, json
data = json.load(sys.stdin)
objects = data.get('Objects') or []
if objects:
    print(json.dumps({'Objects': objects, 'Quiet': True}))
" | while read -r delete_json; do
            if [ -n "$delete_json" ]; then
                aws s3api delete-objects --bucket "$bucket" --delete "$delete_json" 2>/dev/null || true
            fi
        done
        echo "Deleting bucket: $bucket"
        aws s3api delete-bucket --bucket "$bucket" 2>/dev/null || echo "  (bucket may have been deleted by CDK)"
    else
        echo "Bucket $bucket does not exist — skipping"
    fi
}

if [ -n "$PROJECT_BUCKET" ]; then
    empty_and_delete_bucket "$PROJECT_BUCKET"
fi

if [ -n "$DEST_BUCKET" ]; then
    empty_and_delete_bucket "$DEST_BUCKET"
fi

echo ""

# ==========================================
# 3. DESTROY CDK STACK
# ==========================================
echo "--- Destroying PDFAccessibility CDK stack ---"

set +e
cdk destroy PDFAccessibility --force 2>&1
CDK_EXIT=$?
set -e

if [ $CDK_EXIT -eq 0 ]; then
    echo "✓ CDK stack destroyed"
else
    echo "CDK destroy returned exit code $CDK_EXIT (stack may not exist or partial cleanup needed)"
fi

echo ""

# ==========================================
# 4. DELETE SECRETS MANAGER SECRETS
# ==========================================
echo "--- Deleting Secrets Manager secrets ---"

aws secretsmanager delete-secret \
    --secret-id /myapp/client_credentials \
    --force-delete-without-recovery 2>/dev/null && echo "✓ Deleted /myapp/client_credentials" || echo "  Secret not found — skipping"

echo ""

# ==========================================
# 5. DELETE SSM PARAMETERS
# ==========================================
echo "--- Deleting SSM parameters (/pdf-processing/*) ---"

SSM_PARAMS=$(aws ssm get-parameters-by-path \
    --path "/pdf-processing/" \
    --recursive \
    --query "Parameters[].Name" \
    --output text 2>/dev/null || echo "")

if [ -n "$SSM_PARAMS" ]; then
    for param in $SSM_PARAMS; do
        aws ssm delete-parameter --name "$param" 2>/dev/null && echo "  ✓ Deleted $param" || true
    done
else
    echo "  No SSM parameters found"
fi

echo ""

# ==========================================
# 6. DELETE CLOUDWATCH LOG GROUPS
# ==========================================
echo "--- Deleting CloudWatch log groups ---"

# Log groups created by this application
LOG_GROUP_PREFIXES=(
    "/aws/lambda/PDFAccessibility-"
    "/ecs/pdf-remediation/"
    "/custom/pdf-remediation/"
    "/pdf-processing/"
)

for prefix in "${LOG_GROUP_PREFIXES[@]}"; do
    LOG_GROUPS=$(aws logs describe-log-groups \
        --log-group-name-prefix "$prefix" \
        --query "logGroups[].logGroupName" \
        --output text 2>/dev/null || echo "")
    
    if [ -n "$LOG_GROUPS" ]; then
        for lg in $LOG_GROUPS; do
            aws logs delete-log-group --log-group-name "$lg" 2>/dev/null && echo "  ✓ Deleted $lg" || true
        done
    fi
done

echo ""

# ==========================================
# 7. DELETE ECR REPOSITORIES
# ==========================================
echo "--- Deleting ECR repositories ---"

ECR_REPOS=$(aws ecr describe-repositories \
    --query "repositories[?contains(repositoryName, 'pdfaccessibility')].repositoryName" \
    --output text 2>/dev/null || echo "")

if [ -n "$ECR_REPOS" ]; then
    for repo in $ECR_REPOS; do
        echo "  Deleting ECR repository: $repo"
        aws ecr delete-repository --repository-name "$repo" --force 2>/dev/null || true
    done
else
    echo "  No matching ECR repositories found"
fi

# Also check for cdk-managed asset repos
CDK_ECR_REPOS=$(aws ecr describe-repositories \
    --query "repositories[?starts_with(repositoryName, 'cdk-')].repositoryName" \
    --output text 2>/dev/null || echo "")

if [ -n "$CDK_ECR_REPOS" ]; then
    for repo in $CDK_ECR_REPOS; do
        echo "  Deleting CDK ECR repository: $repo"
        aws ecr delete-repository --repository-name "$repo" --force 2>/dev/null || true
    done
fi

echo ""

# ==========================================
# 8. CDK BOOTSTRAP STACK (OPTIONAL)
# ==========================================
echo "--- CDK Bootstrap stack ---"
read -p "Delete the CDKToolkit bootstrap stack? (only if no other CDK apps use this account) [y/N] " del_bootstrap
del_bootstrap=${del_bootstrap:-N}

if [[ "$del_bootstrap" =~ ^[Yy]$ ]]; then
    # Empty the CDK staging bucket first
    CDK_BUCKET=$(aws cloudformation describe-stack-resources \
        --stack-name CDKToolkit \
        --query "StackResources[?ResourceType=='AWS::S3::Bucket'].PhysicalResourceId" \
        --output text 2>/dev/null || echo "")
    
    if [ -n "$CDK_BUCKET" ]; then
        empty_and_delete_bucket "$CDK_BUCKET"
    fi

    # Delete the CDK ECR repository if it exists
    aws ecr delete-repository --repository-name cdk-hnb659fds-container-assets-$(aws sts get-caller-identity --query Account --output text)-${AWS_REGION:-us-east-1} --force 2>/dev/null || true

    aws cloudformation delete-stack --stack-name CDKToolkit 2>/dev/null && echo "✓ CDKToolkit stack deletion initiated" || echo "  CDKToolkit stack not found"
    echo "  Waiting for stack deletion..."
    aws cloudformation wait stack-delete-complete --stack-name CDKToolkit 2>/dev/null || true
    echo "✓ CDKToolkit stack deleted"
else
    echo "  Skipping CDK bootstrap cleanup"
fi

echo ""
echo "========================================"
echo "  UNINSTALL COMPLETE"
echo "========================================"
echo ""
echo "All application resources have been removed."
echo "Your ~/set-*.sh script still exists — delete it manually if no longer needed."
echo ""
