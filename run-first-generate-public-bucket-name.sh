#!/bin/bash

if [[ -z "$1" ]]; then
    echo "Usage: $0 <path to your set-[AWS_PROFILE_NAME].sh file>"
    exit 1
fi

INPUT_FILE="$1"

if [[ ! -f "$INPUT_FILE" ]]; then
    echo "Error: File not found: $INPUT_FILE" >&2
    exit 1
fi

RANDOM_STRING=$(tr -dc 'a-z0-9' </dev/urandom | head -c 15)
destination_bucket_prefix="pdfaccessibility-public"
new_bucket_name="${destination_bucket_prefix}-${RANDOM_STRING}"

echo "$new_bucket_name" > ~/destination_bucket.txt

sed -i "s|^export AWS_DESTINATION_BUCKET_NAME=.*|export AWS_DESTINATION_BUCKET_NAME=\"${new_bucket_name}\"|" "$INPUT_FILE"
