<p align="center">
  <img src="images/pdf-accessibility-usf-libraries-pdf-to-pdf-compact.png" alt="USF Libraries PDF Accessibility PDF-to-PDF">
</p>

# USF Libraries Fork — Installation Instructions

> **Note:** This documentation covers the USF Libraries extended fork of the [ASUCICREPO/PDF_Accessibility](https://github.com/ASUCICREPO/PDF_Accessibility) software. For the original upstream deployment, see the main README.

## What You'll End Up With

A fully automated PDF accessibility remediation pipeline deployed to AWS that:
- Accepts PDFs via an S3 queue folder
- Two-level queue feature with failure retries, including file splitting based on page size and file size
- Processes them through Adobe's Accessibility Auto-Tag API
- Generates alt text for images using Amazon Bedrock (Nova Pro)
- Merges remediated chunks back into compliant PDFs
- Provides a CloudWatch dashboard for monitoring throughput, success rates, and rate limiting
- Additional processing reports & spreadsheets
- Supports runtime tuning via 17 SSM parameters (no redeployment needed)

## Architecture Overview

See: [PDF Accessibility – USF Libraries Implementation](USF-Libraries-PDF-to-PDF-remediation.md)

![Architecture Diagram](images/pdf-processing-pipeline.png)

## Time Estimate

- Initial setup and deployment: ~30–45 minutes
- Subsequent redeployments: ~10–15 minutes

## Cost Estimate

Primary AWS services used (approximate monthly costs vary by volume):
- **ECS Fargate** — container tasks for Adobe API and alt-text generation
- **Lambda** — PDF splitting, merging, queue processing, monitoring
- **S3** — PDF storage (input, temp, result, queue, failed)
- **Step Functions** — workflow orchestration
- **DynamoDB** — rate limiting and failure tracking
- **CloudWatch** — dashboard, logs, metrics
- **Secrets Manager** — Adobe API credentials
- **SSM Parameter Store** — runtime configuration

For low-volume usage (~100 PDFs/month), expect roughly $20–50/month. High-volume batch processing (10,000+ PDFs) will scale primarily with ECS Fargate and Adobe API costs.

## Prerequisites

### Required Software (on your deployment machine)

| Software | Purpose | Install |
|----------|---------|---------|
| Linux (Fedora recommended) | Deployment environment | — |
| AWS CLI v2 | AWS API access | [Install guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| Node.js 18+ | CDK CLI dependency | `dnf install nodejs` or [nvm](https://github.com/nvm-sh/nvm) |
| AWS CDK CLI | Infrastructure deployment | `npm install -g aws-cdk` |
| Docker | Container image builds | [Install guide](https://docs.docker.com/engine/install/) |
| Python 3.12+ | CDK app runtime | `dnf install python3` |
| jq | JSON processing in scripts | `dnf install jq` |
| pip | Python package manager | Included with Python |

### Required AWS Access

- A dedicated AWS account (new/empty strongly recommended)
- An IAM user with `AdministratorAccess` policy attached
- An access key configured as an AWS CLI profile
- Adobe PDF Services API credentials (client ID and client secret) from the [Adobe Developer Console](https://developer.adobe.com/console)

## Installation Steps

### 1. Configure Your AWS CLI Profile

Create a named profile for this deployment:

```bash
aws configure --profile MY_PROFILE_NAME
```

Set the following values:
- Access Key ID
- Secret Access Key
- Default region: `us-east-1` (or your preferred region)
- Output format: `json` (required by deployment scripts)

### 2. Create Your Environment Script

Copy the template to a location outside the repository:

```bash
cp set-template.sh ~/set-myprofile.sh
```

Suggested naming convention: `set-[institution][first 3 digits of account ID].sh` (e.g., `set-usf427.sh`)

Edit the copy and fill in all required values:
- `AWS_ACCOUNT_ID`
- `AWS_PROFILE`
- `AWS_REGION` / `AWS_DEFAULT_REGION`
- `AWS_PDF_SERVICES_CLIENT_ID` (from Adobe Developer Console)
- `AWS_PDF_SERVICES_CLIENT_SECRET` (from Adobe Developer Console)
- `AWS_DEFAULT_EMAIL_ADDRESS` (for failure digest notifications)
- `AWS_TARGET_TOTAL` (your remediation goal count)

> **Important:** Keep this file outside the repo and never commit it. It contains your credentials.

### 3. Source Your Environment Script

```bash
source ~/set-myprofile.sh
```

This sets all required environment variables and navigates to the repository root. You must source this script in every new terminal session before running deployment or operational scripts.

### 4. Generate the Public Bucket Name

```bash
./run-first-generate-public-bucket-name.sh ~/set-myprofile.sh
```

This generates a random `AWS_DESTINATION_BUCKET_NAME` value for the public-facing S3 bucket (where copies of remediated PDFs are served). The script automatically updates your set script and saves the value to `~/destination_name.txt`. Run this once during initial setup.

### 5. Run Initial Deployment

```bash
./usfl-custom-initial-deployment.sh ~/set-myprofile.sh
```

This script performs the one-time setup:
1. Validates all required environment variables
2. Confirms your AWS identity (with a prompt to abort if incorrect)
3. Bootstraps CDK for your account/region
4. Creates Adobe API credentials in Secrets Manager
5. Invokes `./usfl-custom-cdk-deploy-redeploy.sh` to deploy the full stack

During deployment, the script will:
- Discover and write the `AWS_PROJECT_S3_BUCKET_NAME` back to your set script
- Create the default S3 folder structure (`pdf/`, `temp/`, `result/`, `queue/`, `reports/`, `failed/`, `logs-public-access/`, and report subfolders)
- Run `./bin/set-all-ssm-parameters.sh --defaults` to configure all operational parameters

### 6. Configure the Public S3 Bucket

Edit `./bin/configure-public-S3-bucket.sh` and update:
- `BUCKET_NAME` — set to your `AWS_DESTINATION_BUCKET_NAME` value
- `ALLOWED_IPS` — set to the IP addresses/CIDR blocks that should have access

Then run:

```bash
./bin/configure-public-S3-bucket.sh
```

This configures static website hosting and an IP-restricted bucket policy on your public-facing bucket.

## Post-Deployment Configuration

### SSM Parameters

The deployment automatically sets all 17 SSM parameters to their default values. To view current values:

```bash
./bin/view-current-ssm-parameters.sh
```

To reconfigure interactively:

```bash
./bin/set-all-ssm-parameters.sh
```

Individual parameters can be changed with their respective `./bin/set-*.sh` scripts.

### Email Notifications (Optional)

To enable daily failure digest emails:

1. Verify your sender email address in AWS SES
2. Set the sender email: `./bin/set-sender-email.sh your-email@domain.com`
3. Enable the feature: `./bin/set-email-enabled-ssm-parameter.sh true`

### Update Fork Version

```bash
./bin/set-fork-version.sh V3-20260529
```

This value is displayed in the CloudWatch dashboard widget title.

### Update Remediation Deadline

```bash
./bin/set-remediation-deadline.sh 2027-04-26
```

## Operating the System

### Starting PDF Processing

1. Upload PDF files to the `queue/` folder in the project S3 bucket
2. Resume the queue processor:

```bash
./bin/queue-resume.sh
```

### Pausing Processing

```bash
./bin/queue-pause.sh
```

> **Note:** Pausing only stops new files from being moved from `queue/` to `pdf/`. Files already in `pdf/` will continue processing.

### Monitoring

- **CloudWatch Dashboard** — created automatically, shows throughput, success rates, rate limiting, and in-flight counts
- **Step Functions Console** — view individual workflow executions
- **ECS Console** — monitor container task status

### Redeployment

For subsequent code updates, use:

```bash
source ~/set-myprofile.sh
./usfl-custom-cdk-deploy-redeploy.sh ~/set-myprofile.sh
```

Redeployment does not modify or remove the project S3 bucket or its existing contents.

## Troubleshooting

- [Troubleshooting CDK Deploy](TROUBLESHOOTING_CDK_DEPLOY.md)
- [Adobe API Rate Limiting](ADOBE_API_RATE_LIMITING.md)
- [In-Flight Counter Monitoring](IN_FLIGHT_COUNTER_MONITORING.md)

### Common Issues

| Issue | Solution |
|-------|----------|
| CDK bootstrap fails | Verify IAM user has AdministratorAccess; check region matches |
| "Could not discover project S3 bucket" | Script retries 3x with 30s delays; if still fails, check CloudFormation console for the bucket name |
| CloudWatch widgets show "log group not found" | Normal on fresh deploy — log groups are created on first Lambda invocation |
| Adobe API 429 errors | Reduce `adobe-api-rpm` and `adobe-api-rps` SSM parameters |
| Queue not processing | Verify `queue-enabled` SSM parameter is `true`; check `queue-max-in-flight` isn't exceeded |

## File Reference

| Script | Purpose |
|--------|---------|
| `set-template.sh` | Template for environment variables (copy and customize) |
| `run-first-generate-public-bucket-name.sh` | One-time: generates destination bucket name |
| `usfl-custom-initial-deployment.sh` | One-time: bootstraps and deploys to new account |
| `usfl-custom-cdk-deploy-redeploy.sh` | Deploys/redeploys the CDK stack |
| `bin/configure-public-S3-bucket.sh` | Configures public bucket access |
| `bin/set-all-ssm-parameters.sh` | Sets all operational SSM parameters |
| `bin/view-current-ssm-parameters.sh` | Displays current SSM parameter values |
| `bin/queue-resume.sh` | Enables queue processing |
| `bin/queue-pause.sh` | Pauses queue processing |
| `bin/set-sender-email.sh` | Sets digest notification sender email |
| `bin/set-email-enabled-ssm-parameter.sh` | Enables/disables email notifications |
| `usfl-custom-uninstall.sh` | Destroys all resources in the account that were setup for this software |
