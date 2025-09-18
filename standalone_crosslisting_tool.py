#!/usr/bin/env python3
"""
Canvas Cross-Listing Tool - Streamlined for Faculty & Administrators

Automates Canvas course cross-listing operations. Cross-listing combines multiple
course sections into one Canvas course shell for easier management.

Key Features:
• Interactive term and section selection
• Smart validation of cross-listing candidates
• Cross-list and un-cross-list operations
• CSV export for documentation
• Service ticket integration support

Prerequisites:
• Parent course: unpublished (no student activity)
• Child course: published
• Different courses (not same course)
• Neither section already cross-listed

Setup:
1. Create .env file with Canvas API credentials
2. Install: pip install python-dotenv
3. Ensure API token has cross-listing permissions

For detailed function documentation, see FUNCTION_DOCUMENTATION.md
"""

import os
import json
import csv
import http.client
import urllib.parse
import time
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Union, Generator, Tuple
from dataclasses import dataclass
from functools import lru_cache

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class CanvasConfig:
    """Configuration settings for Canvas API operations."""
    api_token: str
    base_url: str
    account_id: int = 415
    per_page: int = 100
    timeout: int = 30
    max_retries: int = 3
    requests_per_minute: int = 60
    retry_delay: float = 1.0
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        if not self.api_token or self.api_token == 'PLACEHOLDERAPIKEY':
            raise ValueError("API token is required and cannot be placeholder")
        
        if not self.base_url:
            raise ValueError("Base URL is required")
        
        # Ensure base URL doesn't end with slash
        self.base_url = self.base_url.rstrip('/')


class CanvasAPIError(Exception):
    """Custom exception for Canvas API errors with detailed context."""
    def __init__(self, message: str, status_code: Optional[int] = None, 
                 response_body: Optional[str] = None, request_url: Optional[str] = None):
        self.message = message
        self.status_code = status_code
        self.response_body = response_body
        self.request_url = request_url
        super().__init__(self.message)


class CanvasAPIClient:
    """Canvas API client with rate limiting and error handling."""
    
    def __init__(self, config: CanvasConfig):
        self.config = config
    
    def _rate_limit(self):
        """Implement basic rate limiting between requests."""
        # Simple delay between requests - removed shared state for thread safety
        time.sleep(0.1)  # 100ms delay instead of 1 second
    
    def _make_request(self, method: str, path: str, params: Optional[Dict] = None, 
                     data: Optional[Dict] = None) -> Dict[str, Any]:
        """Make HTTP request to Canvas API with error handling."""
        self._rate_limit()
        
        # Parse URL
        parsed_url = urllib.parse.urlparse(self.config.base_url)
        host = parsed_url.netloc
        port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)
        
        # Build full path
        full_path = path
        if params:
            # IMPORTANT: doseq=True encodes list params correctly (include[]=a&include[]=b)
            query_string = urllib.parse.urlencode(params, doseq=True)
            full_path += '?' + query_string
        
        # Create connection
        if parsed_url.scheme == 'https':
            conn = http.client.HTTPSConnection(host, port, timeout=self.config.timeout)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.config.timeout)
        
        try:
            # Set headers
            headers = {
                'Authorization': f'Bearer {self.config.api_token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            # Prepare request body
            request_body = None
            if data:
                request_body = json.dumps(data).encode('utf-8')
            
            # Make request
            conn.request(method, full_path, body=request_body, headers=headers)
            response = conn.getresponse()
            
            # Read response
            response_body = response.read().decode('utf-8')
            
            # Handle response
            if response.status in [200, 201, 204]:
                if response_body.strip():
                    try:
                        return json.loads(response_body)
                    except json.JSONDecodeError as e:
                        raise CanvasAPIError(f"Invalid JSON response: {e}", response.status, response_body, full_path)
                else:
                    return {}
            elif response.status == 401:
                logger.error(f"Authentication failed (401): {response_body}")
                raise CanvasAPIError(
                    f"Authentication failed: {response.status} {response.reason}. "
                    f"Please check your API token and try again later.",
                    response.status,
                    response_body,
                    full_path
                )
            elif response.status == 403:
                logger.error(f"Permission denied (403): {response_body}")
                raise CanvasAPIError(
                    f"Permission denied: {response.status} {response.reason}. "
                    f"You may not have the necessary permissions for this operation.",
                    response.status,
                    response_body,
                    full_path
                )
            elif response.status == 429:
                logger.error(f"Rate limit exceeded (429): {response_body}")
                raise CanvasAPIError(
                    f"Rate limit exceeded: {response.status} {response.reason}. "
                    f"Please wait a few minutes and try again.",
                    response.status,
                    response_body,
                    full_path
                )
            else:
                logger.error(f"API Error Response: {response_body}")
                raise CanvasAPIError(
                    f"API request failed: {response.status} {response.reason}",
                    response.status,
                    response_body,
                    full_path
                )
        
        except (http.client.HTTPException, OSError) as e:
            raise CanvasAPIError(f"Network error: {e}", request_url=full_path)
        finally:
            conn.close()
    
    def get_paginated_data(self, path: str, params: Optional[Dict] = None, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieve paginated data from Canvas API with retry logic."""
        if params is None:
            params = {}
        
        # Set pagination parameters
        params['per_page'] = self.config.per_page
        
        all_data = []
        page = 1
        consecutive_errors = 0
        max_consecutive_errors = 3
        
        # Safety limits to prevent infinite loops
        max_pages_absolute = 50  # Never fetch more than 50 pages
        seen_data_hashes = set()  # Track duplicate responses
        
        while True:
            # Check page limit for testing
            if max_pages and page > max_pages:
                logger.info(f"Reached maximum page limit ({max_pages}). Stopping pagination.")
                break
            
            # Safety check - absolute maximum
            if page > max_pages_absolute:
                logger.warning(f"Reached absolute page limit ({max_pages_absolute}). Stopping pagination.")
                break
            
            # Add page parameter
            params['page'] = page
            
            for attempt in range(self.config.max_retries):
                try:
                    logger.info(f"Fetching page {page} from {path}")
                    response = self._make_request('GET', path, params)
                    
                    # Handle different response formats
                    if isinstance(response, list):
                        data = response
                    elif isinstance(response, dict) and 'data' in response:
                        data = response['data']
                    else:
                        data = [response]
                    
                    if not data:
                        break
                    
                    # Check for duplicate data (indicates API is returning same page repeatedly)
                    data_hash = hash(str(sorted([item.get('id', 0) for item in data if isinstance(item, dict)])))
                    if data_hash in seen_data_hashes:
                        logger.warning(f"Detected duplicate data on page {page}. Stopping pagination.")
                        break
                    seen_data_hashes.add(data_hash)
                    
                    all_data.extend(data)
                    consecutive_errors = 0  # Reset error counter on success
                    
                    # Check if we have more pages
                    if len(data) < self.config.per_page:
                        break
                    
                    page += 1
                    break  # Success, move to next page
                    
                except CanvasAPIError as e:
                    consecutive_errors += 1
                    logger.error(f"Error fetching page {page} (attempt {attempt + 1}): {e.message}")
                    
                    if e.status_code == 401:
                        logger.error("Authentication failed. Please check your API token.")
                        return all_data  # Stop on auth failure
                    elif e.status_code == 429:
                        logger.warning("Rate limit hit. Waiting 60 seconds before retry...")
                        time.sleep(60)
                    else:
                        # Wait before retry
                        wait_time = self.config.retry_delay * (attempt + 1)
                        logger.info(f"Waiting {wait_time} seconds before retry...")
                        time.sleep(wait_time)
                    
                    if attempt == self.config.max_retries - 1:
                        logger.error(f"Failed to fetch page {page} after {self.config.max_retries} attempts")
                        if consecutive_errors >= max_consecutive_errors:
                            logger.error(f"Too many consecutive errors ({consecutive_errors}). Stopping pagination.")
                            break
                        break  # Move to next page even if this one failed
        
        logger.info(f"Retrieved {len(all_data)} total items")
        return all_data


def get_config() -> CanvasConfig:
    """Get Canvas API configuration from environment variables."""
    api_token = os.getenv('CANVAS_API_TOKEN')
    base_url = os.getenv('CANVAS_BASE_URL')
    
    # Read optional settings with defaults
    try:
        account_id = int(os.getenv('CANVAS_ACCOUNT_ID', '415'))
    except ValueError:
        account_id = 415
    
    try:
        per_page = int(os.getenv('CANVAS_PER_PAGE', '100'))
    except ValueError:
        per_page = 100
    
    try:
        timeout = int(os.getenv('CANVAS_TIMEOUT', '30'))
    except ValueError:
        timeout = 30
    
    try:
        max_retries = int(os.getenv('CANVAS_MAX_RETRIES', '3'))
    except ValueError:
        max_retries = 3
    
    try:
        requests_per_minute = int(os.getenv('CANVAS_REQUESTS_PER_MINUTE', '60'))
    except ValueError:
        requests_per_minute = 60
    
    try:
        retry_delay = float(os.getenv('CANVAS_RETRY_DELAY', '1.0'))
    except ValueError:
        retry_delay = 1.0
    
    return CanvasConfig(
        api_token=api_token,
        base_url=base_url,
        account_id=account_id,
        per_page=per_page,
        timeout=timeout,
        max_retries=max_retries,
        requests_per_minute=requests_per_minute,
        retry_delay=retry_delay
    )


def fetch_active_terms(config: CanvasConfig) -> List[Dict[str, Any]]:
    """Fetch active enrollment terms from Canvas API."""
    client = CanvasAPIClient(config)
    
    try:
        # Terms endpoint returns a single object { enrollment_terms: [...] }
        path = f"/api/v1/accounts/{config.account_id}/terms"
        params = {'workflow_state[]': 'active', 'include[]': 'overrides'}
        resp = client._make_request('GET', path, params)
        if isinstance(resp, dict) and 'enrollment_terms' in resp:
            return resp['enrollment_terms']
        # Fallback: some proxies wrap in a list
        if isinstance(resp, list) and resp and isinstance(resp[0], dict) and 'enrollment_terms' in resp[0]:
            return resp[0]['enrollment_terms']
        return []
        
    except CanvasAPIError as e:
        logger.error(f"Failed to fetch terms: {e.message}")
        return []


def list_account_courses_filtered(
    config: CanvasConfig,
    term_id: int,
    teacher_ids: Optional[list[int]] = None,
    subaccount_ids: Optional[list[int]] = None,
    search_term: Optional[str] = None,
    only_published: bool = False,
    states: Optional[list[str]] = None
) -> list[dict]:
    """
    STAFF NARROWING: Use account-level filters so we don't load the whole term.
    Server-side filters supported by Canvas: enrollment_term_id, by_teachers[], by_subaccounts[],
    search_term, published, state[], include[] (teachers, term, account_name).
    """
    client = CanvasAPIClient(config)
    path = f"/api/v1/accounts/{config.account_id}/courses"
    params: dict = {
        "enrollment_term_id": term_id,
        # Either require teacher enrollments or at least any enrollments to skip empty shells:
        "with_enrollments": "true",
        "include[]": ["teachers", "term", "account_name"],
        "per_page": config.per_page
    }
    # States default: available (and optionally created)
    effective_states = states if states else ["available"]
    for st in effective_states:
        params.setdefault("state[]", []).append(st)
    if only_published:
        params["published"] = "true"
    if teacher_ids:
        for tid in teacher_ids:
            params.setdefault("by_teachers[]", []).append(tid)
        # When filtering by teachers, you can also add enrollment_type to be explicit
        params.setdefault("enrollment_type[]", []).append("teacher")
    if subaccount_ids:
        for sid in subaccount_ids:
            params.setdefault("by_subaccounts[]", []).append(sid)
    if search_term and len(search_term) >= 2:
        params["search_term"] = search_term
    return client.get_paginated_data(path, params)

def list_user_term_courses_via_enrollments(config: CanvasConfig, user_id: int, term_id: int) -> list[dict]:
    """
    FACULTY PATH (future GUI-ready): scope by term USING USER ENROLLMENTS, then hydrate courses.
    """
    client = CanvasAPIClient(config)
    # 1) Teacher enrollments scoped to the term
    enroll_path = f"/api/v1/users/{user_id}/enrollments"
    enroll_params = {
        "type[]": "TeacherEnrollment",
        "enrollment_state": "active",
        "enrollment_term_id": term_id,
        "per_page": config.per_page
    }
    enrollments = client.get_paginated_data(enroll_path, enroll_params)
    course_ids = sorted({e.get("course_id") for e in enrollments if e.get("course_id")})
    # 2) Hydrate courses with term & teachers for UI
    courses: list[dict] = []
    for cid in course_ids:
        course = client._make_request(
            "GET",
            f"/api/v1/courses/{cid}",
            params={"include[]": ["term", "teachers"]}
        )
        if course:
            courses.append(course)
    return courses

def list_sections_for_courses(config: CanvasConfig, courses: list[dict]) -> list[dict]:
    """Fetch sections only for the narrowed set of courses."""
    client = CanvasAPIClient(config)
    out: list[dict] = []

    # Deduplicate courses by ID to prevent fetching sections multiple times for same course
    unique_courses = {}
    for course in courses:
        cid = course.get("id")
        if cid and cid not in unique_courses:
            unique_courses[cid] = course

    print(f"Debug: Processing {len(unique_courses)} unique courses (was {len(courses)} total)")

    for course in unique_courses.values():
        cid = course.get("id")
        if not cid:
            continue
        secs = client.get_paginated_data(f"/api/v1/courses/{cid}/sections", {"per_page": config.per_page})
        for s in secs or []:
            out.append({
                "section_id": s.get("id"),
                "section_name": s.get("name"),
                "course_id": cid,
                "course_name": course.get("name"),
                "course_code": course.get("course_code"),
                "sis_course_id": course.get("sis_course_id"),
                "sis_section_id": s.get("sis_section_id"),
                "workflow_state": course.get("workflow_state"),
                "published": course.get("workflow_state") == "available",
                "teachers": course.get("teachers", []),
                "cross_listed": s.get("cross_listed", False),
                "parent_course_id": s.get("parent_course_id"),
                "full_title": f"{course.get('course_code')}: {course.get('name')}: Section {s.get('name')}"
            })
    return out

def get_course_sections(
    config: CanvasConfig,
    term_id: int,
    user_id: Optional[int] = None,
    teacher_ids: Optional[list[int]] = None,
    subaccount_ids: Optional[list[int]] = None,
    search_term: Optional[str] = None,
    only_published: bool = False
) -> List[Dict[str, Any]]:
    """Get course sections for a term with robust narrowing."""
    try:
        print(f"🔍 Fetching course sections for term {term_id}...")
        if user_id:
            # Faculty path (scoped to term via enrollments, then hydrate)
            courses = list_user_term_courses_via_enrollments(config, user_id, term_id)
        else:
            # Staff narrowing path (account-level filters)
            courses = list_account_courses_filtered(
                config,
                term_id,
                teacher_ids=teacher_ids,
                subaccount_ids=subaccount_ids,
                search_term=search_term,
                only_published=only_published
            )
        sections = list_sections_for_courses(config, courses)
        print(f"✅ Found {len(sections)} course sections (after narrowing)")
        return sections
    except CanvasAPIError as e:
        logger.error(f"Failed to fetch course sections: {e.message}")
        return []


def validate_cross_listing_candidates(parent_section: Dict[str, Any], child_section: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate if two sections can be cross-listed.
    
    This function performs comprehensive validation to ensure that cross-listing
    operations meet Canvas requirements and best practices.
    
    Validation Rules:
    1. Parent section must not already be cross-listed
    2. Child section must not already be cross-listed
    3. Sections must be from different courses (not same course)
    4. Parent course must be unpublished (no student activity)
    5. Child course must be published (ready for cross-listing)
    
    Args:
        parent_section: Dictionary containing parent section data
        child_section: Dictionary containing child section data
        
    Returns:
        Tuple of (is_valid: bool, message: str)
        
    Example:
        is_valid, message = validate_cross_listing_candidates(parent, child)
        if not is_valid:
            print(f"Validation failed: {message}")
    """
    # Check if sections are already cross-listed
    if parent_section.get('cross_listed'):
        return False, "Parent section is already cross-listed"
    
    if child_section.get('cross_listed'):
        return False, "Child section is already cross-listed"
    
    # Check if sections are in the same course
    if parent_section['course_id'] == child_section['course_id']:
        return False, "Cannot cross-list sections from the same course"
    
    # Check if parent course is published (should be unpublished for cross-listing)
    if parent_section.get('published'):
        return False, "Parent course should be unpublished for cross-listing"
    
    # Check if child course is published (should be published for cross-listing)
    if not child_section.get('published'):
        return False, "Child course should be published for cross-listing"
    
    # Additional validation can be added here (e.g., course number matching)
    
    return True, "Validation passed"


def cross_list_section(config: CanvasConfig, child_section_id: int, parent_course_id: int) -> bool:
    """Cross-list a child section into a parent course."""
    client = CanvasAPIClient(config)
    
    try:
        path = f"/api/v1/sections/{child_section_id}/crosslist"
        data = {
            'new_course_id': parent_course_id
        }
        
        print(f"🔄 Cross-listing section {child_section_id} into course {parent_course_id}...")
        response = client._make_request('POST', path, data=data)
        
        print(f"✅ Successfully cross-listed section {child_section_id}")
        return True
        
    except CanvasAPIError as e:
        logger.error(f"Failed to cross-list section: {e.message}")
        return False


def un_cross_list_section(config: CanvasConfig, section_id: int) -> bool:
    """Un-cross-list a section (remove it from cross-listing)."""
    client = CanvasAPIClient(config)
    
    try:
        path = f"/api/v1/sections/{section_id}/crosslist"
        
        print(f"🔄 Un-cross-listing section {section_id}...")
        response = client._make_request('DELETE', path)
        
        print(f"✅ Successfully un-cross-listed section {section_id}")
        return True
        
    except CanvasAPIError as e:
        logger.error(f"Failed to un-cross-list section: {e.message}")
        return False


def display_sections_table(sections: List[Dict[str, Any]]) -> None:
    """Display sections in a formatted table for user interaction."""
    if not sections:
        print("No sections found.")
        return
    
    print("\n" + "=" * 120)
    print("COURSE SECTIONS")
    print("=" * 120)
    print(f"{'#':<3} {'Course Code':<15} {'Section':<10} {'Published':<10} {'Cross-listed':<12} {'Course Name'}")
    print("-" * 120)
    
    for i, section in enumerate(sections, 1):
        published = "Yes" if section.get('published') else "No"
        cross_listed = "Yes" if section.get('cross_listed') else "No"
        
        print(f"{i:<3} {section['course_code']:<15} {section['section_name']:<10} {published:<10} {cross_listed:<12} {section['course_name']}")


def get_user_selection(sections: List[Dict[str, Any]], prompt: str) -> Optional[Dict[str, Any]]:
    """Get user selection from sections list with input validation."""
    while True:
        try:
            choice = input(f"\n{prompt} (1-{len(sections)}) or 'q' to quit: ").strip()
            
            if choice.lower() == 'q':
                return None
            
            if choice.isdigit():
                choice_num = int(choice)
                if 1 <= choice_num <= len(sections):
                    return sections[choice_num - 1]
                else:
                    print(f"❌ Please enter a number between 1 and {len(sections)}")
            else:
                print("❌ Please enter a valid number or 'q' to quit")
        except ValueError:
            print("❌ Please enter a valid number or 'q' to quit")


def export_sections_to_csv(sections: List[Dict[str, Any]], filename: str = 'sections_export.csv') -> None:
    """
    Export sections to CSV file for documentation and analysis.
    
    This function creates a CSV file containing all section information,
    which is useful for documentation, reporting, and service ticket integration.
    
    CSV Columns:
    - Section ID: Canvas section ID
    - Section Name: Section number/name
    - Course ID: Canvas course ID
    - Course Name: Full course name
    - Course Code: Course identifier
    - SIS Course ID: Student Information System course ID
    - SIS Section ID: Student Information System section ID
    - Published: Whether course is published (Yes/No)
    - Cross-listed: Whether section is cross-listed (Yes/No)
    - Parent Course ID: ID of parent course (if cross-listed)
    - Full Title: Complete section title for display
    
    Args:
        sections: List of section dictionaries to export
        filename: Output CSV filename (default: 'sections_export.csv')
        
    Raises:
        Exception: If file writing fails
        
    Example:
        export_sections_to_csv(sections, 'fall_2024_sections.csv')
        # Creates: fall_2024_sections.csv with all section data
    """
    if not sections:
        logger.warning("No sections to export")
        return
    
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['Section ID', 'Section Name', 'Course ID', 'Course Name', 'Course Code', 
                         'SIS Course ID', 'SIS Section ID', 'Published', 'Cross-listed', 'Parent Course ID', 'Full Title']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for section in sections:
                writer.writerow({
                    'Section ID': section.get('section_id', ''),
                    'Section Name': section.get('section_name', ''),
                    'Course ID': section.get('course_id', ''),
                    'Course Name': section.get('course_name', ''),
                    'Course Code': section.get('course_code', ''),
                    'SIS Course ID': section.get('sis_course_id', ''),
                    'SIS Section ID': section.get('sis_section_id', ''),
                    'Published': 'Yes' if section.get('published') else 'No',
                    'Cross-listed': 'Yes' if section.get('cross_listed') else 'No',
                    'Parent Course ID': section.get('parent_course_id', ''),
                    'Full Title': section.get('full_title', '')
                })
        
        logger.info(f"Exported {len(sections)} sections to {filename}")
        
    except Exception as e:
        logger.error(f"Failed to export CSV: {e}")
        raise


class CrosslistingService:
    """Simple service interface for crosslisting operations - similar to VB.NET pattern"""
    
    def __init__(self, config: CanvasConfig):
        self.config = config
        self.client = CanvasAPIClient(config)
    
    def crosslist_sections(self, child_section_id: int, parent_course_id: int) -> Tuple[bool, str]:
        """
        Simple interface for crosslisting - similar to myCanvasInterface.CrossListSections()
        
        Args:
            child_section_id: Section to be cross-listed
            parent_course_id: Course to cross-list into
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            success = cross_list_section(self.config, child_section_id, parent_course_id)
            if success:
                return True, f"Successfully cross-listed section {child_section_id} into course {parent_course_id}"
            else:
                return False, "Cross-listing operation failed"
        except Exception as e:
            return False, f"Error during cross-listing: {str(e)}"
    
    def uncrosslist_section(self, section_id: int) -> Tuple[bool, str]:
        """
        Simple interface for un-crosslisting
        
        Args:
            section_id: Section to un-crosslist
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            success = un_cross_list_section(self.config, section_id)
            if success:
                return True, f"Successfully un-cross-listed section {section_id}"
            else:
                return False, "Un-cross-listing operation failed"
        except Exception as e:
            return False, f"Error during un-cross-listing: {str(e)}"


def main():
    """Main function to run the cross-listing tool."""
    print("=" * 60)
    print("Canvas LMS - Cross-Listing Tool")
    print("=" * 60)
    
    # Load configuration
    try:
        config = get_config()
    except ValueError as e:
        print(f"❌ Configuration Error: {e}")
        return
    
    # Initialize Canvas client
    client = CanvasAPIClient(config)
    
    # Get enrollment terms
    print("\nFetching available enrollment terms...")
    terms = fetch_active_terms(config)
    
    if not terms:
        print("❌ No enrollment terms found or error occurred.")
        return
    
    # Display terms
    print(f"\nAvailable Terms ({len(terms)} found):")
    print("-" * 80)
    for i, term in enumerate(terms, 1):
        start_date = term.get('start_at', 'No start date')
        end_date = term.get('end_at', 'No end date')
        print(f"{i:2d}. {term['name']:<30} (ID: {term['id']})")
        print(f"     Start: {start_date:<25} End: {end_date}")
    
    # Get user selection
    selected_term = get_user_selection(terms, "Select term")
    if not selected_term:
        print("Operation cancelled.")
        return
    
    print(f"\n✅ Selected: {selected_term['name']} (ID: {selected_term['id']})")
    
    # Optional narrowing inputs for STAFF usage
    print("\nOptional narrowing (press Enter to skip):")
    teacher_id_input = input("Filter by a specific teacher Canvas user ID (single ID): ").strip()
    teacher_ids = [int(teacher_id_input)] if teacher_id_input.isdigit() else None
    subaccounts_input = input("Filter by sub-account IDs (comma separated): ").strip()
    subaccount_ids = None
    if subaccounts_input:
        try:
            subaccount_ids = [int(x) for x in subaccounts_input.split(",") if x.strip().isdigit()]
        except Exception:
            subaccount_ids = None
    search_term = input("Search term (e.g., MATH, 1405, BIO): ").strip() or None
    only_published = input("Only published courses? (y/N): ").strip().lower() == 'y'

    # Get course sections (STAFF path by default; FACULTY path will be used when user_id is provided later)
    print(f"\nFetching course sections for {selected_term['name']}...")
    sections = get_course_sections(
        config,
        selected_term['id'],
        user_id=None,
        teacher_ids=teacher_ids,
        subaccount_ids=subaccount_ids,
        search_term=search_term,
        only_published=only_published
    )
    
    if not sections:
        print("No course sections found for the selected term.")
        return
    
    # Display sections
    display_sections_table(sections)
    
    # Main menu
    while True:
        print("\n" + "=" * 60)
        print("Cross-Listing Operations")
        print("=" * 60)
        print("1. Cross-list sections")
        print("2. Un-cross-list sections")
        print("3. Export sections to CSV")
        print("4. Refresh sections")
        print("5. Exit")
        print("-" * 60)
        
        choice = input("Enter your choice (1-5): ").strip()
        
        if choice == '1':
            # Cross-list sections
            print("\n" + "=" * 60)
            print("CROSS-LIST SECTIONS")
            print("=" * 60)
            
            # Get parent section
            parent_section = get_user_selection(sections, "Select parent section (main course)")
            if not parent_section:
                continue
            
            # Get child section
            child_section = get_user_selection(sections, "Select child section (to be cross-listed)")
            if not child_section:
                continue
            
            # Validate cross-listing
            is_valid, message = validate_cross_listing_candidates(parent_section, child_section)
            if not is_valid:
                print(f"❌ Validation failed: {message}")
                continue
            
            # Confirm cross-listing
            print(f"\nPlease confirm the cross-listing:")
            print(f"Parent: {parent_section['full_title']}")
            print(f"Child:  {child_section['full_title']}")
            
            confirm = input("\nProceed with cross-listing? (y/n): ").strip().lower()
            if confirm != 'y':
                print("Cross-listing cancelled.")
                continue
            
            # Perform cross-listing
            success = cross_list_section(config, child_section['section_id'], parent_section['course_id'])
            if success:
                print("✅ Cross-listing completed successfully!")
                # Refresh sections
                sections = get_course_sections(config, selected_term['id'])
                display_sections_table(sections)
            else:
                print("❌ Cross-listing failed. Please check the logs for details.")
        
        elif choice == '2':
            # Un-cross-list sections
            print("\n" + "=" * 60)
            print("UN-CROSS-LIST SECTIONS")
            print("=" * 60)
            
            # Filter for cross-listed sections
            cross_listed_sections = [s for s in sections if s.get('cross_listed')]
            
            if not cross_listed_sections:
                print("No cross-listed sections found.")
                continue
            
            print("Cross-listed sections:")
            for i, section in enumerate(cross_listed_sections, 1):
                print(f"{i}. {section['full_title']}")
            
            section_to_unlist = get_user_selection(cross_listed_sections, "Select section to un-cross-list")
            if not section_to_unlist:
                continue
            
            # Confirm un-cross-listing
            print(f"\nPlease confirm un-cross-listing:")
            print(f"Section: {section_to_unlist['full_title']}")
            
            confirm = input("\nProceed with un-cross-listing? (y/n): ").strip().lower()
            if confirm != 'y':
                print("Un-cross-listing cancelled.")
                continue
            
            # Perform un-cross-listing
            success = un_cross_list_section(config, section_to_unlist['section_id'])
            if success:
                print("✅ Un-cross-listing completed successfully!")
                # Refresh sections
                sections = get_course_sections(config, selected_term['id'])
                display_sections_table(sections)
            else:
                print("❌ Un-cross-listing failed. Please check the logs for details.")
        
        elif choice == '3':
            # Export to CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"crosslisting_sections_{selected_term['name'].replace(' ', '_')}_{timestamp}.csv"
            
            try:
                export_sections_to_csv(sections, filename)
                print(f"✅ Sections exported to: {filename}")
            except Exception as e:
                print(f"❌ Export failed: {e}")
        
        elif choice == '4':
            # Refresh sections (re-apply same filters)
            print("Refreshing sections...")
            sections = get_course_sections(
                config,
                selected_term['id'],
                user_id=None,
                teacher_ids=teacher_ids,
                subaccount_ids=subaccount_ids,
                search_term=search_term,
                only_published=only_published
            )
            display_sections_table(sections)
        
        elif choice == '5':
            print("Exiting...")
            break
        
        else:
            print("❌ Please enter a valid choice (1-5)")


def simple_crosslist_example():
    """Example of using the service like myCanvasInterface.CrossListSections()"""
    try:
        config = get_config()
        service = CrosslistingService(config)
        
        # Simple API call like VB.NET pattern
        success, message = service.crosslist_sections(
            child_section_id=12345,  # Replace with actual section ID
            parent_course_id=67890   # Replace with actual course ID
        )
        
        print(f"Operation result: {message}")
        return success
        
    except Exception as e:
        print(f"Service error: {e}")
        return False


if __name__ == "__main__":
    # Run interactive tool
    main()
    
    # Or use simple service interface:
    # simple_crosslist_example() 