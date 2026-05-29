#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

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

echo ""

# ==========================================
# CONFIGURATION - Update these variables
# ==========================================
BUCKET_NAME="your-unique-bucket-name"
REGION="us-east-1"
INDEX_DOCUMENT="index.html"
ERROR_DOCUMENT="error.html"

# Array of allowed IP addresses or CIDR blocks
# Example: ("192.168.1.50/32" "10.0.0.0/16" "203.0.113.4")
ALLOWED_IPS=("203.0.113.4/32" "198.51.100.0/22")

# ==========================================
# VALIDATION
# ==========================================
if [[ "$BUCKET_NAME" == "your-unique-bucket-name" ]]; then
    echo "Error: Please update the BUCKET_NAME variable in the script."
    exit 1
fi

echo "Starting configuration for bucket: $BUCKET_NAME in region: $REGION"

# 1. Enable Static Website Hosting
echo "Enabling static website hosting..."
aws s3api put-bucket-website \
    --bucket "$BUCKET_NAME" \
    --website-configuration "{\"IndexDocument\":{\"Suffix\":\"$INDEX_DOCUMENT\"},\"ErrorDocument\":{\"Key\":\"$ERROR_DOCUMENT\"}}" \
    --region "$REGION"

# 2. Disable Public Access Block 
# (Required so AWS allows you to attach a policy granting 'public' access)
echo "Disabling Public Access Block..."
aws s3api put-public-access-block \
    --bucket "$BUCKET_NAME" \
    --public-access-block-configuration "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false" \
    --region "$REGION"

# 3. Generate the IP-restricted Bucket Policy
echo "Generating IP-restricted bucket policy..."

# Format the IP array into a JSON array string
IP_JSON_ARRAY=$(printf '%s\n' "${ALLOWED_IPS[@]}" | jq -R . | jq -s -c .)

POLICY=$(cat <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PublicReadGetObjectWithIPRestriction",
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": "arn:aws:s3:::$BUCKET_NAME/*",
            "Condition": {
                "IpAddress": {
                    "aws:SourceIp": $IP_JSON_ARRAY
                }
            }
        }
    ]
}
EOF
)

# 4. Apply the Bucket Policy
echo "Applying bucket policy..."
aws s3api put-bucket-policy \
    --bucket "$BUCKET_NAME" \
    --policy "$POLICY" \
    --region "$REGION"

echo "--------------------------------------------------------"
echo "Success! Your S3 website is now configured."
echo "Bucket Website URL: http://$BUCKET_NAME.s3-website-$REGION.amazonaws.com"
echo "Access is restricted to the specified IPs."
echo "--------------------------------------------------------"
