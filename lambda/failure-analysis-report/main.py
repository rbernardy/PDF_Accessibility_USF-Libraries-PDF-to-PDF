"""
Failure Analysis Report Generator Lambda

Generates an Excel spreadsheet from PDF failure analysis data stored in DynamoDB.
Scheduled to run hourly via EventBridge.

Timing data (started_at, failed_at) is now stored permanently in the failure analysis
table at the moment of failure, so it doesn't depend on the in-flight tracker TTL.
Falls back to in-flight tracker for older entries that don't have timing data.
"""

import json
import os
import logging
import re
import boto3
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')

ANALYSIS_TABLE = os.environ.get('ANALYSIS_TABLE', '')
REPORT_BUCKET = os.environ.get('REPORT_BUCKET', '')
RATE_LIMIT_TABLE = os.environ.get('RATE_LIMIT_TABLE', 'adobe-api-in-flight-tracker')
PRESCAN_TABLE = os.environ.get('PRESCAN_TABLE', 'pdf-prescan-data')

# Prefix for individual file tracking entries (must match rate_limiter.py)
IN_FLIGHT_FILE_PREFIX = "file_"


def convert_dynamodb_value(value):
    """
    Recursively convert DynamoDB values (Decimals, nested dicts/lists) to Python native types.
    """
    if isinstance(value, Decimal):
        # Convert to int if it's a whole number, otherwise float
        if value % 1 == 0:
            return int(value)
        return float(value)
    elif isinstance(value, dict):
        # Recursively convert dict values
        return {k: convert_dynamodb_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        # Recursively convert list items
        return [convert_dynamodb_value(item) for item in value]
    else:
        return value


def sanitize_for_excel(value):
    """
    Remove illegal characters that cannot be used in Excel worksheets.
    
    Excel (via openpyxl) doesn't allow control characters (0x00-0x1F except tab/newline/carriage return).
    This function strips those characters from strings.
    """
    if not isinstance(value, str):
        return value
    
    # Remove control characters except tab (0x09), newline (0x0A), carriage return (0x0D)
    # These are the only control chars allowed in XML (which Excel uses internally)
    import re
    # Pattern matches control chars 0x00-0x08, 0x0B-0x0C, 0x0E-0x1F
    illegal_pattern = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
    return illegal_pattern.sub('', value)


def format_for_excel(value):
    """
    Format a value for Excel cell output.
    Converts lists and dicts to readable strings.
    Sanitizes strings to remove illegal characters.
    """
    if isinstance(value, list):
        result = ', '.join(str(v) for v in value)
    elif isinstance(value, dict):
        result = json.dumps(value)
    else:
        result = value
    
    return sanitize_for_excel(result)


def extract_collection_folder(s3_key: str) -> str:
    """
    Extract the collection folder from an S3 key.
    
    The collection folder is the first folder after the top-level prefix
    (temp/, failed/, queue/, pdf/, etc.)
    
    Examples:
        temp/pdfs-test-failures-13-american_boy-S0-F19/11_american_boy_a/file.pdf 
            -> pdfs-test-failures-13-american_boy-S0-F19
        failed/pdfs-test-failures-13-american_boy-S0-F19/11_american_boy_a.pdf 
            -> pdfs-test-failures-13-american_boy-S0-F19
        queue/pdfs-test-failures-07-coe_college/60_coe.pdf 
            -> pdfs-test-failures-07-coe_college
    
    Args:
        s3_key: The S3 key of the PDF file
        
    Returns:
        The collection folder name, or empty string if not found
    """
    if not s3_key:
        return ''
    
    # Split by '/'
    parts = s3_key.split('/')
    
    # Need at least 2 parts: top-level-prefix/collection-folder/...
    if len(parts) >= 2:
        # Return the second part (index 1), which is the collection folder
        return parts[1]
    
    return ''


def load_all_prescan_data() -> dict:
    """
    Load all prescan data from DynamoDB into a dictionary keyed by pdf_key.
    
    Note: The prescan table has a sort key (scan_timestamp), so there may be
    multiple records per pdf_key. We keep the most recent one (last scanned).
    
    Returns:
        dict mapping pdf_key to prescan data dict
    """
    prescan_cache = {}
    
    if not PRESCAN_TABLE:
        logger.warning("PRESCAN_TABLE environment variable not set")
        return prescan_cache
    
    try:
        table = dynamodb.Table(PRESCAN_TABLE)
        logger.info(f"Loading prescan data from table: {PRESCAN_TABLE}")
        
        # Scan all items from prescan table
        try:
            response = table.scan()
        except Exception as scan_error:
            logger.error(f"Error during initial scan of {PRESCAN_TABLE}: {scan_error}", exc_info=True)
            return prescan_cache
            
        items = response.get('Items', [])
        logger.info(f"First scan returned {len(items)} items, has more: {'LastEvaluatedKey' in response}")
        
        page_count = 1
        while 'LastEvaluatedKey' in response:
            page_count += 1
            try:
                response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
                new_items = response.get('Items', [])
                items.extend(new_items)
                logger.info(f"Scan page {page_count} returned {len(new_items)} items, total so far: {len(items)}")
            except Exception as page_error:
                logger.error(f"Error during scan page {page_count}: {page_error}", exc_info=True)
                break
        
        logger.info(f"Loaded {len(items)} total prescan records from DynamoDB after {page_count} scan(s)")
        
        # Log a few sample keys for debugging
        if items:
            sample_keys = [item.get('pdf_key', 'NO_KEY') for item in items[:5]]
            logger.info(f"Sample prescan pdf_keys: {sample_keys}")
        else:
            logger.warning(f"No items found in prescan table {PRESCAN_TABLE}")
        
        # Build cache keyed by pdf_key
        # Since there's a sort key, multiple records may exist per pdf_key
        # We'll keep the most recent one (by scan_timestamp)
        for item in items:
            pdf_key = item.get('pdf_key', '')
            if pdf_key:
                scan_timestamp = item.get('scan_timestamp', '')
                
                # Check if we already have this key and if this record is newer
                existing = prescan_cache.get(pdf_key)
                if existing:
                    existing_timestamp = existing.get('scan_timestamp', '')
                    if scan_timestamp <= existing_timestamp:
                        continue  # Skip older record
                
                # Convert Decimal types to float/int for Excel compatibility
                # Also handle nested dicts/lists that may contain Decimals
                converted = {}
                for key, value in item.items():
                    converted[key] = convert_dynamodb_value(value)
                prescan_cache[pdf_key] = converted
            else:
                logger.warning(f"Prescan item missing pdf_key: {item}")
        
        logger.info(f"Built prescan cache with {len(prescan_cache)} unique pdf_keys from {len(items)} records")
        
        # Log all keys if there are few (for debugging)
        if len(prescan_cache) <= 20:
            logger.info(f"All prescan keys: {list(prescan_cache.keys())}")
                
    except Exception as e:
        logger.error(f"Error loading prescan data: {e}", exc_info=True)
    
    return prescan_cache


def extract_original_filename(chunk_filename: str) -> str:
    """
    Extract the original PDF filename from a processed chunk filename.
    
    During processing, files may have:
    1. A prefix added: COMPLIANT_, REMEDIATED_, etc.
    2. A chunk suffix: _chunk_N
    
    We need to strip both to get back to the original filename.
    
    Examples:
        COMPLIANT_19_american_boy_a_chunk_4.pdf -> 19_american_boy_a.pdf
        document_chunk_1.pdf -> document.pdf
        REMEDIATED_report_chunk_3.pdf -> report.pdf
        my_file.pdf -> my_file.pdf (no change)
    
    Args:
        chunk_filename: The processed filename
        
    Returns:
        The original filename, or the input if no patterns match
    """
    import re
    
    result = chunk_filename
    
    # Step 1: Remove known prefixes (COMPLIANT_, REMEDIATED_, etc.)
    prefixes_to_remove = ['COMPLIANT_', 'REMEDIATED_', 'PROCESSED_', 'TAGGED_']
    for prefix in prefixes_to_remove:
        if result.upper().startswith(prefix.upper()):
            result = result[len(prefix):]
            break
    
    # Step 2: Remove _chunk_N suffix (before .pdf)
    # Pattern: anything_chunk_N.pdf where N is one or more digits
    match = re.match(r'^(.+)_chunk_\d+\.pdf$', result, re.IGNORECASE)
    if match:
        original_basename = match.group(1)
        result = f"{original_basename}.pdf"
    
    return result


def get_prescan_data(filename: str, collection_folder: str, prescan_cache: dict, log_misses: bool = False) -> tuple:
    """
    Look up prescan data from the pre-loaded cache.
    
    The prescan data is stored for the ORIGINAL PDF file (before splitting into chunks).
    So we need to convert chunk filenames back to original filenames.
    
    Args:
        filename: The PDF filename (may be a chunk like 'document_chunk_1.pdf')
        collection_folder: The collection folder name
        prescan_cache: Pre-loaded prescan data dictionary
        log_misses: If True, log when prescan data is not found
        
    Returns:
        tuple of (dict with prescan data or empty dict, str with matching info for debugging)
    """
    matching_info = ""
    
    if not filename or not collection_folder:
        matching_info = f"SKIPPED: missing filename='{filename}' or collection_folder='{collection_folder}'"
        if log_misses:
            logger.debug(f"Prescan lookup skipped - missing filename ({filename}) or collection_folder ({collection_folder})")
        return {}, matching_info
    
    # Convert chunk filename to original filename
    # e.g., document_chunk_1.pdf -> document.pdf
    original_filename = extract_original_filename(filename)
    
    # Construct the pdf_key to look up (prescan stores with pdf/ prefix)
    pdf_key = f"pdf/{collection_folder}/{original_filename}"
    
    # Build matching info for debugging
    if original_filename != filename:
        matching_info = f"Chunk '{filename}' -> Original '{original_filename}' | Query: '{pdf_key}'"
    else:
        matching_info = f"Query: '{pdf_key}'"
    
    result = prescan_cache.get(pdf_key, {})
    
    if result:
        matching_info += f" | FOUND"
    else:
        matching_info += f" | NOT FOUND in {len(prescan_cache)} cached records"
        # Try to find similar keys for debugging
        similar_keys = [k for k in prescan_cache.keys() if original_filename in k][:3]
        if similar_keys:
            matching_info += f" | Similar keys: {similar_keys}"
    
    if log_misses and not result:
        logger.debug(f"Prescan data NOT FOUND for pdf_key: {pdf_key}")
    
    return result, matching_info


def parse_adobe_error(original_error: str) -> dict:
    """
    Parse Adobe API error message to extract structured fields.
    
    Example error formats:
    - "ECS Task Failed (adobe-autotag): ... Adobe Autotag API failed: description =An Internal Server Error has occurred.;;"
    - "description =An Internal Server Error has occurred.;; requestTrackingId=...; statusCode=500; errorCode=INTERNAL_SERVER_ERROR"
    - "Status: 500"
    
    Returns:
        dict with 'api_name', 'description', 'status_code', 'error_code', 'request_tracking_id' keys
    """
    result = {
        'api_name': '',
        'description': '',
        'status_code': '',
        'error_code': '',
        'request_tracking_id': ''
    }
    
    if not original_error:
        return result
    
    # Extract API name - try multiple patterns
    # Pattern 1: "Adobe Autotag API failed:" or "Adobe Extract API failed:"
    api_match = re.search(r'Adobe\s+(Autotag|Extract)\s+API\s+failed', original_error, re.IGNORECASE)
    if api_match:
        api_type = api_match.group(1).capitalize()
        result['api_name'] = f"Adobe {api_type} API"
    else:
        # Pattern 2: "ECS Task Failed (adobe-autotag)" or "ECS Task Failed (adobe-extract)"
        ecs_match = re.search(r'ECS\s+Task\s+Failed\s*\(\s*adobe[_-]?(autotag|extract)\s*\)', original_error, re.IGNORECASE)
        if ecs_match:
            api_type = ecs_match.group(1).capitalize()
            result['api_name'] = f"Adobe {api_type} API"
    
    # Extract description - everything between "description =" and ";"
    # This handles: description =Some error message here;
    desc_match = re.search(r"description\s*=\s*([^;]+)", original_error, re.IGNORECASE)
    if desc_match:
        result['description'] = desc_match.group(1).strip().strip("'\"")
    else:
        # Fallback: try message=...;
        msg_match = re.search(r"message\s*=\s*([^;]+)", original_error, re.IGNORECASE)
        if msg_match:
            result['description'] = msg_match.group(1).strip()
    
    # Extract requestTrackingId
    tracking_match = re.search(r"requestTrackingId\s*=\s*([^;\s]+)", original_error, re.IGNORECASE)
    if tracking_match:
        result['request_tracking_id'] = tracking_match.group(1).strip()
    
    # Extract statusCode, httpStatusCode, or Status:
    status_match = re.search(r"(?:statusCode|httpStatusCode)\s*=\s*(\d+)", original_error, re.IGNORECASE)
    if status_match:
        result['status_code'] = status_match.group(1)
    else:
        # Try "Status: 500" format
        status_match = re.search(r"Status:\s*(\d+)", original_error, re.IGNORECASE)
        if status_match:
            result['status_code'] = status_match.group(1)
    
    # Extract errorCode
    error_code_match = re.search(r"errorCode\s*=\s*([A-Z_0-9]+)", original_error, re.IGNORECASE)
    if error_code_match:
        result['error_code'] = error_code_match.group(1)
    
    return result


def scan_all_items(table):
    """Scan all items from DynamoDB table with pagination."""
    items = []
    response = table.scan()
    items.extend(response.get('Items', []))
    
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response.get('Items', []))
    
    return items


def load_all_timing_data() -> dict:
    """
    Load all timing data from the in-flight tracker table into a dictionary.
    
    This is MUCH more efficient than individual lookups - one scan instead of
    thousands of filtered scans.
    
    Returns:
        dict mapping filename to timing data dict (most recent entry per filename)
    """
    timing_cache = {}
    
    try:
        table = dynamodb.Table(RATE_LIMIT_TABLE)
        logger.info(f"Loading timing data from table: {RATE_LIMIT_TABLE}")
        
        # Scan all file entries (those starting with IN_FLIGHT_FILE_PREFIX)
        response = table.scan(
            FilterExpression='begins_with(counter_id, :prefix)',
            ExpressionAttributeValues={
                ':prefix': IN_FLIGHT_FILE_PREFIX
            }
        )
        
        items = response.get('Items', [])
        scan_count = 1
        
        while 'LastEvaluatedKey' in response:
            response = table.scan(
                FilterExpression='begins_with(counter_id, :prefix)',
                ExpressionAttributeValues={
                    ':prefix': IN_FLIGHT_FILE_PREFIX
                },
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            items.extend(response.get('Items', []))
            scan_count += 1
        
        logger.info(f"Loaded {len(items)} timing records from DynamoDB after {scan_count} scan(s)")
        
        # Build cache keyed by filename, keeping the most recent entry per file
        # Group by filename first
        by_filename = {}
        for item in items:
            filename = item.get('filename', '')
            if not filename:
                continue
            
            if filename not in by_filename:
                by_filename[filename] = []
            by_filename[filename].append(item)
        
        # For each filename, pick the most recent entry (by started_at)
        for filename, file_items in by_filename.items():
            # Sort by started_at descending
            file_items.sort(key=lambda x: x.get('started_at', ''), reverse=True)
            latest = file_items[0]
            
            # Build the timing data dict
            timing_data = {
                'started_at': latest.get('started_at'),
                'released_at': latest.get('released_at'),
                'crashed': False,
                'crashed_at': latest.get('crashed_at'),
                'crash_details': latest.get('crash_details')
            }
            
            # Determine if this was a crash
            if timing_data['crashed_at']:
                timing_data['crashed'] = True
                if not timing_data['released_at']:
                    timing_data['released_at'] = timing_data['crashed_at']
            elif timing_data['started_at'] and not timing_data['released_at']:
                if not latest.get('released'):
                    timing_data['crashed'] = True
            
            timing_cache[filename] = timing_data
        
        logger.info(f"Built timing cache with {len(timing_cache)} unique filenames")
        return timing_cache
        
    except Exception as e:
        logger.error(f"Error loading timing data: {e}", exc_info=True)
        return {}


def get_timing_data_from_cache(filename: str, timing_cache: dict) -> dict:
    """
    Look up timing data from the pre-loaded cache.
    
    Args:
        filename: The PDF filename to look up
        timing_cache: Pre-loaded timing data dictionary
        
    Returns:
        dict with 'started_at', 'released_at', 'crashed', 'crashed_at', and 'crash_details' keys
    """
    default_result = {
        'started_at': None,
        'released_at': None,
        'crashed': False,
        'crashed_at': None,
        'crash_details': None
    }
    
    if not filename:
        return default_result
    
    return timing_cache.get(filename, default_result)


def create_excel_report(items: list, prescan_cache: dict) -> bytes:
    """Create an Excel spreadsheet from the analysis data."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Failure Analysis"
    
    # Define headers - includes parsed error fields, timing columns, and prescan data
    headers = [
        'Filename',
        'Collection Folder',
        'S3 Key',
        'Analysis Timestamp',
        'API Type',
        'Started At',
        'Released At',
        'Duration (s)',
        'Crashed',
        'Crash Reason',
        'Exit Code',
        'File Size (MB)',
        'Page Count',
        'Image Count',
        'Font Count',
        'Encrypted',
        'PDF Version',
        'Likely Cause',
        'Issue Count',
        'Error API',
        'Error Description',
        'Error Status Code',
        'Error Code',
        'Request Tracking ID',
        'Original Error',
        # Prescan data columns
        'Prescan Info Available',
        'Prescan Matching Info',
        'Prescan Risk Level',
        'Prescan Complexity Score',
        'Prescan Risk Factors',
        'Prescan Total Images',
        'Prescan Total Text Chars',
        'Prescan Total Fonts',
        'Prescan Total Drawings',
        'Prescan Max Image Pixels',
        'Prescan Images Over 1MP',
        'Prescan Images Over 4MP',
        'Prescan Unique Page Sizes',
        'Prescan Pages Over Tabloid',
        'Prescan MB Per Page',
        'Prescan Images Per Page',
        'Prescan Chars Per Page',
        'Prescan Drawings Per Page',
        'Prescan Has Javascript',
        'Prescan Has Embedded Files',
        'Prescan Recommended Chunk Size'
    ]
    
    # Style definitions
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    prescan_header_fill = PatternFill(start_color='70AD47', end_color='70AD47', fill_type='solid')  # Green for prescan
    header_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    crash_fill = PatternFill(start_color='FFCCCC', end_color='FFCCCC', fill_type='solid')
    
    # Index where prescan columns start (0-indexed)
    prescan_start_col = 26  # After 'Original Error'
    
    # Write headers
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        # Use green fill for prescan columns
        if col > prescan_start_col:
            cell.fill = prescan_header_fill
        else:
            cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border
    
    # Write data rows
    for row_num, item in enumerate(items, 2):
        # Calculate duration if both timestamps exist
        duration = ''
        started_at = item.get('started_at', '')
        # Use failed_at if available (new approach), otherwise use released_at
        released_at = item.get('failed_at', '') or item.get('released_at', '')
        crashed = item.get('crashed', False)
        
        if started_at and released_at:
            try:
                start_dt = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                end_dt = datetime.fromisoformat(released_at.replace('Z', '+00:00'))
                duration = round((end_dt - start_dt).total_seconds(), 1)
            except Exception:
                duration = ''
        
        # Extract crash details if available
        crash_reason = ''
        exit_code = ''
        crash_details = item.get('crash_details')
        if crash_details:
            try:
                if isinstance(crash_details, str):
                    details = json.loads(crash_details)
                else:
                    details = crash_details
                crash_reason = details.get('stopped_reason', '') or details.get('container_reason', '')
                exit_code = details.get('exit_code', '')
            except Exception:
                pass
        
        # Parse Adobe error message for structured fields
        original_error = item.get('original_error', '')
        parsed_error = parse_adobe_error(original_error)
        
        # Extract collection folder from S3 key
        s3_key = item.get('s3_key', '')
        collection_folder = extract_collection_folder(s3_key)
        
        # Look up prescan data for this file
        filename = item.get('filename', '')
        # Log first 10 lookups for debugging
        log_this_lookup = (row_num <= 12)  # row_num starts at 2
        prescan, prescan_matching_info = get_prescan_data(filename, collection_folder, prescan_cache, log_misses=log_this_lookup)
        
        # Log the constructed key for first few rows
        if row_num <= 12:
            constructed_key = f"pdf/{collection_folder}/{filename}"
            logger.info(f"Row {row_num}: filename={filename}, collection_folder={collection_folder}, constructed_key={constructed_key}, prescan_found={bool(prescan)}")
        
        prescan_available = 'True' if prescan else 'False'
        
        row_data = [
            filename,
            collection_folder,
            s3_key,
            item.get('analysis_timestamp', ''),
            item.get('api_type', ''),
            started_at,
            released_at,
            duration,
            'Yes' if crashed else 'No',
            crash_reason[:100] if crash_reason else '',  # Truncate long reasons
            exit_code if exit_code is not None else '',
            item.get('file_size_mb', ''),
            item.get('page_count', 0),
            item.get('image_count', 0),
            item.get('font_count', 0),
            'Yes' if item.get('has_encryption') else 'No',
            item.get('pdf_version', ''),
            item.get('likely_cause', ''),
            item.get('issue_count', 0),
            parsed_error['api_name'],
            parsed_error['description'],
            parsed_error['status_code'],
            parsed_error['error_code'],
            parsed_error['request_tracking_id'],
            original_error[:500],  # Truncate long errors
            # Prescan data
            prescan_available,
            prescan_matching_info,
            prescan.get('risk_level', ''),
            prescan.get('complexity_score', ''),
            format_for_excel(prescan.get('risk_factors', '')),
            prescan.get('total_images', ''),
            prescan.get('total_text_chars', ''),
            prescan.get('total_fonts', ''),
            prescan.get('total_drawings', ''),
            prescan.get('max_image_pixels', ''),
            prescan.get('images_over_1mp', ''),
            prescan.get('images_over_4mp', ''),
            prescan.get('unique_page_sizes', ''),
            prescan.get('pages_over_tabloid', ''),
            prescan.get('mb_per_page', ''),
            prescan.get('images_per_page', ''),
            prescan.get('chars_per_page', ''),
            prescan.get('drawings_per_page', ''),
            'Yes' if prescan.get('has_javascript') else ('No' if prescan else ''),
            'Yes' if prescan.get('has_embedded_files') else ('No' if prescan else ''),
            prescan.get('recommended_pages_per_chunk', '')
        ]
        
        for col, value in enumerate(row_data, 1):
            # Sanitize string values to remove illegal Excel characters
            sanitized_value = sanitize_for_excel(value) if isinstance(value, str) else value
            cell = ws.cell(row=row_num, column=col, value=sanitized_value)
            cell.border = thin_border
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            # Highlight crashed rows
            if crashed:
                cell.fill = crash_fill
    
    # Auto-adjust column widths - updated for new columns including prescan
    # Original columns + prescan columns (added Prescan Matching Info column)
    column_widths = [
        30, 30, 50, 25, 12, 25, 25, 12, 10, 40, 10, 12, 12, 12, 12, 10, 12, 50, 12, 20, 40, 12, 25, 40, 60,
        # Prescan columns: Info Available, Matching Info, Risk Level, Complexity, Risk Factors, etc.
        15, 60, 12, 12, 40, 12, 12, 12, 12, 15, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12
    ]
    for col, width in enumerate(column_widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    
    # Add auto-filter to the header row (simpler than Table, allows editing)
    if len(items) > 0:
        last_col_letter = get_column_letter(len(headers))
        last_row = len(items) + 1  # +1 for header row
        ws.auto_filter.ref = f"A1:{last_col_letter}{last_row}"
    
    # Freeze header row
    ws.freeze_panes = 'A2'
    
    # Save to bytes
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def handler(event, context):
    """Generate Excel report from failure analysis data."""
    logger.info(f"Received event: {json.dumps(event)}")
    
    if not ANALYSIS_TABLE:
        logger.error("ANALYSIS_TABLE not configured")
        return {'statusCode': 500, 'body': 'ANALYSIS_TABLE not configured'}
    
    if not REPORT_BUCKET:
        logger.error("REPORT_BUCKET not configured")
        return {'statusCode': 500, 'body': 'REPORT_BUCKET not configured'}
    
    # Scan all items from DynamoDB
    table = dynamodb.Table(ANALYSIS_TABLE)
    logger.info(f"Scanning table: {ANALYSIS_TABLE}")
    items = scan_all_items(table)
    logger.info(f"Found {len(items)} analysis records")
    
    if not items:
        logger.info("No analysis data found, skipping report generation")
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'No analysis data found'})
        }
    
    # Load all cache data upfront for efficiency (single scans instead of per-record lookups)
    import time
    cache_start = time.time()
    
    logger.info("Loading timing data from in-flight tracker...")
    timing_cache = load_all_timing_data()
    
    logger.info("Loading prescan data...")
    prescan_cache = load_all_prescan_data()
    
    cache_elapsed = time.time() - cache_start
    logger.info(f"Cache loading completed in {cache_elapsed:.1f}s")
    
    # Enrich items with timing data
    # Priority: 1) Use timing data stored in failure analysis table (permanent)
    #           2) Fall back to in-flight tracker cache (may have expired)
    enrich_start = time.time()
    logger.info(f"Enriching {len(items)} items with timing data")
    items_with_stored_timing = 0
    items_with_cache_timing = 0
    
    for item in items:
        filename = item.get('filename', '')
        
        # Check if timing data is already in the failure analysis record (new approach)
        started_at = item.get('started_at', '')
        failed_at = item.get('failed_at', '')
        
        if started_at and failed_at:
            # Use timing data from failure analysis table (permanent storage)
            item['released_at'] = failed_at  # Use failed_at as released_at for consistency
            item['crashed'] = True  # If we have failure analysis, it was a failure
            items_with_stored_timing += 1
        elif filename:
            # Fall back to in-flight tracker cache for older entries
            timing_data = get_timing_data_from_cache(filename, timing_cache)
            if not started_at:
                item['started_at'] = timing_data.get('started_at', '')
            if not item.get('released_at'):
                item['released_at'] = timing_data.get('released_at', '')
            item['crashed'] = timing_data.get('crashed', False)
            item['crashed_at'] = timing_data.get('crashed_at', '')
            item['crash_details'] = timing_data.get('crash_details')
            if timing_data.get('started_at'):
                items_with_cache_timing += 1
    
    enrich_elapsed = time.time() - enrich_start
    logger.info(f"Enrichment completed in {enrich_elapsed:.1f}s - {items_with_stored_timing} with stored timing, {items_with_cache_timing} from cache")
    
    # Sort by timestamp descending
    items.sort(key=lambda x: x.get('analysis_timestamp', ''), reverse=True)
    
    # Generate Excel report
    excel_bytes = create_excel_report(items, prescan_cache)
    
    # Generate filename with timestamp (US Eastern time - handles EST/EDT automatically)
    from zoneinfo import ZoneInfo
    eastern_tz = ZoneInfo('America/New_York')
    now_eastern = datetime.now(eastern_tz)
    timestamp_str = now_eastern.strftime('%Y%m%d_%H%M%S')
    report_key = f"reports/failure_analysis_summary/failure_analysis_report_{timestamp_str}.xlsx"
    
    # Upload to S3
    s3.put_object(
        Bucket=REPORT_BUCKET,
        Key=report_key,
        Body=excel_bytes,
        ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    
    # Count crashes for summary
    crash_count = sum(1 for item in items if item.get('crashed', False))
    
    logger.info(f"Saved report to s3://{REPORT_BUCKET}/{report_key}")
    logger.info(f"Report contains {len(items)} records, {crash_count} with container crashes")
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Report generated successfully',
            'record_count': len(items),
            'crash_count': crash_count,
            'report_location': f"s3://{REPORT_BUCKET}/{report_key}"
        })
    }
