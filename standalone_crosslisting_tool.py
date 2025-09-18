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

# Token caching for OAuth2 client credentials
_cached_token = None
_token_expiry = None

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
    """Deprecated: Token provider that reads from environment variable.

    This class is deprecated and provided for backwards compatibility only.
    Use OAuthTokenProvider for OAuth2 client credentials flow instead.
    """
    def __init__(self, env_var: str = 'CANVAS_API_TOKEN'):
        self.env_var = env_var

    def get_token(self) -> str:
        token = os.getenv(self.env_var)
        if not token or token == 'PLACEHOLDERAPIKEY':
            raise ValueError(f"API token not found in environment variable {self.env_var}. Please use OAuth2 client credentials (CANVAS_CLIENT_ID and CANVAS_CLIENT_SECRET) instead.")
        return token


def get_canvas_token() -> str:
    """
    Get Canvas access token using OAuth2 client credentials flow.

    Reads CANVAS_CLIENT_ID, CANVAS_CLIENT_SECRET, and CANVAS_BASE_URL from environment.
    Caches the token in memory for the session run.

    Returns:
        str: Access token for Canvas API

    Raises:
        ValueError: If required environment variables are missing
        CanvasAPIError: If token request fails
    """
    global _cached_token, _token_expiry

    # Check if we have a valid cached token
    if _cached_token and _token_expiry:
        import time
        if time.time() < _token_expiry:
            return _cached_token

    # Get required environment variables
    client_id = os.getenv('CANVAS_CLIENT_ID')
    client_secret = os.getenv('CANVAS_CLIENT_SECRET')
    base_url = os.getenv('CANVAS_BASE_URL')

    if not client_id:
        raise ValueError("CANVAS_CLIENT_ID environment variable is required")
    if not client_secret:
        raise ValueError("CANVAS_CLIENT_SECRET environment variable is required")
    if not base_url:
        raise ValueError("CANVAS_BASE_URL environment variable is required")

    # Prepare OAuth2 request
    token_url = f"{base_url.rstrip('/')}/login/oauth2/token"

    # Parse URL for connection
    parsed_url = urllib.parse.urlparse(token_url)
    host = parsed_url.netloc
    port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)
    path = parsed_url.path

    # Prepare form data
    form_data = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret
    }

    # Encode form data
    encoded_data = urllib.parse.urlencode(form_data).encode('utf-8')

    # Create connection
    if parsed_url.scheme == 'https':
        conn = http.client.HTTPSConnection(host, port, timeout=30)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=30)

    try:
        # Set headers for form submission
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'
        }

        # Make request
        conn.request('POST', path, body=encoded_data, headers=headers)
        response = conn.getresponse()
        response_body = response.read().decode('utf-8')

        if response.status == 200:
            try:
                token_data = json.loads(response_body)
                access_token = token_data.get('access_token')
                if not access_token:
                    raise CanvasAPIError("No access_token in response", response.status, response_body, token_url)

                # Cache the token (assume 1 hour expiry if not provided)
                expires_in = token_data.get('expires_in', 3600)  # Default 1 hour
                import time
                _cached_token = access_token
                _token_expiry = time.time() + expires_in - 60  # Refresh 1 minute early

                logger.info("Successfully obtained Canvas access token via OAuth2")
                return access_token

            except json.JSONDecodeError as e:
                raise CanvasAPIError(f"Invalid JSON response from token endpoint: {e}", response.status, response_body, token_url)
        else:
            logger.error(f"OAuth2 token request failed: {response.status} {response.reason}")
            raise CanvasAPIError(
                f"OAuth2 token request failed: {response.status} {response.reason}",
                response.status,
                response_body,
                token_url
            )

    except (http.client.HTTPException, OSError) as e:
        raise CanvasAPIError(f"Network error during token request: {e}", request_url=token_url)
    finally:
        conn.close()


class OAuthTokenProvider:
    """Token provider that uses OAuth2 client credentials flow."""
    def get_token(self) -> str:
        return get_canvas_token()


class OAuthSessionTokenProvider:
    """Deprecated: Use OAuthTokenProvider instead."""
    def get_token(self) -> str:
        return get_canvas_token()


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
        # OAuth2 tokens will be validated by token provider
        # Skip token validation in config - just ensure placeholder is set
        if not self.api_token:
            self.api_token = "oauth2_placeholder"

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
            full_path += '?' + query_string
        
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


def is_sandbox_course_name(course_name: Optional[str]) -> bool:
    """Detect sandbox courses by name.

    A course is considered a sandbox if its name matches:
    ^Sandbox Course \\d+ for [A-Za-z0-9._-]+
    """
    if not course_name:
        return False
    return re.match(r'^Sandbox Course \d+ for [A-Za-z0-9._-]+', str(course_name)) is not None


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
    # OAuth2 client credentials will be handled by token provider
    # Just validate that base URL is available
    base_url = os.getenv('CANVAS_BASE_URL')
    if not base_url:
        raise ValueError("CANVAS_BASE_URL environment variable is required")

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

    # Use placeholder token - actual token will be provided by TokenProvider
    placeholder_token = "oauth2_placeholder"

    return CanvasConfig(
        api_token=placeholder_token,
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
    """Resolve instructor by id, email/login_id, SIS id, or name.

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
        # Resolution order as specified
        if "@" in user_key:
            # Email/login_id search - validate @collin.edu format
            if not user_key.lower().endswith("@collin.edu"):
                logger.warning(f"Email format should be @collin.edu, got: {user_key}")

            path = f"/api/v1/accounts/{config.account_id}/users"
            params = {"login_id": user_key}
            try:
                resp = client._make_request('GET', path, params)
                if isinstance(resp, list) and resp:
                    raw_matches += len(resp)
                    candidates.extend(resp)
            except CanvasAPIError:
                # Fallback to search_term
                params = {"search_term": user_key}
                resp = client._make_request('GET', path, params)
                if isinstance(resp, list):
                    candidates.extend(resp)

        elif user_key.startswith("sis:") or re.match(r'^[A-Z]+[0-9]+$', user_key):
            # SIS ID search
            sis_id = user_key.replace("sis:", "")
            path = f"/api/v1/users/sis_user_id:{sis_id}"
            try:
                resp = client._make_request('GET', path)
                if resp:
                    raw_matches += 1
                    candidates.append(resp)
            except CanvasAPIError:
                pass

        elif user_key.isdigit():
            # Canvas user ID
            path = f"/api/v1/users/{user_key}"
            try:
                resp = client._make_request('GET', path)
                if resp:
                    candidates.append(resp)
            except CanvasAPIError:
                pass

        else:
            # Name search
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

def list_user_term_courses_via_enrollments(config: CanvasConfig, token_provider: TokenProvider, user_id: int, term_id: int) -> list[dict]:
    """
    FACULTY PATH (future GUI-ready): scope by term USING USER ENROLLMENTS, then hydrate courses in parallel.
    """
    client = CanvasAPIClient(token_provider, config)
    # 1) Teacher enrollments scoped to the term
    enroll_path = f"/api/v1/users/{user_id}/enrollments"
    enroll_params = {
        "type[]": "TeacherEnrollment",
        "state[]": "active",
        "enrollment_term_id": term_id,
        "per_page": config.per_page
    }
    logger.info(f"Fetching enrollments for user {user_id} in term {term_id}")
    # Limit pages and protect against empty/looping responses from Canvas
    enrollments = client.get_paginated_data(enroll_path, enroll_params, max_pages=5)
    if not enrollments:
        logger.info("No enrollments returned; exiting early to avoid pagination loops")
        return []
    logger.info(f"Found {len(enrollments)} enrollments")
    course_ids = sorted({e.get("course_id") for e in enrollments if e.get("course_id")})
    logger.info(f"Extracted {len(course_ids)} unique course IDs: {course_ids}")

    # 2) Hydrate courses in parallel with ThreadPoolExecutor
    def fetch_course(course_id: int) -> Optional[dict]:
        try:
            # Create a separate client for each thread to avoid shared state issues
            thread_client = CanvasAPIClient(token_provider, config)
            return thread_client._make_request(
                "GET",
                f"/api/v1/courses/{course_id}",
                params={"include[]": ["term", "teachers", "sections", "total_students"]}
            )
        except CanvasAPIError as e:
            logger.warning(f"Failed to fetch course {course_id}: {e.message}")
            return None

    courses: list[dict] = []
    if course_ids:
        # Limit parallel requests to avoid overwhelming the API
        with ThreadPoolExecutor(max_workers=3) as executor:
            course_futures = {executor.submit(fetch_course, cid): cid for cid in course_ids}
            for future in course_futures:
                try:
                    course = future.result(timeout=30)  # Add timeout
                    if course:
                        courses.append(course)
                except Exception as e:
                    logger.warning(f"Failed to get course result: {e}")
    else:
        logger.info("No courses found from enrollments; returning empty list")

    return courses

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
            # Faculty path (scoped to term via enrollments, then hydrate)
            courses = list_user_term_courses_via_enrollments(config, token_provider, user_id, term_id)
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


def validate_cross_listing_candidates(config: CanvasConfig, parent_section: Dict[str, Any], child_section: Dict[str, Any]) -> List[str]:
    """
    Validate if two sections can be cross-listed according to policy rules.

    Returns:
        List of human-readable error messages (empty if valid)
    """
    errors = []

    # Check if sections are already cross-listed
    if parent_section.get('cross_listed'):
        errors.append("Parent section is already cross-listed")

    if child_section.get('cross_listed'):
        errors.append("Child section is already cross-listed")

    # Check if sections are in the same course
    if parent_section['course_id'] == child_section['course_id']:
        errors.append("Cannot cross-list sections from the same course")

    # Policy-based validation (SOP): enforce that parent is UNPUBLISHED (strict) and child is PUBLISHED
    if config.require_parent_unpublished:
        if parent_section.get('published'):
            errors.append("Parent must be unpublished")
    if config.forbid_parent_with_students:
        if (parent_section.get('total_students', 0) > 0) and parent_section.get('published'):
            errors.append("Parent is published and has student activity")

    # Child must be published (strict)
    if not child_section.get('published'):
        errors.append("Child course must be published")

    # Course number matching
    parent_number = extract_course_number(parent_section.get('course_code', ''))
    child_number = extract_course_number(child_section.get('course_code', ''))
    if parent_number and child_number and parent_number != child_number:
        errors.append(f"Course numbers don't match: {parent_number} vs {child_number}")

    # Same term required
    if config.enforce_same_term:
        parent_term = parent_section.get('enrollment_term_id')
        child_term = child_section.get('enrollment_term_id')
        if parent_term is not None and child_term is not None and parent_term != child_term:
            errors.append("Parent and child must be in the same enrollment term")

    # Same subaccount check
    if config.enforce_same_subaccount:
        parent_subaccount = parent_section.get('subaccount_id')
        child_subaccount = child_section.get('subaccount_id')
        if parent_subaccount != child_subaccount:
            errors.append(f"Subaccounts don't match: {parent_subaccount} vs {child_subaccount}")

    # Teachers must match (only enforce when both sides have teacher lists)
    parent_teachers = parent_section.get('teachers') or []
    child_teachers = child_section.get('teachers') or []
    parent_teacher_ids = {t.get('id') for t in parent_teachers if isinstance(t, dict) and t.get('id')}
    child_teacher_ids = {t.get('id') for t in child_teachers if isinstance(t, dict) and t.get('id')}
    if parent_teacher_ids and child_teacher_ids and parent_teacher_ids.isdisjoint(child_teacher_ids):
        errors.append("Teachers must match between parent and child courses")

    return errors


def log_audit_action(actor_as_user_id: Optional[int], term_id: int, instructor_id: Optional[int],
                    action: str, parent_course_id: Optional[int], child_section_id: Optional[int],
                    result: str, dry_run: bool, message: str,
                    new_parent_course_title: Optional[str] = None,
                    child_section_ids: Optional[List[int]] = None,
                    syllabus_updated: Optional[bool] = None,
                    sandbox_mode: Optional[bool] = None,
                    sop_warnings: Optional[List[str]] = None) -> None:
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
                         'new_parent_course_title', 'child_section_ids', 'syllabus_updated',
                         'sandbox_mode', 'sop_warnings']
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
                'syllabus_updated': '' if syllabus_updated is None else ('Yes' if syllabus_updated else 'No'),
                'sandbox_mode': '' if sandbox_mode is None else ('Yes' if sandbox_mode else 'No'),
                'sop_warnings': '\n'.join(sop_warnings) if sop_warnings else ''
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

        # Sandbox auto-detection based on course names
        sandbox_mode = bool(is_sandbox_course_name(parent_course.get('name')) or is_sandbox_course_name(child_course.get('name')))
        sop_warnings: List[str] = []

        # Helper to determine published state for SOP checks
        def _is_published(course: Dict[str, Any]) -> bool:
            return (course.get('workflow_state') == 'available') or bool(course.get('published'))

        parent_is_published = _is_published(parent_course)
        parent_has_students = (parent_course.get('total_students') or 0) > 0
        child_is_published = _is_published(child_course)

        # Same term strict check unless in sandbox mode
        if config.enforce_same_term and parent_term_id is not None and child_term_id is not None and parent_term_id != child_term_id:
            if sandbox_mode:
                sop_warnings.append(f"Warning: Term mismatch (parent term {parent_term_id} vs child term {child_term_id})")
            else:
                message = (
                    f"Term mismatch: parent course term {parent_term_id} vs child course term {child_term_id}. "
                    f"Cross-listing blocked."
                )
                logger.error(message)
                log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "error", dry_run, message,
                                 sandbox_mode=False, sop_warnings=None)
                return False

        # Parent published with students -> warning in sandbox mode
        if (config.require_parent_unpublished or config.forbid_parent_with_students) and parent_is_published and parent_has_students:
            if sandbox_mode:
                sop_warnings.append("Warning: Parent is published and has student activity (sandbox mode)")

        # Child must be published -> warning in sandbox mode
        if not child_is_published:
            if sandbox_mode:
                sop_warnings.append("Warning: Child course is not published (sandbox mode)")

        # Teachers must match -> warning in sandbox mode
        parent_teacher_ids = {t.get('id') for t in (parent_course.get('teachers') or []) if isinstance(t, dict) and t.get('id')}
        child_teacher_ids = {t.get('id') for t in (child_course.get('teachers') or []) if isinstance(t, dict) and t.get('id')}
        if parent_teacher_ids and child_teacher_ids and parent_teacher_ids.isdisjoint(child_teacher_ids):
            if sandbox_mode:
                sop_warnings.append("Warning: Teachers do not match between parent and child (sandbox mode)")

    except CanvasAPIError as e:
        message = f"Failed to fetch course details for term check: {e.message}"
        logger.error(message)
        log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "error", dry_run, message,
                         sandbox_mode=None, sop_warnings=None)
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
        try:
            log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "success", True, message,
                             sandbox_mode=sandbox_mode, sop_warnings=sop_warnings)
        except NameError:
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
            try:
                log_audit_action(
                    as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id,
                    "success", False, message,
                    new_parent_course_title=updates.get('new_course_name'),
                    child_section_ids=updates.get('child_section_ids') or [],
                    syllabus_updated=updates.get('syllabus_updated'),
                    sandbox_mode=sandbox_mode,
                    sop_warnings=sop_warnings
                )
            except NameError:
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
            try:
                log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "error", False, message,
                                 sandbox_mode=sandbox_mode, sop_warnings=sop_warnings)
            except NameError:
                log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "error", False, message)
            return False

    except CanvasAPIError as e:
        message = f"Failed to cross-list section: {e.message}"
        logger.error(message)
        try:
            log_audit_action(as_user_id, term_id or 0, instructor_id, action, parent_course_id, child_section_id, "error", False, message,
                             sandbox_mode=sandbox_mode, sop_warnings=sop_warnings)
        except NameError:
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


def _build_option_c_course_name(base_course_code: str, existing_course_name: str, parent_primary_suffix: str, child_suffixes: List[str]) -> str:
    """Compose course name per Option C: <parent_suffix>/<child_suffixes>: <parent_course_name> prefixed by base course code.
    - Only the primary parent section suffix is included for parent
    - Include all child suffixes
    """
    # Determine course code prefix (before last '-')
    prefix = base_course_code or ''
    if '-' in prefix:
        maybe_prefix, maybe_suffix = prefix.rsplit('-', 1)
        if re.fullmatch(r'[0-9A-Z]{1,5}', (maybe_suffix or '').upper()):
            prefix = maybe_prefix

    parts: List[str] = []
    if parent_primary_suffix:
        parts.append(parent_primary_suffix.upper())
    # Deduplicate child suffixes and exclude if same as parent
    child_list = [c.upper() for c in child_suffixes if c]
    child_list = sorted({c for c in child_list if c != (parent_primary_suffix or '').upper()})
    parts.extend(child_list)

    code_part = f"{prefix}-{('/'.join(parts))}" if parts else prefix
    return f"{code_part}: {existing_course_name}" if code_part else existing_course_name


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


def apply_post_crosslist_updates(config: CanvasConfig, token_provider: TokenProvider, parent_course_id: int,
                                 as_user_id: Optional[int] = None,
                                 primary_parent_suffix: Optional[str] = None) -> Dict[str, Any]:
    """
    After a successful cross-list, update parent course name (Option C) and syllabus child list block.
    Returns dict with new_course_name, child_section_ids, syllabus_updated.
    """
    client = CanvasAPIClient(token_provider, config, as_user_id)

    # Fetch parent course details (need name, course_code, term, syllabus)
    parent_course = get_course(config, token_provider, parent_course_id, include=["syllabus_body"], as_user_id=as_user_id)
    parent_course_name = parent_course.get('name') or ''
    parent_course_code = parent_course.get('course_code') or ''
    current_syllabus = parent_course.get('syllabus_body') or ''

    # Extract stored primary suffix marker if present
    primary_marker_re = re.compile(r"<!--\s*CROSSLIST_PRIMARY_SUFFIX:\s*([0-9A-Z]{1,5})\s*-->")
    stored_primary_suffix: Optional[str] = None
    m = primary_marker_re.search(current_syllabus)
    if m:
        stored_primary_suffix = m.group(1).upper()

    # Fetch all sections currently in the parent course
    sections = client.get_paginated_data(f"/api/v1/courses/{parent_course_id}/sections", {"per_page": config.per_page})

    parent_candidates: List[Dict[str, Any]] = []
    child_sections: List[Dict[str, Any]] = []
    child_section_ids: List[int] = []
    child_origin_course_ids: List[int] = []

    for s in sections or []:
        nonx = s.get('nonxlist_course_id')
        if nonx is None or nonx == parent_course_id:
            parent_candidates.append(s)
        else:
            child_sections.append(s)
            child_section_ids.append(s.get('id'))
            if nonx:
                child_origin_course_ids.append(nonx)

    # Decide primary parent suffix per rules: stored > provided > fallback native first
    parent_primary_suffix = (stored_primary_suffix or (primary_parent_suffix.upper() if primary_parent_suffix else ''))
    if not parent_primary_suffix and parent_candidates:
        primary = sorted(parent_candidates, key=lambda x: (x.get('id') or 0))[0]
        parent_primary_suffix = _extract_section_suffix(primary.get('sis_section_id'), primary.get('name')).upper()

    # Build child suffix list
    child_suffixes: List[str] = []
    for s in child_sections:
        child_suffixes.append(_extract_section_suffix(s.get('sis_section_id'), s.get('name')))

    # Build new course name
    new_course_name = _build_option_c_course_name(parent_course_code, parent_course_name, parent_primary_suffix, child_suffixes)

    # Update course name if changed
    if new_course_name and new_course_name != parent_course_name:
        update_course_fields(config, token_provider, parent_course_id, {"name": new_course_name}, as_user_id)

    # Build syllabus children list (fetch child origin course details)
    children_display: List[Tuple[str, str]] = []
    for ocid in sorted({cid for cid in child_origin_course_ids if cid}):
        try:
            child_course = get_course(config, token_provider, ocid, include=None, as_user_id=as_user_id)
            code = child_course.get('course_code') or ''
            name = child_course.get('name') or ''
            children_display.append((code, name))
        except CanvasAPIError:
            continue

    # Prepare syllabus block
    html_block = _build_children_html_list(children_display)
    header_block = "<hr>\n<h3>Cross-listed Child Courses</h3>\n"

    # Replace existing block between markers if present, else append header + block and persist primary marker
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
        primary_marker_str = f"<!-- CROSSLIST_PRIMARY_SUFFIX: {parent_primary_suffix} -->\n" if parent_primary_suffix else ""
        new_syllabus = (current_syllabus or '') + sep + primary_marker_str + header_block + html_block
        update_course_fields(config, token_provider, parent_course_id, {"syllabus_body": new_syllabus}, as_user_id)
        syllabus_updated = True

    return {
        "new_course_name": new_course_name,
        "child_section_ids": child_section_ids,
        "syllabus_updated": syllabus_updated
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

    parser = argparse.ArgumentParser(description='Canvas Cross-Listing Tool')
    parser.add_argument('--no_cache', action='store_true', help='Bypass cache')
    parser.add_argument('--dry_run', action='store_true', help='Dry run mode - log actions without executing')
    parser.add_argument('--as_user_id', type=int, help='Act as user ID for safe staff testing')
    parser.add_argument('--staff_max_pages', type=int, default=5, help='Max pages for staff mode (default: 5)')
    args = parser.parse_args()

    print("=" * 60)
    print("Canvas LMS - Cross-Listing Tool (Instructor-First)")
    print("=" * 60)

    # Load configuration
    try:
        config = get_config()
        token_provider = OAuthTokenProvider()
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

            # Validate cross-listing; allow in sandbox mode with warnings
            errors = validate_cross_listing_candidates(config, parent_section, child_section)
            parent_is_sandbox = is_sandbox_course_name(parent_section.get('course_name', ''))
            child_is_sandbox = is_sandbox_course_name(child_section.get('course_name', ''))
            sandbox_active = parent_is_sandbox or child_is_sandbox
            if errors and not sandbox_active:
                print(f"❌ Validation failed:")
                for error in errors:
                    print(f"  • {error}")
                continue
            elif errors and sandbox_active:
                print("⚠️  Sandbox detected: SOP checks relaxed. Warnings logged, not enforced.")

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
        token_provider = OAuthTokenProvider()
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