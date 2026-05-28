#!/bin/bash
# Set the sender email SSM parameter for PDF failure digest notifications

EMAIL="${1:-$AWS_DEFAULT_EMAIL_ADDRESS}"

if [ -z "$EMAIL" ]; then
    echo "ERROR: No email provided and AWS_DEFAULT_EMAIL_ADDRESS is not set"
    exit 1
fi

echo "Setting /pdf-processing/sender-email to: $EMAIL"

aws ssm put-parameter \
    --name "/pdf-processing/sender-email" \
    --value "$EMAIL" \
    --type String \
    --overwrite

if [ $? -eq 0 ]; then
    echo "✓ Sender email set successfully"
else
    echo "✗ Failed to set sender email"
    exit 1
fi
