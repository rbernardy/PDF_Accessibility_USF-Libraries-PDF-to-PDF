#!/bin/bash
#
# Set the email-enabled SSM parameter for PDF failure digest notifications
#
# Usage:
#   ./bin/set-email-enabled-ssm-parameter.sh         # Sets to default (false)
#   ./bin/set-email-enabled-ssm-parameter.sh true    # Enables email digests
#   ./bin/set-email-enabled-ssm-parameter.sh false   # Disables email digests
#

VALUE="${1:-false}"

echo "Setting /pdf-processing/email-enabled to: $VALUE"

aws ssm put-parameter \
    --name "/pdf-processing/email-enabled" \
    --value "$VALUE" \
    --type String \
    --overwrite

if [ $? -eq 0 ]; then
    echo "✓ email-enabled set successfully"
else
    echo "✗ Failed to set email-enabled"
    exit 1
fi
