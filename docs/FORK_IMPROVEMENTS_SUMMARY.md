# PDF Accessibility Solution - Fork Improvements Summary

This document summarizes all the enhancements, features, and bug fixes implemented in this fork of the PDF Accessibility Solution.

## Overview

This fork significantly extends the original PDF Accessibility Solution with enterprise-grade features for high-volume PDF processing, including rate limiting, failure handling, monitoring dashboards, and automated retry mechanisms.

---

## 1. Folder Structure Preservation

### Problem Solved
The original solution lost folder organization when processing PDFs. Files uploaded to `pdf/batch1/subfolder/doc.pdf` would output to `result/COMPLIANT_doc.pdf`, making it difficult to organize large batches.

### Solution
- Full folder path is now extracted and preserved throughout the entire pipeline
- Input: `pdf/2024/january/report.pdf` → Output: `result/2024/january/COMPLIANT_report.pdf`
- Works with any folder depth
- Backward compatible with root-level files

### Files Modified
- `lambda/pdf-splitter-lambda/main.py` - Extract and pass folder path
- `lambda/pre-remediation-accessibility-checker/main.py` - Use folder path in reports
- `lambda/post-remediation-accessibility-checker/main.py` - Preserve folder in final reports
- `adobe-autotag-container/adobe_autotag_processor.py` - Handle folder paths in ECS
- `alt-text-generator-container/alt_text_generator.js` - JavaScript container folder support
- `lambda/pdf-merger-lambda/.../App.java` - Java merger preserves paths
- `cdk/cdk_stack.py` - Pass folder path through Step Functions

---

## 2. Adobe API Rate Limiting System

### Problem Solved
Adobe PDF Services API has a 200 requests/minute limit. Without rate limiting, concurrent ECS tasks would overwhelm the API causing 429 errors and failed processing.

### Solution: Dual-Protection Rate Limiter
1. **In-Flight Tracking**: Limits concurrent API requests (default: 150)
2. **RPM Limiting**: Limits requests per minute (default: 190)

### Key Components
- **DynamoDB Table**: `adobe-api-in-flight-tracker` stores counters
- **Rate Limiter Module**: `adobe-autotag-container/rate_limiter.py`
- **SSM Parameters**: Configurable limits without redeployment
  - `/pdf-processing/adobe-api-max-in-flight` (default: 150)
  - `/pdf-processing/adobe-api-rpm` (default: 190)

### Features
- Atomic counter operations with DynamoDB
- Exponential backoff when at capacity
- RPM window counters with auto-expiry (TTL)
- Real-time CloudWatch metrics

---

## 3. Queue-Based Processing System

### Problem Solved
Uploading thousands of PDFs at once would overwhelm AWS infrastructure (ECS throttling, subnet exhaustion, Step Function limits).

### Solution: Controlled Intake Queue
- Teams upload to `queue/` folder instead of `pdf/`
- Scheduled Lambda moves files to `pdf/` at controlled rate
- Prevents infrastructure overload while maintaining throughput

### Components
- **Queue Processor Lambda**: `lambda/pdf-retry-processor/main.py`
- Runs every 2 minutes via EventBridge
- Checks capacity before moving files
- FIFO ordering (oldest files first)

### Configuration (SSM Parameters)
- `/pdf-processing/queue-enabled` - ON/OFF switch
- `/pdf-processing/queue-max-in-flight` - Max in-flight before pausing (default: 10)
- `/pdf-processing/queue-max-executions` - Max Step Functions before pausing (default: 50)
- `/pdf-processing/queue-batch-size` - Files per batch (default: 5)

---

## 4. Automatic Retry System

### Problem Solved
Transient failures (rate limits, timeouts) would permanently fail PDFs, requiring manual re-upload.

### Solution: Automatic Retry with Backoff
- Failed PDFs move to `queue/` folder for automatic retry
- Retry count tracked in S3 object metadata
- After MAX_RETRIES (default: 3), PDFs move to `failed/` folder
- Original PDFs are NEVER deleted

### Flow
1. PDF fails processing → Cleanup Lambda detects failure
2. If retry_count < MAX_RETRIES → Move to `queue/` (increment count)
3. If retry_count >= MAX_RETRIES → Move to `failed/` folder
4. Queue processor picks up retries when capacity available

### Configuration
- `/pdf-processing/max-retries` - Retry attempts before giving up (default: 3)

---

## 5. In-Flight Counter Reconciliation

### Problem Solved
If an ECS task crashes without releasing its rate limit slot, the counter gets "stuck", blocking all new processing.

### Solution: Automatic Reconciler
- Scheduled Lambda runs every 5 minutes
- Compares counter against actual running tasks
- Resets counter if clearly stale
- Cleans up orphaned file tracking entries

### Components
- **Reconciler Lambda**: `lambda/in-flight-reconciler/main.py`
- **ECS Task Failure Tracker**: `lambda/ecs-task-failure-tracker/main.py`
  - Triggered by EventBridge on ECS task failures
  - Updates tracking entries with crash details
  - Decrements counter for crashed tasks

### Configuration
- `/pdf-processing/reconciler-enabled` - Enable/disable (default: true)
- `/pdf-processing/reconciler-max-drift` - Max counter drift before reset (default: 5)

---

## 6. PDF Failure Analysis

### Problem Solved
When PDFs fail Adobe API processing, there was no insight into why they failed.

### Solution: Automated PDF Analysis
- Lambda analyzes failed PDFs using PyMuPDF
- Identifies likely failure causes (large images, too many pages, encryption, etc.)
- Generates detailed reports

### Analysis Checks
- Page count (>500 pages flagged)
- File size (>100 MB flagged)
- Image count and dimensions (>4000px flagged)
- Font count and embedding status
- Encryption detection
- PDF version compatibility
- Annotation count

### Output
- Structured CloudWatch logs
- Word document reports saved to S3
- Integration with failure digest emails

### Files
- `lambda/pdf-failure-analysis/main.py`
- `lambda/pdf-failure-analysis/analyzer.py`

---

## 7. Success Tracking & Throughput Metrics

### Problem Solved
No visibility into processing throughput or progress toward remediation goals.

### Solution: Comprehensive Success Tracking
- **Success Tracker Lambda**: Records completions in DynamoDB
- **Success Rate Widget**: CloudWatch dashboard widget

### Metrics Tracked
- Total PDFs processed (all time)
- PDFs processed today
- Hourly averages (24-hour rolling)
- Current hour count
- Queue depth
- Failed file count

### Dashboard Features
- Remediation goal progress
- Estimated days to completion
- Days to deadline countdown
- Real-time queue status

### Files
- `lambda/pdf-success-tracker/main.py`
- `lambda/success-rate-widget/main.py`

---

## 8. CloudWatch Dashboard Widgets

### Custom Widgets Added
1. **Success Rate Widget** - Throughput metrics and goal tracking
2. **Rate Limit Widget** - Current in-flight and RPM utilization
3. **In-Flight Files Widget** - Files currently being processed
4. **Failure Analysis Widget** - Recent failure summaries

### Files
- `lambda/rate-limit-widget/main.py`
- `lambda/in-flight-files-widget/main.py`
- `lambda/success-rate-widget/main.py`

---

## 9. Failure Digest Emails

### Feature
Daily email digest of PDF processing failures sent to relevant team members.

### Components
- **Failure Digest Lambda**: `lambda/pdf-failure-digest/main.py`
- Runs daily at 11:55 PM via EventBridge
- Groups failures by user/collection
- Includes failure reasons and analysis summaries

---

## 10. S3 PDF Copier (Auto-Sync)

### Feature
Automatically copies successfully processed PDFs to a destination bucket.

### Use Case
Sync remediated PDFs to a separate bucket for distribution or archival.

### Components
- `lambda/s3-pdf-copier/main.py`
- Triggered by S3 events on `/result` prefix
- Preserves folder structure in destination

---

## 11. Pipeline Status Checker

### Feature
Lambda that checks the overall health of the processing pipeline.

### Checks Performed
- Step Function execution status
- ECS task health
- Queue depths
- Rate limiter status

### Files
- `lambda/pipeline-status-checker/main.py`

---

## 12. Failure Report Generation

### Feature
Generate Excel reports of PDF failures filtered by collection, date range, error type.

### Components
- `lambda/failure-analysis-report/main.py`
- Queries DynamoDB failure records
- Generates downloadable Excel files

### Planned Enhancement
- Web UI for on-demand report generation (spec in `.kiro/specs/failure-report-web-ui/`)

---

## 13. Bug Fixes

### Step Functions Data Flow
- Fixed `result_path` on ECS tasks to preserve input data
- Fixed environment variable passing between tasks
- Fixed Java Lambda output wrapping for Step Functions compatibility

### JavaScript Container Scoping
- Fixed variable scoping in `alt-text.js` where `fileDirectory` and `fileKey` were undefined in `modifyPDF` function

### Folder Path Extraction
- Fixed `add_title` Lambda to preserve full folder path (was removing too many components)
- Fixed `a11y_postcheck` Lambda to extract and use folder path from save_path

---

## 14. Helper Scripts

### Operational Scripts Added
| Script | Purpose |
|--------|---------|
| `bin/set-max-retries.sh` | Configure max retry count |
| `bin/check-queue-status.sh` | Check queue/retry folder status |
| `bin/check-in-flight-status.sh` | Diagnose in-flight counter issues |
| `bin/reset-AIFRRT-in-flight-value-to-zero.sh` | Manual counter reset |
| `bin/clear-rate-limit-table.sh` | Clear tracking table |

---

## 15. Documentation

### New Documentation Files
- `docs/ADOBE_API_RATE_LIMITING.md` - Rate limiting implementation details
- `docs/ADOBE_API_QUEUE_ARCHITECTURE.md` - Queue architecture design
- `docs/PDF_DELETION_CLEANUP_FEATURE.md` - Failure cleanup and retry system
- `docs/PDF_FAILURE_ANALYSIS_FEATURE.md` - Failure analysis feature
- `docs/IN_FLIGHT_COUNTER_MONITORING.md` - Counter monitoring and recovery
- `docs/FIX_APPLIED_SUMMARY.md` - Detailed changelog of all fixes

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           PDF Processing Pipeline                               │
│                                                                                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│  │  queue/  │───▶│   pdf/   │───▶│  temp/   │───▶│ result/  │───▶│  Sync    │  │
│  │ (upload) │    │(process) │    │ (chunks) │    │(complete)│    │ (copy)   │  │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘  │
│       │              │                                                          │
│       │              │ failure                                                  │
│       │              ▼                                                          │
│       │         ┌──────────┐    ┌──────────┐                                   │
│       │         │ Cleanup  │───▶│ failed/  │ (after max retries)               │
│       │         │ Lambda   │    └──────────┘                                   │
│       │         └──────────┘                                                   │
│       │              │                                                          │
│       │              │ retry                                                    │
│       │              ▼                                                          │
│       └─────────────────────────────────────────────────────────────────────────│
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        Rate Limiting Layer                              │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │   │
│  │  │ In-Flight   │  │    RPM      │  │  Reconciler │  │   Queue     │    │   │
│  │  │  Counter    │  │   Counter   │  │   Lambda    │  │  Processor  │    │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        Monitoring Layer                                 │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │   │
│  │  │  Success    │  │  Failure    │  │  Dashboard  │  │   Digest    │    │   │
│  │  │  Tracker    │  │  Analysis   │  │   Widgets   │  │   Emails    │    │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Summary Statistics

| Category | Count |
|----------|-------|
| New Lambda Functions | 12 |
| Modified Lambda Functions | 6 |
| New Documentation Files | 6 |
| Helper Scripts | 5+ |
| SSM Parameters | 15+ |
| CloudWatch Widgets | 4 |

---

## Version History

| Version | Date | Key Changes |
|---------|------|-------------|
| Fork | Jan-Mar 2026 | Rate limiting, queue system, failure handling, monitoring |

---

*Last Updated: April 2026*
