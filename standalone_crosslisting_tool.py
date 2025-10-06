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
from typing import List, Dict, Any, Optional, Union, Generator, Tuple, Protocol
from dataclasses import dataclass
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
import re
from pathlib import Path
import tempfile

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Removed OAuth2 caching - back to simple API token auth

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TokenProvider(Protocol):
    """Protocol for providing Canvas API tokens."""
    def get_token(self) -> str:
        """Get the current API token."""
        ...


class EnvTokenProvider:
    """Token provider that reads from environment variable."""
    def __init__(self, env_var: str = 'CANVAS_API_TOKEN'):
        self.env_var = env_var

    def get_token(self) -> str:
        token = os.getenv(self.env_var)
        if not token or token == 'PLACEHOLDERAPIKEY':
            raise ValueError(f"API token not found in environment variable {self.env_var}")
        return token


# class OAuthSessionTokenProvider:
#     """Placeholder OAuth token provider - returns dummy token for now."""
#     def get_token(self) -> str:
#         # TODO: Implement OAuth flow
#         return "dummy_oauth_token"


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
    require_parent_unpublished: bool = True
    forbid_parent_with_students: bool = True
    enforce_same_subaccount: bool = False
    enforce_same_term: bool = True
    default_override_sis_stickiness: bool = True
    
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

    def __init__(self, token_provider: TokenProvider, config: CanvasConfig, as_user_id: Optional[int] = None):
        self.token_provider = token_provider
        self.config = config
        self.as_user_id = as_user_id
    
    def _rate_limit(self):
        """Implement basic rate limiting between requests."""
        # Simple delay between requests - thread-safe
        time.sleep(0.2)  # 200ms delay to be more conservative with API
    
    def _make_request(self, method: str, path: str, params: Optional[Dict] = None,
                     data: Optional[Dict] = None) -> Dict[str, Any]:
        """Make HTTP request to Canvas API with error handling."""
        self._rate_limit()

        # Parse URL
        parsed_url = urllib.parse.urlparse(self.config.base_url)
        host = parsed_url.netloc
        port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)

        # Add as_user_id parameter if set
        if params is None:
            params = {}
        if self.as_user_id:
            params['as_user_id'] = self.as_user_id

        # Build full path
        full_path = path
        if params:
            # IMPORTANT: doseq=True encodes list params correctly (include[]=a&include[]=b)
            query_string = urllib.parse.urlencode(params, doseq=True)
            # Use & if path already has ?, otherwise use ?
            separator = '&' if '?' in full_path else '?'
            full_path += separator + query_string
        
        # Create connection
        if parsed_url.scheme == 'https':
            conn = http.client.HTTPSConnection(host, port, timeout=self.config.timeout)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.config.timeout)
        
        try:
            # Set headers
            headers = {
                'Authorization': f'Bearer {self.token_provider.get_token()}',
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

        # Set pagination parameters only if not already in path
        if 'per_page' not in path:
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

            # Add page parameter - handle both embedded and separate params
            if params:
                params['page'] = page
                current_path = path
            else:
                # Path already has params embedded, append page parameter
                separator = '&' if '?' in path else '?'
                current_path = f"{path}{separator}page={page}"

            for attempt in range(self.config.max_retries):
                try:
                    logger.info(f"Fetching page {page} from {current_path if not params else path}")
                    response = self._make_request('GET', current_path if not params else path, params if params else None)
                    
                    # Handle different response formats
                    if isinstance(response, list):
                        data = response
                    elif isinstance(response, dict) and 'data' in response:
                        data = response['data']
                    else:
                        data = [response]
                    
                    if not data:
                        # No data on this page – treat as end of pagination to avoid looping on page 1
                        return all_data
                    
                    # Check for duplicate data (indicates API is returning same page repeatedly)
                    data_hash = hash(str(sorted([item.get('id', 0) for item in data if isinstance(item, dict)])))
                    if data_hash in seen_data_hashes:
                        logger.warning(f"Detected duplicate data on page {page}. Stopping pagination.")
                        return all_data
                    seen_data_hashes.add(data_hash)
                    
                    all_data.extend(data)
                    consecutive_errors = 0  # Reset error counter on success
                    
                    # Check if we have more pages
                    if len(data) < self.config.per_page:
                        # Last page (short page)
                        return all_data
                    
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
                            return all_data
                        # Give up on this page and stop pagination to avoid re-fetching the same page forever
                        return all_data
        
        logger.info(f"Retrieved {len(all_data)} total items")
        return all_data


# Cache helpers
def cache_get(key: str) -> Optional[Any]:
    """Get value from JSON cache with TTL check."""
    cache_dir = Path('./cache')
    cache_file = cache_dir / 'cache.json'

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, 'r') as f:
            cache_data = json.load(f)

        if key not in cache_data:
            return None

        entry = cache_data[key]
        if 'expires' in entry and datetime.now().timestamp() > entry['expires']:
            # Expired, remove it
            del cache_data[key]
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f)
            return None

        return entry.get('value')
    except (json.JSONDecodeError, IOError):
        return None


def cache_set(key: str, value: Any, ttl_seconds: int = 43200) -> None:
    """Set value in JSON cache with TTL using atomic writes to prevent corruption."""
    cache_dir = Path('./cache')
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / 'cache.json'

    try:
        cache_data: dict
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
            except (json.JSONDecodeError, IOError):
                # Corrupted cache; start fresh
                cache_data = {}
        else:
            cache_data = {}

        cache_data[key] = {
            'value': value,
            'expires': datetime.now().timestamp() + ttl_seconds
        }

        # Write atomically to a temp file, then replace
        with tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8', dir=str(cache_dir)) as tf:
            json.dump(cache_data, tf, indent=2)
            temp_name = tf.name
        os.replace(temp_name, cache_file)
    except Exception as e:
        logger.warning(f"Failed to write cache: {e}")


def extract_course_number(course_code: str) -> str:
    """Extract course number from course code for comparison."""
    if not course_code:
        return ""
    # Remove common prefixes and keep the numeric/alphanumeric part
    # Example: "MATH 1405" -> "1405", "BIO-101A" -> "101A"
    match = re.search(r'[A-Z]*[- ]?([0-9]+[A-Z]?)', course_code.upper())
    return match.group(1) if match else course_code


def get_course_prefix(course_code: str) -> str:
    """Extract course prefix (letters before hyphen) for comparison.

    Examples:
    - MATH-1405 -> MATH
    - ENG-1301 -> ENG
    - BIOL1405 -> BIOL (if no hyphen, extract letters before numbers)
    """
    if not course_code:
        return ""

    # First try to find hyphen
    if '-' in course_code:
        return course_code.split('-')[0].strip().upper()

    # If no hyphen, extract letters before numbers
    match = re.match(r'^([A-Za-z]+)', course_code.strip())
    return match.group(1).upper() if match else course_code.upper()


def get_section(config: CanvasConfig, token_provider: TokenProvider, section_id: int, as_user_id: Optional[int] = None) -> Dict[str, Any]:
    """Fetch a single section by id from Canvas."""
    client = CanvasAPIClient(token_provider, config, as_user_id)
    return client._make_request('GET', f'/api/v1/sections/{section_id}')


def get_course(config: CanvasConfig, token_provider: TokenProvider, course_id: int, include: Optional[List[str]] = None,
               as_user_id: Optional[int] = None) -> Dict[str, Any]:
    """Fetch a single course by id from Canvas."""
    client = CanvasAPIClient(token_provider, config, as_user_id)
    params = {"include[]": include} if include else None
    return client._make_request('GET', f'/api/v1/courses/{course_id}', params)


def update_course_fields(config: CanvasConfig, token_provider: TokenProvider, course_id: int,
                         fields: Dict[str, Any], as_user_id: Optional[int] = None) -> Dict[str, Any]:
    """Update course fields (e.g., name, syllabus_body)."""
    client = CanvasAPIClient(token_provider, config, as_user_id)
    data = {"course": fields}
    return client._make_request('PUT', f'/api/v1/courses/{course_id}', data=data)


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

    # Policy toggles
    require_parent_unpublished = os.getenv('REQUIRE_PARENT_UNPUBLISHED', 'true').lower() == 'true'
    forbid_parent_with_students = os.getenv('FORBID_PARENT_WITH_STUDENTS', 'true').lower() == 'true'
    enforce_same_subaccount = os.getenv('ENFORCE_SAME_SUBACCOUNT', 'false').lower() == 'true'
    enforce_same_term = os.getenv('ENFORCE_SAME_TERM', 'true').lower() == 'true'
    default_override_sis_stickiness = os.getenv('DEFAULT_OVERRIDE_SIS_STICKINESS', 'true').lower() == 'true'

    return CanvasConfig(
        api_token=api_token,
        base_url=base_url,
        account_id=account_id,
        per_page=per_page,
        timeout=timeout,
        max_retries=max_retries,
        requests_per_minute=requests_per_minute,
        retry_delay=retry_delay,
        require_parent_unpublished=require_parent_unpublished,
        forbid_parent_with_students=forbid_parent_with_students,
        enforce_same_subaccount=enforce_same_subaccount,
        enforce_same_term=enforce_same_term,
        default_override_sis_stickiness=default_override_sis_stickiness
    )


def resolve_instructor(config: CanvasConfig, term_id: int, user_key: str, token_provider: TokenProvider) -> Dict[str, Any]:
    """Resolve instructor by SIS id, Canvas user ID, login_id, or name.

    Resolution order (prioritizes SIS over Canvas ID):
    1. SIS ID (always attempted first, even for numeric inputs)
    2. Canvas user ID (only if SIS lookup fails and input is all digits)
    3. Name/login search (fallback)

    Returns a dict with:
      - candidates: list of resolved instructor dicts (id, name, login_id, email)
      - raw_matches: number of raw user matches before filtering by active enrollments
    """
    cache_key = f"instructor:{user_key}:{term_id}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    client = CanvasAPIClient(token_provider, config)
    candidates = []
    raw_matches = 0

    try:
        # Resolution order: SIS → Canvas ID → Name/Login

        # COMMENTED OUT: Email lookup (too many false positives)
        # if "@" in user_key:
        #     # API: GET /api/v1/accounts/{account_id}/users?login_id={user_key}
        #     # Email/login_id search
        #     path = f"/api/v1/accounts/{config.account_id}/users"
        #     params = {"login_id": user_key}
        #     try:
        #         resp = client._make_request('GET', path, params)
        #         if isinstance(resp, list) and resp:
        #             raw_matches += len(resp)
        #             candidates.extend(resp)
        #     except CanvasAPIError:
        #         # Fallback to search_term
        #         # API: GET /api/v1/accounts/{account_id}/users?search_term={user_key}
        #         params = {"search_term": user_key}
        #         resp = client._make_request('GET', path, params)
        #         if isinstance(resp, list):
        #             raw_matches += len(resp)
        #             candidates.extend(resp)
        # else:

        # Try SIS ID lookup FIRST (even for numeric inputs)
        # API: GET /api/v1/users/sis_user_id:{sis_id}
        sis_id = user_key.replace("sis:", "")  # Remove prefix if present
        path = f"/api/v1/users/sis_user_id:{sis_id}"
        sis_found = False
        try:
            resp = client._make_request('GET', path)
            if resp:
                raw_matches += 1
                candidates.append(resp)
                sis_found = True
                logger.info(f"Found user via SIS ID: {sis_id}")
        except CanvasAPIError as e:
            logger.debug(f"SIS lookup failed for '{sis_id}': {e.message}")

        # If SIS lookup failed and input is all digits, try Canvas user ID
        if not sis_found and user_key.isdigit():
            # API: GET /api/v1/users/{user_id}
            path = f"/api/v1/users/{user_key}"
            try:
                resp = client._make_request('GET', path)
                if resp:
                    raw_matches += 1
                    candidates.append(resp)
                    logger.info(f"Found user via Canvas ID: {user_key}")
            except CanvasAPIError as e:
                logger.debug(f"Canvas ID lookup failed for '{user_key}': {e.message}")

        # If no SIS or Canvas ID match, try name/login search (unless it's clearly numeric)
        if not candidates and not user_key.isdigit():
            # API: GET /api/v1/accounts/{account_id}/users?search_term={user_key}
            path = f"/api/v1/accounts/{config.account_id}/users"
            params = {"search_term": user_key}
            resp = client._make_request('GET', path, params)
            if isinstance(resp, list):
                raw_matches += len(resp)
                candidates.extend(resp)

        # Filter to teachers active in the term
        filtered_candidates = []
        for candidate in candidates:
            user_id = candidate.get('id')
            if not user_id:
                continue

            # Check teacher enrollments in term
            enroll_path = f"/api/v1/users/{user_id}/enrollments"
            enroll_params = {
                "type[]": "TeacherEnrollment",
                "state[]": "active",
                "enrollment_term_id": term_id
            }
            try:
                enrollments = client.get_paginated_data(enroll_path, enroll_params, max_pages=1)
                if enrollments:
                    filtered_candidates.append({
                        "id": candidate.get('id'),
                        "name": candidate.get('name'),
                        "login_id": candidate.get('login_id'),
                        "email": candidate.get('email') or candidate.get('primary_email')
                    })
            except CanvasAPIError:
                continue

        result = {"candidates": filtered_candidates, "raw_matches": raw_matches}
        cache_set(cache_key, result)
        return result

    except CanvasAPIError as e:
        logger.error(f"Failed to resolve instructor '{user_key}': {e.message}")
        return {"candidates": []}


def fetch_active_terms(config: CanvasConfig, token_provider: TokenProvider, use_cache: bool = True) -> List[Dict[str, Any]]:
    """Fetch active enrollment terms from Canvas API with caching."""
    cache_key = "active_terms"
    if use_cache:
        cached = cache_get(cache_key)
        if cached:
            return cached

    client = CanvasAPIClient(token_provider, config)

    try:
        # Terms endpoint returns a single object { enrollment_terms: [...] }
        path = f"/api/v1/accounts/{config.account_id}/terms"
        params = {'workflow_state[]': 'active', 'include[]': 'overrides'}
        resp = client._make_request('GET', path, params)
        terms = []
        if isinstance(resp, dict) and 'enrollment_terms' in resp:
            terms = resp['enrollment_terms']
        # Fallback: some proxies wrap in a list
        elif isinstance(resp, list) and resp and isinstance(resp[0], dict) and 'enrollment_terms' in resp[0]:
            terms = resp[0]['enrollment_terms']

        if use_cache:
            cache_set(cache_key, terms)
        return terms

    except CanvasAPIError as e:
        logger.error(f"Failed to fetch terms: {e.message}")
        return []


def list_account_courses_filtered(
    config: CanvasConfig,
    token_provider: TokenProvider,
    term_id: int,
    teacher_ids: Optional[list[int]] = None,
    subaccount_ids: Optional[list[int]] = None,
    search_term: Optional[str] = None,
    only_published: bool = False,
    states: Optional[list[str]] = None,
    staff_max_pages: int = 5
) -> list[dict]:
    """
    STAFF NARROWING: Use account-level filters so we don't load the whole term.
    Server-side filters supported by Canvas: enrollment_term_id, by_teachers[], by_subaccounts[],
    search_term, published, state[], include[] (teachers, term, account_name).
    """
    if not search_term:
        raise ValueError("Staff mode requires a search_term")

    client = CanvasAPIClient(token_provider, config)
    path = f"/api/v1/accounts/{config.account_id}/courses"
    params: dict = {
        "enrollment_term_id": term_id,
        "with_enrollments": "true",
        # Do not request sections at the account endpoint; keep term/account_name
        "include[]": ["term", "account_name"],
        "per_page": config.per_page
    }
    # Prefer state[] semantics; include unpublished + available when browsing
    # SOP requires allowing unpublished (Canvas "created") courses to appear initially.
    if only_published:
        effective_states = ["available"]
    else:
        # Include both unpublished (created) and available so potential parents are not filtered out upfront
        effective_states = states if states else ["unpublished", "available"]
    for st in effective_states:
        params.setdefault("state[]", []).append(st)
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
    return client.get_paginated_data(path, params, max_pages=staff_max_pages)

def get_user_courses(config: CanvasConfig, token_provider: TokenProvider, user_id: int, term_id: Optional[int] = None) -> list[dict]:
    """
    Get user's courses using GET /api/v1/users/{user_id}/courses.
    Optionally filter by term_id if provided.

    Args:
        config: Canvas configuration
        token_provider: Token provider for authentication
        user_id: Canvas user ID
        term_id: Optional enrollment term ID to filter courses

    Returns:
        List of course objects with term, teachers, sections, and total_students included
    """
    client = CanvasAPIClient(token_provider, config)

    # Build the correct Canvas API path: GET /api/v1/users/{user_id}/courses
    # Include term, teachers, sections, and total_students data
    courses_path = f"/api/v1/users/{user_id}/courses?include%5B%5D=term&include%5B%5D=teachers&include%5B%5D=sections&include%5B%5D=total_students&per_page={config.per_page}"

    logger.info(f"Fetching courses for user {user_id}")

    # Get all courses for this user (paginated)
    all_courses = client.get_paginated_data(courses_path, None, max_pages=10)

    if not all_courses:
        logger.info("No courses returned for user")
        return []

    logger.info(f"Found {len(all_courses)} total courses for user {user_id}")

    # Filter out truly orphaned courses (courses where all sections belong to other courses)
    # This happens after cross-listing when sections are moved to another course
    active_courses = []
    for course in all_courses:
        course_id = course.get('id')
        sections = course.get('sections', [])

        # Skip courses with no sections at all
        if not sections:
            logger.debug(f"Filtering out course {course_id} - no sections")
            continue

        # Check if ALL sections are explicitly cross-listed to OTHER courses
        # A section is cross-listed elsewhere if:
        # 1. It has a nonxlist_course_id (meaning it was moved)
        # 2. The nonxlist_course_id equals THIS course_id (meaning it originated here)
        # 3. The section's course_id is different from this course (meaning it now belongs elsewhere)
        has_own_sections = False
        for section in sections:
            section_course_id = section.get('course_id')
            nonxlist_course_id = section.get('nonxlist_course_id')

            # If course_id is None/missing, treat section as belonging to parent course
            if section_course_id is None:
                section_course_id = course_id

            # Section belongs here if: no cross-listing OR cross-listed TO here
            belongs_here = (
                (nonxlist_course_id is None) or  # Not cross-listed at all
                (section_course_id == course_id)  # Cross-listed TO this course
            )

            if belongs_here:
                has_own_sections = True
                break

        if has_own_sections:
            # Keep the course - it has at least one section that belongs to it
            active_courses.append(course)
        else:
            logger.debug(f"Filtering out orphaned course {course_id} '{course.get('name')}' - "
                        f"all {len(sections)} sections belong to other courses")

    logger.info(f"After orphan filtering: {len(active_courses)} active courses")

    # Filter by enrollment_term_id if term_id is provided
    if term_id is not None:
        term_courses = [
            course for course in active_courses
            if course.get('enrollment_term_id') == term_id
        ]
        logger.info(f"Filtered to {len(term_courses)} courses in term {term_id}")
        return term_courses

    return active_courses


# Keep old function name for backward compatibility, but redirect to new implementation
def list_user_term_courses_via_enrollments(config: CanvasConfig, token_provider: TokenProvider, user_id: int, term_id: int) -> list[dict]:
    """Deprecated: Use get_user_courses() instead."""
    return get_user_courses(config, token_provider, user_id, term_id)

def list_sections_for_courses(config: CanvasConfig, token_provider: TokenProvider, courses: list[dict]) -> list[dict]:
    """Fetch sections only for the narrowed set of courses, preferring course['sections'] when present."""
    client = CanvasAPIClient(token_provider, config)
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

        # Hydrate teachers/total_students if not present on the course object
        teachers_for_course = course.get("teachers")
        total_students_for_course = course.get("total_students")
        if not teachers_for_course or total_students_for_course is None:
            try:
                course_resp = client._make_request(
                    "GET",
                    f"/api/v1/courses/{cid}",
                    params={"include[]": ["teachers", "total_students"]}
                )
                teachers_for_course = course_resp.get("teachers", teachers_for_course or [])
                total_students_for_course = course_resp.get("total_students", total_students_for_course or 0)
            except CanvasAPIError:
                teachers_for_course = teachers_for_course or []
                total_students_for_course = total_students_for_course or 0

        # Always fetch sections from the course endpoint
        sections_data = client.get_paginated_data(f"/api/v1/courses/{cid}/sections", {"per_page": config.per_page})

        for s in sections_data or []:
            # Standardize cross-list detection per sections API fields
            cross_listed = bool(s.get("cross_listing_id")) or (
                s.get("nonxlist_course_id") is not None and s.get("nonxlist_course_id") != s.get("course_id")
            )

            section_data = {
                "section_id": s.get("id"),
                "section_name": s.get("name"),
                "course_id": cid,
                "course_name": course.get("name"),
                "course_code": course.get("course_code"),
                "enrollment_term_id": course.get("enrollment_term_id"),
                "sis_course_id": course.get("sis_course_id"),
                "sis_section_id": s.get("sis_section_id"),
                "workflow_state": course.get("workflow_state"),
                "published": course.get("workflow_state") == "available",
                "teachers": teachers_for_course or [],
                "cross_listed": cross_listed,
                "parent_course_id": s.get("parent_course_id"),
                "total_students": total_students_for_course or 0,
                "subaccount_id": course.get("account_id"),
                "full_title": f"{course.get('course_code')}: {course.get('name')}: Section {s.get('name')}"
            }
            out.append(section_data)

    # Sort deterministically: course_code, section_name, course_id, section_id
    def sort_key(section):
        course_code = section.get('course_code', '')
        section_name = section.get('section_name', '')
        course_id = section.get('course_id', 0)
        section_id = section.get('section_id', 0)
        # Natural sort for course code
        return (course_code, section_name, course_id, section_id)

    out.sort(key=sort_key)
    return out

def check_course_permissions(config: CanvasConfig, token_provider: TokenProvider, course_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Check permissions for potential parent courses."""
    permissions_map = {}

    def check_single_course(course_id: int) -> Tuple[int, Dict[str, Any]]:
        try:
            # Create a separate client for each thread to avoid shared state issues
            thread_client = CanvasAPIClient(token_provider, config)
            resp = thread_client._make_request(
                'GET',
                f'/api/v1/courses/{course_id}',
                params={'include[]': ['permissions']}
            )
            permissions = resp.get('permissions', {})
            can_crosslist = permissions.get('manage_courses', False) or permissions.get('manage_sections', False)
            return course_id, {
                'can_crosslist': can_crosslist,
                'reason': '' if can_crosslist else 'Insufficient permissions to manage courses/sections'
            }
        except CanvasAPIError as e:
            return course_id, {
                'can_crosslist': False,
                'reason': f'Permission check failed: {e.message}'
            }

    # Check permissions in parallel with reduced worker count and timeout
    if course_ids:
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_course = {executor.submit(check_single_course, cid): cid for cid in course_ids}
            for future in future_to_course:
                try:
                    course_id, permission_info = future.result(timeout=15)
                    permissions_map[course_id] = permission_info
                except Exception as e:
                    logger.warning(f"Failed to check permissions: {e}")
                    # Add default deny permissions for failed checks
                    cid = future_to_course.get(future)
                    if cid:
                        permissions_map[cid] = {
                            'can_crosslist': False,
                            'reason': 'Permission check timed out'
                        }

    return permissions_map


def get_course_sections(
    config: CanvasConfig,
    token_provider: TokenProvider,
    term_id: int,
    user_id: Optional[int] = None,
    teacher_ids: Optional[list[int]] = None,
    subaccount_ids: Optional[list[int]] = None,
    search_term: Optional[str] = None,
    only_published: bool = False,
    staff_max_pages: int = 5
) -> List[Dict[str, Any]]:
    """Get course sections for a term with robust narrowing."""
    try:
        print(f"🔍 Fetching course sections for term {term_id}...")
        if user_id:
            # Faculty path: Get user's courses and filter by term
            courses = get_user_courses(config, token_provider, user_id, term_id)
        else:
            # Staff narrowing path (account-level filters)
            courses = list_account_courses_filtered(
                config,
                token_provider,
                term_id,
                teacher_ids=teacher_ids,
                subaccount_ids=subaccount_ids,
                search_term=search_term,
                only_published=only_published,
                staff_max_pages=staff_max_pages
            )
        sections = list_sections_for_courses(config, token_provider, courses)
        print(f"✅ Found {len(sections)} course sections (after narrowing)")
        return sections
    except CanvasAPIError as e:
        logger.error(f"Failed to fetch course sections: {e.message}")
        return []


def validate_cross_listing_candidates(config: CanvasConfig, parent_section: Dict[str, Any], child_section: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """
    Validate if two sections can be cross-listed according to policy rules.

    Returns:
        Tuple of (errors, warnings) where:
        - errors: List of blocking issues that prevent cross-listing
        - warnings: List of issues that should show modal confirmation but allow proceeding
    """
    errors = []
    warnings = []

    # HARD ERRORS (blocking)

    # Check if sections are already cross-listed
    if parent_section.get('cross_listed'):
        errors.append("Parent section is already cross-listed")

    if child_section.get('cross_listed'):
        errors.append("Child section is already cross-listed")

    # Check if sections are in the same course
    if parent_section['course_id'] == child_section['course_id']:
        errors.append("Cannot cross-list sections from the same course")

    # Parent must be unpublished (blocking error)
    if config.require_parent_unpublished:
        if parent_section.get('published'):
            errors.append("Parent must be unpublished")

    # Child must be published (blocking error)
    if not child_section.get('published'):
        errors.append("Child course must be published")

    # Same term required (blocking error)
    if config.enforce_same_term:
        parent_term = parent_section.get('enrollment_term_id')
        child_term = child_section.get('enrollment_term_id')
        if parent_term is not None and child_term is not None and parent_term != child_term:
            errors.append("Parent and child must be in the same enrollment term")

    # WARNINGS (modal confirmation)

    # Parent cannot have students if published (warning)
    if config.forbid_parent_with_students:
        if (parent_section.get('total_students', 0) > 0) and parent_section.get('published'):
            warnings.append("Parent is published and has student activity")

    # Teachers must match (warning)
    parent_teachers = parent_section.get('teachers') or []
    child_teachers = child_section.get('teachers') or []
    parent_teacher_ids = {t.get('id') for t in parent_teachers if isinstance(t, dict) and t.get('id')}
    child_teacher_ids = {t.get('id') for t in child_teachers if isinstance(t, dict) and t.get('id')}
    if parent_teacher_ids and child_teacher_ids and parent_teacher_ids.isdisjoint(child_teacher_ids):
        warnings.append("Teachers do not match between parent and child courses")

    # Same subaccount check (warning)
    if config.enforce_same_subaccount:
        parent_subaccount = parent_section.get('subaccount_id')
        child_subaccount = child_section.get('subaccount_id')
        if parent_subaccount != child_subaccount:
            warnings.append(f"Subaccounts don't match: {parent_subaccount} vs {child_subaccount}")

    # Course name mismatch check (warning if different prefixes)
    parent_code = parent_section.get('course_code', '')
    child_code = child_section.get('course_code', '')
    parent_prefix = get_course_prefix(parent_code)
    child_prefix = get_course_prefix(child_code)

    if parent_prefix and child_prefix and parent_prefix != child_prefix:
        warnings.append(f"Course name mismatch: {parent_section.get('course_name', '')} vs {child_section.get('course_name', '')}")

    return errors, warnings


def log_audit_action(actor_as_user_id: Optional[int], term_id: int, instructor_id: Optional[int],
                    action: str, parent_course_id: Optional[int], child_section_id: Optional[int],
                    result: str, dry_run: bool, message: str,
                    new_parent_course_title: Optional[str] = None,
                    child_section_ids: Optional[List[int]] = None,
                    syllabus_updated: Optional[bool] = None) -> None:
    """Log action to audit CSV."""
    audit_dir = Path('./logs')
    audit_dir.mkdir(exist_ok=True)
    audit_file = audit_dir / 'crosslist_audit.csv'

    # Check if file exists and has headers
    file_exists = audit_file.exists()

    try:
        with open(audit_file, 'a', newline='', encoding='utf-8') as f:
            fieldnames = ['timestamp', 'actor_as_user_id', 'term_id', 'instructor_id', 'action',
                         'parent_course_id', 'child_section_id', 'result', 'dry_run', 'message',
                         'new_parent_course_title', 'child_section_ids', 'syllabus_updated']
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            if not file_exists:
                writer.writeheader()

            writer.writerow({
                'timestamp': datetime.now().isoformat(),
                'actor_as_user_id': actor_as_user_id or '',
                'term_id': term_id,
                'instructor_id': instructor_id or '',
                'action': action,
                'parent_course_id': parent_course_id or '',
                'child_section_id': child_section_id or '',
                'result': result,
                'dry_run': 'Yes' if dry_run else 'No',
                'message': message,
                'new_parent_course_title': new_parent_course_title or '',
                'child_section_ids': ",".join(str(i) for i in (child_section_ids or [])),
                'syllabus_updated': '' if syllabus_updated is None else ('Yes' if syllabus_updated else 'No')
            })
    except IOError as e:
        logger.warning(f"Failed to write audit log: {e}")


def cross_list_section(config: CanvasConfig, token_provider: TokenProvider, child_section_id: int, parent_course_id: int,
                      dry_run: bool = False, term_id: Optional[int] = None, instructor_id: Optional[int] = None,
                      as_user_id: Optional[int] = None, override_sis_stickiness: Optional[bool] = None) -> bool:
    """Cross-list a child section into a parent course."""
    action = "cross_list"

    # Pre-move guard: fetch authoritative section details
    try:
        pre_section = get_section(config, token_provider, child_section_id, as_user_id)
    except CanvasAPIError as e:
        message = f"Failed to fetch section before cross-list: {e.message}"
        logger.error(message)
        log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "error", dry_run, message)
        return False

    current_course_id = pre_section.get('course_id')
    original_course_id = pre_section.get('nonxlist_course_id')

    # Enforce same-term safety if configured (fetch child and parent course terms)
    try:
        parent_course = get_course(config, token_provider, parent_course_id, include=["total_students", "teachers"], as_user_id=as_user_id)
        child_course = get_course(config, token_provider, current_course_id, include=["total_students", "teachers"], as_user_id=as_user_id)
        parent_term_id = parent_course.get('enrollment_term_id')
        child_term_id = child_course.get('enrollment_term_id')

        # Removed sandbox mode logic - no longer needed

        # Helper to determine published state for SOP checks
        def _is_published(course: Dict[str, Any]) -> bool:
            return (course.get('workflow_state') == 'available') or bool(course.get('published'))

        parent_is_published = _is_published(parent_course)
        parent_has_students = (parent_course.get('total_students') or 0) > 0
        child_is_published = _is_published(child_course)

        # Same term strict check (blocking error)
        if config.enforce_same_term and parent_term_id is not None and child_term_id is not None and parent_term_id != child_term_id:
            message = (
                f"Term mismatch: parent course term {parent_term_id} vs child course term {child_term_id}. "
                f"Cross-listing blocked."
            )
            logger.error(message)
            log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "error", dry_run, message)
            return False

    except CanvasAPIError as e:
        message = f"Failed to fetch course details for term check: {e.message}"
        logger.error(message)
        log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "error", dry_run, message)
        return False

    # No-op if already in the target parent course
    if current_course_id == parent_course_id:
        message = f"Section {child_section_id} already belongs to course {parent_course_id} (no-op)"
        logger.info(message)
        log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "success", dry_run, message)
        return True

    # If already cross-listed, surface clearer message
    if original_course_id is not None and original_course_id != current_course_id:
        message = (
            f"Section {child_section_id} is already cross-listed (original course {original_course_id}, current {current_course_id})."
        )
        logger.error(message)
        log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "error", dry_run, message)
        return False

    if dry_run:
        message = f"DRY RUN: Would cross-list section {child_section_id} into course {parent_course_id}"
        print(f"🔄 {message}")
        log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "success", True, message)
        return True

    client = CanvasAPIClient(token_provider, config, as_user_id)

    try:
        path = f"/api/v1/sections/{child_section_id}/crosslist/{parent_course_id}"
        effective_override = config.default_override_sis_stickiness if override_sis_stickiness is None else override_sis_stickiness
        params = {"override_sis_stickiness": str(effective_override).lower()} if effective_override else None

        print(f"🔄 Cross-listing section {child_section_id} into course {parent_course_id}...")
        _ = client._make_request('POST', path, params=params)

        # Post-move verification
        post_section = get_section(config, token_provider, child_section_id, as_user_id)
        if post_section.get('course_id') == parent_course_id:
            # Apply post-success updates: rename course per Option C and update syllabus child listing
            try:
                updates = apply_post_crosslist_updates(config, token_provider, parent_course_id, as_user_id)
            except Exception as _:
                updates = {"new_course_name": None, "child_section_ids": [], "syllabus_updated": False}

            message = f"Successfully cross-listed section {child_section_id} into course {parent_course_id}"
            if updates.get('new_course_name'):
                message += f"; new course name: {updates['new_course_name']}"
            print(f"✅ {message}")
            log_audit_action(
                as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id,
                "success", False, message,
                new_parent_course_title=updates.get('new_course_name'),
                child_section_ids=updates.get('child_section_ids') or [],
                syllabus_updated=updates.get('syllabus_updated')
            )
            return True
        else:
            message = (
                f"Post-verification failed: section {child_section_id} course_id is {post_section.get('course_id')} not {parent_course_id}"
            )
            logger.error(message)
            log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "error", False, message)
            return False

    except CanvasAPIError as e:
        message = f"Failed to cross-list section: {e.message}"
        logger.error(message)
        log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "error", False, message)
        return False


def _extract_section_suffix(sis_section_id: Optional[str], section_name: Optional[str]) -> str:
    """Extract section suffix using preferred identifiers.
    - Prefer sis_section_id when available
    - Otherwise parse from section name
    - Support alphanumeric endings (e.g., A, 007); normalize to upper-case
    """
    # Helper to normalize and extract trailing alphanumeric group up to 5 chars
    def _extract_from_text(text: str) -> Optional[str]:
        if not text:
            return None
        text_u = str(text).upper().strip()
        # Try common separators first (last token after non-alnum separators)
        m = re.search(r'[^0-9A-Z]([0-9A-Z]{1,5})$', text_u)
        if m:
            return m.group(1)
        # Fallback: pure trailing alnum
        m2 = re.search(r'([0-9A-Z]{1,5})$', text_u)
        return m2.group(1) if m2 else None

    # Prefer SIS section id
    suffix = _extract_from_text(sis_section_id or '') if sis_section_id else None
    if suffix:
        return suffix
    # Fallback to section name
    suffix = _extract_from_text(section_name or '') if section_name else None
    return suffix or ''


def build_simple_crosslist_name(parent_code: str, parent_name: str, child_code: str, child_name: str) -> str:
    """
    Build simple cross-list name format with codes and names.

    Args:
        parent_code: Parent course code (e.g., "ENGL 1301-001")
        parent_name: Parent course name (e.g., "English Composition")
        child_code: Child course code (e.g., "ENGL 1301-005")
        child_name: Child course name (e.g., "English Composition")

    Returns:
        String in format: "ENGL 1301-001: English Composition and ENGL 1301-005: English Composition"
    """
    return f"{parent_code}: {parent_name} and {child_code}: {child_name}"


def _build_children_html_list(children: List[Tuple[str, str]]) -> str:
    """Build the delimited block with <ul> for child courses. children: list of (course_code, name).
    The returned string includes only the markers and the <ul> content, per requirements.
    """
    items = "\n".join([f"  <li>Child Course – {code}: {name}</li>" for code, name in children])
    return (
        "<!-- CROSSLIST_CHILDREN -->\n"
        f"<ul>\n{items}\n</ul>\n"
        "<!-- END_CROSSLIST_CHILDREN -->"
    )


def update_course_code_field(config: CanvasConfig, token_provider: TokenProvider, parent_course_id: int,
                            parent_code: str, child_code: str, as_user_id: Optional[int] = None) -> bool:
    """
    Update the Course Code field (not Description) with cross-listing info.

    Logic:
    - Same course (ENGL 1301-001 + ENGL 1301-005): "ENGL 1301-001 / 005"
    - Different courses (MATH 1405-001 + PHYS 2301-005): "MATH 1405-001 / PHYS 2301-005"

    Args:
        config: Canvas configuration
        token_provider: Authentication provider
        parent_course_id: ID of parent course
        parent_code: Parent course code (e.g., "ENGL 1301-001")
        child_code: Child course code (e.g., "ENGL 1301-005")
        as_user_id: Optional user ID for masquerading

    Returns:
        Boolean indicating success
    """
    try:
        # Extract base course codes (before the last dash/section number)
        # Format: "SUBJ ####-###" where last part after dash is section
        parent_base = parent_code.rsplit('-', 1)[0] if '-' in parent_code else parent_code
        child_base = child_code.rsplit('-', 1)[0] if '-' in child_code else child_code

        # Check if same course (same base)
        if parent_base == child_base:
            # Same course - extract just the section number from child
            child_section = child_code.rsplit('-', 1)[1] if '-' in child_code else child_code
            new_code = f"{parent_code} / {child_section}"
        else:
            # Different courses - use full child code
            new_code = f"{parent_code} / {child_code}"

        # Update ONLY the course code field
        update_course_fields(config, token_provider, parent_course_id, {"course_code": new_code}, as_user_id)

        logger.info(f"Updated course code to: {new_code}")
        return True

    except CanvasAPIError as e:
        logger.error(f"Failed to update course code: {e.message}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error updating course code: {e}")
        return False


def apply_post_crosslist_updates(config: CanvasConfig, token_provider: TokenProvider, parent_course_id: int,
                                 as_user_id: Optional[int] = None,
                                 primary_parent_suffix: Optional[str] = None) -> Dict[str, Any]:
    """
    After a successful cross-list, update parent course with simple naming and course code.

    Updates:
    1. Course name using simple format: "Parent Course Name and Child Course Name"
    2. Course Code field with section or course info (NOT Description field)
    3. Syllabus with child course list

    Returns dict with new_course_name, child_section_ids, syllabus_updated, course_code_updated.
    """
    client = CanvasAPIClient(token_provider, config, as_user_id)

    # Fetch parent course details
    parent_course = get_course(config, token_provider, parent_course_id, include=["syllabus_body"], as_user_id=as_user_id)
    parent_course_name = parent_course.get('name') or ''
    parent_course_code = parent_course.get('course_code') or ''
    current_syllabus = parent_course.get('syllabus_body') or ''

    # Fetch all sections currently in the parent course
    sections = client.get_paginated_data(f"/api/v1/courses/{parent_course_id}/sections", {"per_page": config.per_page})

    child_sections: List[Dict[str, Any]] = []
    child_section_ids: List[int] = []
    child_origin_course_ids: List[int] = []

    for s in sections or []:
        nonx = s.get('nonxlist_course_id')
        if nonx is not None and nonx != parent_course_id:
            child_sections.append(s)
            child_section_ids.append(s.get('id'))
            if nonx:
                child_origin_course_ids.append(nonx)

    # Fetch child origin course details
    children_display: List[Tuple[str, str]] = []
    new_course_name = parent_course_name
    course_code_updated = False

    # Process first child course (for naming and course code)
    if child_origin_course_ids:
        first_child_id = sorted(set(child_origin_course_ids))[0]
        try:
            child_course = get_course(config, token_provider, first_child_id, include=None, as_user_id=as_user_id)
            child_name = child_course.get('name') or ''
            child_code = child_course.get('course_code') or ''

            # Build simple course name: "ENGL 1301-001: English Composition and ENGL 1301-005: English Composition"
            new_course_name = build_simple_crosslist_name(parent_course_code, parent_course_name,
                                                         child_code, child_name)

            # Update course name if changed
            if new_course_name != parent_course_name:
                update_course_fields(config, token_provider, parent_course_id, {"name": new_course_name}, as_user_id)
                logger.info(f"Updated course name to: {new_course_name}")

            # Update course code field: "ENGL 1301-001 / ENGL 1301-005"
            course_code_updated = update_course_code_field(config, token_provider, parent_course_id,
                                                          parent_course_code, child_code, as_user_id)

            children_display.append((child_code, child_name))

        except CanvasAPIError as e:
            logger.error(f"Failed to fetch child course {first_child_id}: {e.message}")

        # Collect remaining child courses for syllabus
        for ocid in sorted(set(child_origin_course_ids)):
            if ocid != first_child_id:
                try:
                    child_course = get_course(config, token_provider, ocid, include=None, as_user_id=as_user_id)
                    child_code = child_course.get('course_code') or ''
                    child_name = child_course.get('name') or ''
                    children_display.append((child_code, child_name))
                except CanvasAPIError:
                    continue

    # Update syllabus with child course list
    html_block = _build_children_html_list(children_display)
    header_block = "<hr>\n<h3>Cross-listed Child Courses</h3>\n"

    start_marker = "<!-- CROSSLIST_CHILDREN -->"
    end_marker = "<!-- END_CROSSLIST_CHILDREN -->"
    syllabus_updated = False

    if start_marker in current_syllabus and end_marker in current_syllabus:
        pattern = re.compile(r"<!-- CROSSLIST_CHILDREN -->[\s\S]*?<!-- END_CROSSLIST_CHILDREN -->", re.MULTILINE)
        new_syllabus = pattern.sub(html_block, current_syllabus)
        if new_syllabus != current_syllabus:
            update_course_fields(config, token_provider, parent_course_id, {"syllabus_body": new_syllabus}, as_user_id)
            syllabus_updated = True
    else:
        sep = "\n\n" if current_syllabus and not current_syllabus.endswith("\n") else "\n"
        new_syllabus = (current_syllabus or '') + sep + header_block + html_block
        update_course_fields(config, token_provider, parent_course_id, {"syllabus_body": new_syllabus}, as_user_id)
        syllabus_updated = True

    return {
        "new_course_name": new_course_name,
        "child_section_ids": child_section_ids,
        "syllabus_updated": syllabus_updated,
        "course_code_updated": course_code_updated
    }


def summarize_crosslist_changes(config: CanvasConfig, token_provider: TokenProvider, parent_course_id: int,
                                as_user_id: Optional[int] = None) -> Dict[str, Any]:
    """Fetch current parent course name and a list of child courses for GUI display (no updates)."""
    client = CanvasAPIClient(token_provider, config, as_user_id)
    parent_course = get_course(config, token_provider, parent_course_id, include=None, as_user_id=as_user_id)
    sections = client.get_paginated_data(f"/api/v1/courses/{parent_course_id}/sections", {"per_page": config.per_page})
    child_origin_course_ids: List[int] = []
    for s in sections or []:
        nonx = s.get('nonxlist_course_id')
        if nonx is not None and nonx != parent_course_id:
            child_origin_course_ids.append(nonx)
    children_display: List[Tuple[str, str]] = []
    for ocid in sorted({cid for cid in child_origin_course_ids if cid}):
        try:
            child_course = get_course(config, token_provider, ocid, include=None, as_user_id=as_user_id)
            code = child_course.get('course_code') or ''
            name = child_course.get('name') or ''
            children_display.append((code, name))
        except CanvasAPIError:
            continue
    return {
        "parent_course_name": parent_course.get('name') or '',
        "children": children_display
    }


def un_cross_list_section(config: CanvasConfig, token_provider: TokenProvider, section_id: int,
                         dry_run: bool = False, term_id: Optional[int] = None, instructor_id: Optional[int] = None,
                         as_user_id: Optional[int] = None, override_sis_stickiness: bool = True) -> bool:
    """Un-cross-list a section (remove it from cross-listing)."""
    action = "un_cross_list"

    # Pre-undo details
    try:
        pre_section = get_section(config, token_provider, section_id, as_user_id)
    except CanvasAPIError as e:
        message = f"Failed to fetch section before un-cross-list: {e.message}"
        logger.error(message)
        log_audit_action(as_user_id, term_id or 0, instructor_id, action, None, section_id, "error", dry_run, message)
        return False

    pre_course_id = pre_section.get('course_id')
    pre_nonx = pre_section.get('nonxlist_course_id')

    # If not cross-listed (no nonxlist_course_id), nothing to undo
    if pre_nonx is None:
        message = f"Section {section_id} is not cross-listed (no-op)"
        logger.info(message)
        log_audit_action(as_user_id, term_id or 0, instructor_id, action, None, section_id, "success", dry_run, message)
        return True

    if dry_run:
        message = f"DRY RUN: Would un-cross-list section {section_id}"
        print(f"🔄 {message}")
        log_audit_action(as_user_id, term_id or 0, instructor_id, action, None, section_id, "success", True, message)
        return True

    client = CanvasAPIClient(token_provider, config, as_user_id)

    try:
        path = f"/api/v1/sections/{section_id}/crosslist"
        params = {"override_sis_stickiness": str(override_sis_stickiness).lower()} if override_sis_stickiness else None

        print(f"🔄 Un-cross-listing section {section_id}...")
        _ = client._make_request('DELETE', path, params=params)

        # Post-undo verification
        post_section = get_section(config, token_provider, section_id, as_user_id)
        post_course_id = post_section.get('course_id')
        if (pre_nonx is not None and post_course_id == pre_nonx) or (post_course_id != pre_course_id):
            message = f"Successfully un-cross-listed section {section_id}"
            print(f"✅ {message}")
            log_audit_action(as_user_id, term_id or 0, instructor_id, action, None, section_id, "success", False, message)
            return True
        else:
            message = (
                f"Post-verification failed: section {section_id} course_id is {post_course_id} (expected revert to {pre_nonx} or change from {pre_course_id})"
            )
            logger.error(message)
            log_audit_action(as_user_id, term_id or 0, instructor_id, action, None, section_id, "error", False, message)
            return False

    except CanvasAPIError as e:
        message = f"Failed to un-cross-list section: {e.message}"
        logger.error(message)
        log_audit_action(as_user_id, term_id or 0, instructor_id, action, None, section_id, "error", False, message)
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


def format_sections_for_ui(sections: List[Dict[str, Any]], permissions_map: Optional[Dict[int, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Format sections for UI consumption."""
    ui_rows = []

    for section in sections:
        course_id = section.get('course_id')
        published = section.get('published', False)
        cross_listed = section.get('cross_listed', False)

        # Determine parent/child candidate status
        # SOP: A parent is valid if it is unpublished OR has zero students (even if published), and not already cross-listed.
        parent_candidate = ((not published) or (section.get('total_students', 0) == 0)) and not cross_listed
        child_candidate = published and not cross_listed

        # Check permission block
        permission_block = None
        if permissions_map and course_id in permissions_map:
            perm_info = permissions_map[course_id]
            if not perm_info.get('can_crosslist', True):
                permission_block = perm_info.get('reason', 'Permission denied')

        ui_row = {
            'parent_candidate': parent_candidate,
            'child_candidate': child_candidate,
            'course': f"{section.get('course_code', '')}: {section.get('course_name', '')}",
            'published': "Yes" if published else "No",
            'cross_listed': "Yes" if cross_listed else "No",
            'undo_allowed': cross_listed,
            'ids': {
                'course_id': course_id,
                'section_id': section.get('section_id')
            },
            'permission_block': permission_block
        }
        ui_rows.append(ui_row)

    return ui_rows


def export_sections_to_csv(sections: List[Dict[str, Any]], term_info: Optional[Dict[str, Any]] = None, filename: str = 'sections_export.csv') -> None:
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
            fieldnames = ['term_id', 'term_name', 'instructor_id', 'instructor_login', 'course_id', 'course_code',
                         'course_name', 'section_id', 'section_name', 'published', 'cross_listed', 'parent_course_id',
                         'sis_course_id', 'sis_section_id', 'subaccount_id']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for section in sections:
                # Extract instructor info from teachers
                teachers = section.get('teachers', [])
                instructor_id = teachers[0].get('id', '') if teachers else ''
                instructor_login = teachers[0].get('display_name', '') if teachers else ''

                writer.writerow({
                    'term_id': term_info.get('id', '') if term_info else '',
                    'term_name': term_info.get('name', '') if term_info else '',
                    'instructor_id': instructor_id,
                    'instructor_login': instructor_login,
                    'course_id': section.get('course_id', ''),
                    'course_code': section.get('course_code', ''),
                    'course_name': section.get('course_name', ''),
                    'section_id': section.get('section_id', ''),
                    'section_name': section.get('section_name', ''),
                    'published': 'Yes' if section.get('published') else 'No',
                    'cross_listed': 'Yes' if section.get('cross_listed') else 'No',
                    'parent_course_id': section.get('parent_course_id', ''),
                    'sis_course_id': section.get('sis_course_id', ''),
                    'sis_section_id': section.get('sis_section_id', ''),
                    'subaccount_id': section.get('subaccount_id', '')
                })
        
        logger.info(f"Exported {len(sections)} sections to {filename}")
        
    except Exception as e:
        logger.error(f"Failed to export CSV: {e}")
        raise


class CrosslistingService:
    """Simple service interface for crosslisting operations - similar to VB.NET pattern"""

    def __init__(self, config: CanvasConfig, token_provider: TokenProvider, as_user_id: Optional[int] = None):
        self.config = config
        self.token_provider = token_provider
        self.as_user_id = as_user_id
        self.client = CanvasAPIClient(token_provider, config, as_user_id)
    
    def crosslist_sections(self, child_section_id: int, parent_course_id: int, dry_run: bool = False,
                          term_id: Optional[int] = None, instructor_id: Optional[int] = None,
                          override_sis_stickiness: bool = True) -> Tuple[bool, str]:
        """
        Simple interface for crosslisting - similar to myCanvasInterface.CrossListSections()

        Args:
            child_section_id: Section to be cross-listed
            parent_course_id: Course to cross-list into
            dry_run: If True, only log the intended action
            term_id: Term ID for audit logging
            instructor_id: Instructor ID for audit logging

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            success = cross_list_section(self.config, self.token_provider, child_section_id, parent_course_id,
                                       dry_run, term_id, instructor_id, self.as_user_id, override_sis_stickiness)
            if success:
                action = "DRY RUN: Would cross-list" if dry_run else "Successfully cross-listed"
                return True, f"{action} section {child_section_id} into course {parent_course_id}"
            else:
                return False, "Cross-listing operation failed"
        except Exception as e:
            return False, f"Error during cross-listing: {str(e)}"
    
    def uncrosslist_section(self, section_id: int, dry_run: bool = False,
                           term_id: Optional[int] = None, instructor_id: Optional[int] = None,
                           override_sis_stickiness: bool = True) -> Tuple[bool, str]:
        """
        Simple interface for un-crosslisting

        Args:
            section_id: Section to un-crosslist
            dry_run: If True, only log the intended action
            term_id: Term ID for audit logging
            instructor_id: Instructor ID for audit logging

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            success = un_cross_list_section(self.config, self.token_provider, section_id,
                                          dry_run, term_id, instructor_id, self.as_user_id, override_sis_stickiness)
            if success:
                action = "DRY RUN: Would un-cross-list" if dry_run else "Successfully un-cross-listed"
                return True, f"{action} section {section_id}"
            else:
                return False, "Un-cross-listing operation failed"
        except Exception as e:
            return False, f"Error during un-cross-listing: {str(e)}"


def main():
    """Main function to run the instructor-first cross-listing tool."""
    import argparse
    import signal
    import sys

    def signal_handler(signum, frame):
        """Handle shutdown signals gracefully."""
        print(f"\nReceived interrupt signal, shutting down gracefully...")
        sys.exit(0)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination signal

    # On Windows, also handle Ctrl+Break
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal_handler)

    parser = argparse.ArgumentParser(description='Canvas Cross-Listing Tool')
    parser.add_argument('--no_cache', action='store_true', help='Bypass cache')
    parser.add_argument('--dry_run', action='store_true', help='Dry run mode - log actions without executing')
    parser.add_argument('--as_user_id', type=int, help='Act as user ID for safe staff testing')
    parser.add_argument('--staff_max_pages', type=int, default=5, help='Max pages for staff mode (default: 5)')
    args = parser.parse_args()

    print("=" * 60)
    print("Canvas LMS - Cross-Listing Tool (Instructor-First)")
    print("=" * 60)
    print("Press Ctrl+C to exit gracefully at any time")

    # Load configuration
    try:
        config = get_config()
        token_provider = EnvTokenProvider()
    except ValueError as e:
        print(f"❌ Configuration Error: {e}")
        return

    # Get enrollment terms
    print("\nFetching available enrollment terms...")
    terms = fetch_active_terms(config, token_provider, use_cache=not args.no_cache)

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

    # Instructor-first flow
    instructor_input = input("\nInstructor (Canvas id, @collin.edu email, SIS id, or name). Leave blank only if you intend staff mode: ").strip()

    user_id = None
    instructor_info = None

    if instructor_input:
        # Validate email format if provided
        if "@" in instructor_input and not instructor_input.lower().endswith("@collin.edu"):
            print(f"⚠️  Warning: Email should end with @collin.edu, got: {instructor_input}")
            confirm = input("Continue anyway? (y/N): ").strip().lower()
            if confirm != 'y':
                print("Operation cancelled.")
                return

        # Resolve instructor
        print(f"Resolving instructor '{instructor_input}'...")
        resolution = resolve_instructor(config, selected_term['id'], instructor_input, token_provider)
        candidates = resolution.get('candidates', [])

        if not candidates:
            print("❌ No instructor found or instructor not active in this term.")
            return
        elif len(candidates) == 1:
            instructor_info = candidates[0]
            user_id = instructor_info['id']
            print(f"✅ Found: {instructor_info['name']} ({instructor_info['email']})")
        else:
            print(f"\nMultiple candidates found:")
            for i, candidate in enumerate(candidates, 1):
                print(f"{i}. {candidate['name']} ({candidate['email']}) - ID: {candidate['id']}")

            choice = get_user_selection(candidates, "Select instructor")
            if not choice:
                print("Operation cancelled.")
                return
            instructor_info = choice
            user_id = instructor_info['id']
            print(f"✅ Selected: {instructor_info['name']} ({instructor_info['email']})")

    else:
        # Staff mode confirmation
        staff_confirm = input("Browse whole term as staff? [y/N]: ").strip().lower()
        if staff_confirm != 'y':
            print("Operation cancelled. Instructor mode requires an instructor identifier.")
            return

        # Require search term for staff mode
        search_term = input("Search term (required for staff mode, e.g., MATH, 1405, BIO): ").strip()
        if not search_term:
            print("❌ Staff mode requires a search term.")
            return

        # Optional staff narrowing
        teacher_id_input = input("Filter by specific teacher Canvas user ID (optional): ").strip()
        teacher_ids = [int(teacher_id_input)] if teacher_id_input.isdigit() else None
        subaccounts_input = input("Filter by sub-account IDs (comma separated, optional): ").strip()
        subaccount_ids = None
        if subaccounts_input:
            try:
                subaccount_ids = [int(x.strip()) for x in subaccounts_input.split(",") if x.strip().isdigit()]
            except Exception:
                subaccount_ids = None
        only_published = input("Only published courses? (y/N): ").strip().lower() == 'y'

    # Get course sections
    print(f"\nFetching course sections for {selected_term['name']}...")
    if user_id:
        # Faculty path
        sections = get_course_sections(
            config, token_provider, selected_term['id'], user_id=user_id
        )
    else:
        # Staff path
        sections = get_course_sections(
            config, token_provider, selected_term['id'],
            user_id=None,
            teacher_ids=teacher_ids,
            subaccount_ids=subaccount_ids,
            search_term=search_term,
            only_published=only_published,
            staff_max_pages=args.staff_max_pages
        )

    if not sections:
        print("No course sections found for the selected criteria.")
        return

    # Check permissions for potential parent courses
    print("Checking course permissions...")
    course_ids = list(set(s['course_id'] for s in sections if not s.get('published')))
    permissions_map = check_course_permissions(config, token_provider, course_ids) if course_ids else {}

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

            # Check parent permissions
            parent_course_id = parent_section['course_id']
            if parent_course_id in permissions_map:
                perm_info = permissions_map[parent_course_id]
                if not perm_info.get('can_crosslist', True):
                    print(f"❌ Cannot use as parent: {perm_info.get('reason', 'Permission denied')}")
                    continue

            # Get child section
            child_section = get_user_selection(sections, "Select child section (to be cross-listed)")
            if not child_section:
                continue

            # Validate cross-listing
            errors, warnings = validate_cross_listing_candidates(config, parent_section, child_section)
            if errors:
                print(f"❌ Validation failed:")
                for error in errors:
                    print(f"  • {error}")
                continue

            if warnings:
                print("⚠️  Warnings detected:")
                for warning in warnings:
                    print(f"  • {warning}")
                print("\nPlease review these warnings carefully before proceeding.")

            # Confirm cross-listing
            print(f"\nPlease confirm the cross-listing:")
            print(f"Parent: {parent_section['full_title']}")
            print(f"Child:  {child_section['full_title']}")
            if args.dry_run:
                print("\n⚠️  DRY RUN MODE - No actual changes will be made")

            confirm = input("\nProceed with cross-listing? (y/n): ").strip().lower()
            if confirm != 'y':
                print("Cross-listing cancelled.")
                continue

            # Perform cross-listing
            instructor_id = instructor_info['id'] if instructor_info else None
            success = cross_list_section(
                config, token_provider, child_section['section_id'], parent_section['course_id'],
                dry_run=args.dry_run, term_id=selected_term['id'], instructor_id=instructor_id, as_user_id=args.as_user_id
            )
            if success:
                action = "logged" if args.dry_run else "completed"
                print(f"✅ Cross-listing {action} successfully!")
                if not args.dry_run:
                    # Refresh sections for real operations
                    if user_id:
                        sections = get_course_sections(config, token_provider, selected_term['id'], user_id=user_id)
                    else:
                        sections = get_course_sections(
                            config, token_provider, selected_term['id'],
                            teacher_ids=teacher_ids, subaccount_ids=subaccount_ids,
                            search_term=search_term, only_published=only_published,
                            staff_max_pages=args.staff_max_pages
                        )
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
            if args.dry_run:
                print("\n⚠️  DRY RUN MODE - No actual changes will be made")

            confirm = input("\nProceed with un-cross-listing? (y/n): ").strip().lower()
            if confirm != 'y':
                print("Un-cross-listing cancelled.")
                continue

            # Perform un-cross-listing
            instructor_id = instructor_info['id'] if instructor_info else None
            success = un_cross_list_section(
                config, token_provider, section_to_unlist['section_id'],
                dry_run=args.dry_run, term_id=selected_term['id'], instructor_id=instructor_id, as_user_id=args.as_user_id
            )
            if success:
                action = "logged" if args.dry_run else "completed"
                print(f"✅ Un-cross-listing {action} successfully!")
                if not args.dry_run:
                    # Refresh sections for real operations
                    if user_id:
                        sections = get_course_sections(config, token_provider, selected_term['id'], user_id=user_id)
                    else:
                        sections = get_course_sections(
                            config, token_provider, selected_term['id'],
                            teacher_ids=teacher_ids, subaccount_ids=subaccount_ids,
                            search_term=search_term, only_published=only_published,
                            staff_max_pages=args.staff_max_pages
                        )
                    display_sections_table(sections)
            else:
                print("❌ Un-cross-listing failed. Please check the logs for details.")
        
        elif choice == '3':
            # Export to CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"crosslisting_sections_{selected_term['name'].replace(' ', '_')}_{timestamp}.csv"

            try:
                export_sections_to_csv(sections, selected_term, filename)
                print(f"✅ Sections exported to: {filename}")
            except Exception as e:
                print(f"❌ Export failed: {e}")
        
        elif choice == '4':
            # Refresh sections (re-apply same filters)
            print("Refreshing sections...")
            if user_id:
                sections = get_course_sections(config, token_provider, selected_term['id'], user_id=user_id)
            else:
                sections = get_course_sections(
                    config, token_provider, selected_term['id'],
                    teacher_ids=teacher_ids, subaccount_ids=subaccount_ids,
                    search_term=search_term, only_published=only_published,
                    staff_max_pages=args.staff_max_pages
                )

            # Re-check permissions
            course_ids = list(set(s['course_id'] for s in sections if not s.get('published')))
            permissions_map = check_course_permissions(config, token_provider, course_ids) if course_ids else {}

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
        token_provider = EnvTokenProvider()
        service = CrosslistingService(config, token_provider)

        # Simple API call like VB.NET pattern
        success, message = service.crosslist_sections(
            child_section_id=12345,  # Replace with actual section ID
            parent_course_id=67890,  # Replace with actual course ID
            dry_run=True  # Safe testing
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