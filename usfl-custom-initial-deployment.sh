#!/bin/bash
set -e

# Check argument provided
if [[ -z "$1" ]]; then
    echo "Usage: $0 <path to your set-[AWS_PROFILE_NAME].sh script file>"
    exit 1
fi

# Check that the specified file exists
if [[ ! -f "$1" ]]; then
    echo "ERROR: File '$1' does not exist."
    exit 1
fi

# AWS_DESTINATION_BUCKET_NAME should already be set from source script, after executing the run-first-generate-public-bucket-name.sh script
if [ -z "$AWS_DESTINATION_BUCKET_NAME" ]; then
    echo "ERROR: AWS_DESTINATION_BUCKET_NAME is not set"
    exit 1
else
    echo "AWS_DESTINATION_BUCKET_NAME=[$AWS_DESTINATION_BUCKET_NAME]."
fi

echo "Confirming current aws cli profile in use"
aws sts get-caller-identity
read -r -p "Press any key to continue"

# Validate required env vars
if [ -z "$AWS_ACCOUNT_ID" ] || [ -z "$AWS_REGION" ]; then
    echo "ERROR: AWS_ACCOUNT_ID and AWS_REGION must be set (from your source script {set-[AWS_PROFILE_NAME]})"
    exit 1
else
    echo "AWS_ACCOUNT_ID=[$AWS_ACCOUNT_ID]."
    echo "AWS_REGION=[$AWS_REGION]."
fi

if [ -z "$PDF_SERVICES_CLIENT_ID" ] || [ -z "$PDF_SERVICES_CLIENT_SECRET" ]; then
    echo "ERROR: PDF_SERVICES_CLIENT_ID and PDF_SERVICES_CLIENT_SECRET must be set"
    exit 1
else
    echo "PDF_SERVICES_CLIENT_ID is set (${#PDF_SERVICES_CLIENT_ID} chars)"
    echo "PDF_SERVICES_CLIENT_SECRET is set (${#PDF_SERVICES_CLIENT_SECRET} chars)"
fi

# 1. Bootstrap CDK (one-time per account/region)
cdk bootstrap aws://${AWS_ACCOUNT_ID}/${AWS_REGION}

# 2. Set up Adobe credentials in Secrets Manager
SECRET_JSON=$(jq -n \
    --arg cid "$PDF_SERVICES_CLIENT_ID" \
    --arg csec "$PDF_SERVICES_CLIENT_SECRET" \
    '{"client_credentials":{"PDF_SERVICES_CLIENT_ID":$cid,"PDF_SERVICES_CLIENT_SECRET":$csec}}')

echo "SECRET_JSON constructed successfully (${#SECRET_JSON} chars)"

if aws secretsmanager create-secret --name /myapp/client_credentials \
    --secret-string "$SECRET_JSON" 2>/dev/null; then
    echo "Secret created successfully"
else
    aws secretsmanager update-secret --secret-id /myapp/client_credentials \
        --secret-string "$SECRET_JSON" || { echo "ERROR: Failed to create/update secret"; exit 1; }
    echo "Secret updated successfully"
fi

# 3. Set deployment type
export AWS_DEPLOYMENT_TYPE="pdf2pdf"
echo "AWS_DEPLOYMENT_TYPE=[$AWS_DEPLOYMENT_TYPE]."

echo "Destination bucket (public-facing remediated PDFs): ${AWS_DESTINATION_BUCKET_NAME}"
read -r -p "Press any key to continue"

echo "Will now run ./usfl-custom-cdk-deploy-redeploy.sh $1"
echo "For subsequent redeployments, just run ./usfl-custom-cdk-deploy-redeploy.sh $1 directly."
read -r -p "Press any key to continue"

# 4. Run the CDK deploy script
./usfl-custom-cdk-deploy-redeploy.sh "$1"
