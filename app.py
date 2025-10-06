#!/usr/bin/env python3
"""
Flask Web Application for Canvas Cross-Listing Tool
Wraps the CLI functionality into a web interface with minimal UI.
"""

import os
import sys
import re
import signal
import atexit
import traceback
import tempfile
import time
from datetime import datetime
from typing import Optional, Dict, Any, List
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, flash, session

# Import our CLI tool functions
from standalone_crosslisting_tool import (
    get_config, EnvTokenProvider, CanvasAPIError, CanvasAPIClient,
    fetch_active_terms, resolve_instructor, get_course_sections,
    validate_cross_listing_candidates, cross_list_section, un_cross_list_section,
    export_sections_to_csv, format_sections_for_ui, check_course_permissions,
    get_section, get_course, summarize_crosslist_changes, get_user_courses, log_audit_action
)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-key-change-in-production')

# Global variables for configuration
config = None
token_provider = None

# List to track temporary files for cleanup
_temp_files = []

def cleanup_temp_files():
    """Clean up any temporary files created during execution."""
    global _temp_files
    for temp_file in _temp_files:
        try:
            if os.path.exists(temp_file):
                os.unlink(temp_file)
                app.logger.info(f"Cleaned up temporary file: {temp_file}")
        except Exception as e:
            app.logger.warning(f"Failed to clean up temporary file {temp_file}: {e}")
    _temp_files.clear()

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    app.logger.info(f"Received signal {signum}, shutting down gracefully...")
    cleanup_temp_files()
    sys.exit(0)

def register_cleanup():
    """Register cleanup handlers for graceful shutdown."""
    # Register cleanup function to run on normal exit
    atexit.register(cleanup_temp_files)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination signal

    # On Windows, also handle Ctrl+Break
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal_handler)

def init_app():
    """Initialize Canvas configuration."""
    global config, token_provider
    try:
        config = get_config()
        token_provider = EnvTokenProvider()
        return True
    except Exception as e:
        app.logger.error(f"Failed to initialize Canvas config: {e}")
        return False

def get_error_message(e: Exception) -> str:
    """Extract user-friendly error message from exceptions."""
    if isinstance(e, CanvasAPIError):
        return f"Canvas API Error: {e.message}"
    return str(e)

@app.route('/')
def home():
    """Home page with instructor lookup."""
    if not init_app():
        return render_template('error.html',
                             error="Failed to initialize Canvas configuration. Please check your environment variables.")

    return render_template('home.html')


@app.route('/lookup_instructor', methods=['POST'])
def lookup_instructor():
    """Lookup instructor and fetch their courses to determine available terms."""
    if not init_app():
        return render_template('error.html',
                             error="Failed to initialize Canvas configuration.")

    instructor_input = request.form.get('instructor', '').strip()

    if not instructor_input:
        return render_template('error.html', error="Please provide an instructor SIS ID, username, or Canvas ID.")

    try:
        # Find the user(s) in Canvas using the same logic as resolve_instructor
        # Resolution order: SIS → Canvas ID → Name/Login
        client = CanvasAPIClient(token_provider, config)
        candidates = []

        # COMMENTED OUT: Email lookup (too many false positives)
        # if "@" in instructor_input:
        #     # API: GET /api/v1/accounts/{account_id}/users?login_id={instructor_input}
        #     # Email/login_id search
        #     path = f"/api/v1/accounts/{config.account_id}/users"
        #     params = {"login_id": instructor_input}
        #     try:
        #         resp = client._make_request('GET', path, params)
        #         if isinstance(resp, list) and resp:
        #             candidates.extend(resp)
        #     except CanvasAPIError:
        #         # Fallback to search_term
        #         # API: GET /api/v1/accounts/{account_id}/users?search_term={instructor_input}
        #         params = {"search_term": instructor_input}
        #         resp = client._make_request('GET', path, params)
        #         if isinstance(resp, list):
        #             candidates.extend(resp)
        # else:

        # Try SIS ID lookup FIRST (even for numeric inputs)
        # API: GET /api/v1/users/sis_user_id:{sis_id}
        sis_id = instructor_input.replace("sis:", "")  # Remove prefix if present
        path = f"/api/v1/users/sis_user_id:{sis_id}"
        sis_found = False
        try:
            resp = client._make_request('GET', path)
            if resp:
                candidates.append(resp)
                sis_found = True
                app.logger.info(f"Found user via SIS ID: {sis_id}")
        except CanvasAPIError as e:
            app.logger.debug(f"SIS lookup failed for '{sis_id}': {e.message}")

        # If SIS lookup failed and input is all digits, try Canvas user ID
        if not sis_found and instructor_input.isdigit():
            # API: GET /api/v1/users/{user_id}
            path = f"/api/v1/users/{instructor_input}"
            try:
                resp = client._make_request('GET', path)
                if resp:
                    candidates.append(resp)
                    app.logger.info(f"Found user via Canvas ID: {instructor_input}")
            except CanvasAPIError as e:
                app.logger.debug(f"Canvas ID lookup failed for '{instructor_input}': {e.message}")

        # If no SIS or Canvas ID match, try name/login search (unless it's clearly numeric)
        if not candidates and not instructor_input.isdigit():
            # API: GET /api/v1/accounts/{account_id}/users?search_term={instructor_input}
            path = f"/api/v1/accounts/{config.account_id}/users"
            params = {"search_term": instructor_input}
            resp = client._make_request('GET', path, params)
            if isinstance(resp, list):
                candidates.extend(resp)

        if not candidates:
            return render_template('error.html',
                                 error=f"No instructor found matching '{instructor_input}'. Please check the SIS ID, username, or Canvas ID and try again.")

        # If multiple candidates, let user choose
        if len(candidates) > 1:
            return render_template('instructor_select_initial.html',
                                 candidates=candidates,
                                 instructor_input=instructor_input)

        # Single candidate - proceed with this user
        user = candidates[0]
        user_id = user.get('id')
        user_name = user.get('name') or user.get('sortable_name')
        user_sis_id = user.get('sis_user_id', 'N/A')
        user_login = user.get('login_id', 'N/A')

        # Step 2: Fetch ALL courses for this user
        all_courses = get_user_courses(config, token_provider, user_id, term_id=None)

        if not all_courses:
            return render_template('error.html',
                                 error=f"No courses found for {user_name}.")

        # Step 3: Extract unique terms from their courses
        terms_dict = {}
        for course in all_courses:
            term_id = course.get('enrollment_term_id')
            if term_id and term_id not in terms_dict:
                term_obj = course.get('term')
                if term_obj:
                    terms_dict[term_id] = {
                        'id': term_id,
                        'name': term_obj.get('name', f'Term {term_id}'),
                        'start_at': term_obj.get('start_at'),
                        'end_at': term_obj.get('end_at')
                    }

        terms = list(terms_dict.values())

        if not terms:
            return render_template('error.html',
                                 error=f"{user_name} has courses but no valid terms found.")

        # Filter terms: Show current, +1, and Default term
        # Get all active terms from Canvas
        try:
            all_active_terms = fetch_active_terms(config, token_provider, use_cache=True)
            current_term = None
            default_term = None

            # Find current and default terms
            from datetime import datetime
            now = datetime.now()
            for term in all_active_terms:
                if term.get('name', '').lower() == 'default term':
                    default_term = term
                # Check if term is current (today is between start and end dates)
                start = term.get('start_at')
                end = term.get('end_at')
                if start and end:
                    try:
                        start_date = datetime.fromisoformat(start.replace('Z', '+00:00'))
                        end_date = datetime.fromisoformat(end.replace('Z', '+00:00'))
                        if start_date <= now <= end_date:
                            current_term = term
                    except:
                        pass

            # Build filtered term list: current, next (+1), and default
            filtered_terms = []
            term_ids_to_show = set()

            if current_term:
                term_ids_to_show.add(current_term['id'])
                # Find next term (+1) by looking for term with ID current+1
                next_term_id = current_term['id'] + 1
                term_ids_to_show.add(next_term_id)

            if default_term:
                term_ids_to_show.add(default_term['id'])

            # Filter user's terms to only show relevant ones
            for term in terms:
                if term['id'] in term_ids_to_show:
                    filtered_terms.append(term)

            # If no terms matched the filter, show all terms
            terms_to_display = filtered_terms if filtered_terms else terms

        except Exception as e:
            app.logger.warning(f"Could not filter terms: {e}")
            terms_to_display = terms

        # Sort terms by ID (most recent first typically)
        terms_to_display.sort(key=lambda t: t['id'], reverse=True)

        # Render term selection page
        return render_template('select_term.html',
                             user_id=user_id,
                             user_name=user_name,
                             user_sis_id=user_sis_id,
                             user_login=user_login,
                             instructor_input=instructor_input,
                             terms=terms_to_display)

    except Exception as e:
        app.logger.error(f"Error during instructor lookup: {traceback.format_exc()}")
        return render_template('error.html', error=get_error_message(e))


@app.route('/select_instructor_initial', methods=['POST'])
def select_instructor_initial():
    """Handle initial instructor selection (when multiple candidates found)."""
    if not init_app():
        return render_template('error.html',
                             error="Failed to initialize Canvas configuration.")

    user_id = request.form.get('user_id', type=int)

    if not user_id:
        return render_template('error.html', error="Missing user ID")

    try:
        # Fetch user details first to get SIS ID and name
        client = CanvasAPIClient(token_provider, config)
        user_path = f"/api/v1/users/{user_id}"
        user = client._make_request('GET', user_path)
        user_name = user.get('name') or user.get('sortable_name', 'Unknown')
        user_sis_id = user.get('sis_user_id', 'N/A')

        # Fetch ALL courses for this user
        all_courses = get_user_courses(config, token_provider, user_id, term_id=None)

        if not all_courses:
            return render_template('error.html',
                                 error=f"No courses found for {user_name}.")

        # Extract unique terms
        terms_dict = {}
        for course in all_courses:
            term_id = course.get('enrollment_term_id')
            if term_id and term_id not in terms_dict:
                term_obj = course.get('term')
                if term_obj:
                    terms_dict[term_id] = {
                        'id': term_id,
                        'name': term_obj.get('name', f'Term {term_id}'),
                        'start_at': term_obj.get('start_at'),
                        'end_at': term_obj.get('end_at')
                    }

        terms = list(terms_dict.values())

        if not terms:
            return render_template('error.html',
                                 error="User has courses but no valid terms found.")

        # Sort terms by ID (most recent first)
        terms.sort(key=lambda t: t['id'], reverse=True)

        # Render term selection page (user_name and user_sis_id already fetched above)
        return render_template('select_term.html',
                             user_id=user_id,
                             user_name=user_name,
                             user_sis_id=user_sis_id,
                             user_login='N/A',  # Not available in this route
                             instructor_input=str(user_id),
                             terms=terms)

    except Exception as e:
        app.logger.error(f"Error during instructor selection: {traceback.format_exc()}")
        return render_template('error.html', error=get_error_message(e))


@app.route('/view_courses', methods=['POST'])
def view_courses():
    """
    View courses for selected instructor and term.

    Parent/Child Logic:
    - Each row represents a section from a course
    - Parent radio button passes course_id (unpublished courses only)
    - Child checkbox passes section_id (published sections only)
    - Template enforces: parent_course_id != child_section_id
    """
    if not init_app():
        return render_template('error.html',
                             error="Failed to initialize Canvas configuration.")

    user_id = request.form.get('user_id', type=int)
    user_name = request.form.get('user_name', '')
    term_id = request.form.get('term_id', type=int)
    flash_success = request.form.get('flash_success', '')

    # Handle flash message from successful crosslisting
    if flash_success:
        flash(flash_success, 'success')

    if not user_id or not term_id:
        return render_template('error.html', error="Missing user ID or term ID")

    try:
        # Get courses for this user in the selected term
        # This always fetches fresh data from Canvas API (no cache)
        # Includes sections with nonxlist_course_id for cross-listing detection
        raw_courses = get_user_courses(config, token_provider, user_id, term_id)

        if not raw_courses:
            return render_template('error.html',
                                 error=f"No courses found for this instructor in the selected term.")

        # Normalize courses through artifact builder
        # This deduplicates courses/sections and removes orphans
        app.logger.info(f"Normalizing {len(raw_courses)} raw courses for user {user_id} in term {term_id}")
        course_artifacts = build_course_artifacts_json(raw_courses)

        # Flatten artifacts into sections list for template
        sections = []
        for course_artifact in course_artifacts:
            for section in course_artifact['sections']:
                sections.append({
                    'section_id': section['id'],
                    'section_name': section['name'],
                    'course_id': section['course_id'],
                    'course_name': course_artifact['name'],
                    'course_code': course_artifact['course_code'],
                    'published': 'Yes' if course_artifact['workflow_state'] == 'available' else 'No',
                    'workflow_state': course_artifact['workflow_state'],
                    'cross_listed': 'Yes' if section['cross_listed'] else 'No',
                    'nonxlist_course_id': section['nonxlist_course_id'],
                    'total_students': course_artifact['total_students']
                })

        # Check permissions for courses
        course_ids = list(set(s['course_id'] for s in sections))
        permissions_map = check_course_permissions(config, token_provider, course_ids) if course_ids else {}

        # Get term info
        term_obj = raw_courses[0].get('term') if raw_courses else None
        term_name = term_obj.get('name') if term_obj else f'Term {term_id}'

        app.logger.info(f"Rendering {len(sections)} normalized sections for {len(course_artifacts)} courses")

        return render_template('courses.html',
                             user_id=user_id,
                             user_name=user_name,
                             term_id=term_id,
                             term_name=term_name,
                             sections=sections,
                             permissions_map=permissions_map)

    except Exception as e:
        app.logger.error(f"Error fetching courses: {traceback.format_exc()}")
        return render_template('error.html', error=get_error_message(e))


@app.route('/about')
def about():
    """
    Display information about cross-listing.
    Replaces: crosslisting_gui.py AboutCrosslistingWindow
    """
    return render_template('about.html')


@app.route('/terms')
def terms():
    """Show available active terms."""
    if not init_app():
        return render_template('error.html',
                             error="Failed to initialize Canvas configuration.")

    try:
        terms_list = fetch_active_terms(config, token_provider, use_cache=True)
        return render_template('terms.html', terms=terms_list)
    except Exception as e:
        app.logger.error(f"Error fetching terms: {traceback.format_exc()}")
        return render_template('error.html', error=get_error_message(e))

@app.route('/sections')
def sections():
    """Show sections for a given term and optional instructor/search criteria."""
    if not init_app():
        return render_template('error.html',
                             error="Failed to initialize Canvas configuration.")

    term_id = request.args.get('term_id', type=int)
    instructor = request.args.get('instructor', '')
    search_term = request.args.get('search_term', '')

    if not term_id:
        return render_template('error.html', error="Term ID is required")

    try:
        # Get term info for display
        terms_list = fetch_active_terms(config, token_provider, use_cache=True)
        term_info = next((t for t in terms_list if t['id'] == term_id), None)
        if not term_info:
            return render_template('error.html', error="Invalid term ID")

        user_id = None
        instructor_info = None
        sections_list = []

        # Handle instructor resolution if provided
        if instructor:
            resolution = resolve_instructor(config, term_id, instructor, token_provider)
            candidates = resolution.get('candidates', [])

            if not candidates:
                return render_template('sections.html',
                                     term=term_info,
                                     sections=[],
                                     error="No instructor found or instructor not active in this term")
            elif len(candidates) == 1:
                instructor_info = candidates[0]
                user_id = instructor_info['id']
            else:
                # Multiple candidates - let user choose
                return render_template('instructor_select.html',
                                     term=term_info,
                                     candidates=candidates,
                                     original_instructor=instructor,
                                     search_term=search_term)

        # Get sections
        if user_id:
            # Faculty path
            sections_list = get_course_sections(config, token_provider, term_id, user_id=user_id)
        elif search_term:
            # Staff path with search term
            sections_list = get_course_sections(
                config, token_provider, term_id,
                search_term=search_term,
                staff_max_pages=5
            )
        else:
            # No criteria provided
            return render_template('sections.html',
                                 term=term_info,
                                 sections=[],
                                 message="Please provide either an instructor or search term to view sections")

        # Check permissions for potential parent courses
        course_ids = list(set(s['course_id'] for s in sections_list if not s.get('published')))
        permissions_map = check_course_permissions(config, token_provider, course_ids) if course_ids else {}

        # Format sections for UI
        formatted_sections = format_sections_for_ui(sections_list, permissions_map)

        return render_template('sections.html',
                             term=term_info,
                             sections=formatted_sections,
                             instructor_info=instructor_info,
                             search_term=search_term)

    except Exception as e:
        app.logger.error(f"Error fetching sections: {traceback.format_exc()}")
        return render_template('error.html', error=get_error_message(e))

@app.route('/instructor_select', methods=['POST'])
def instructor_select():
    """Handle instructor selection from multiple candidates."""
    if not init_app():
        return render_template('error.html',
                             error="Failed to initialize Canvas configuration.")

    term_id = request.form.get('term_id', type=int)
    instructor_id = request.form.get('instructor_id', type=int)
    search_term = request.form.get('search_term', '')

    if not term_id or not instructor_id:
        return render_template('error.html', error="Missing required parameters")

    # Redirect to sections with instructor ID
    return redirect(url_for('sections_by_instructor_id',
                          term_id=term_id,
                          instructor_id=instructor_id,
                          search_term=search_term))

@app.route('/sections_by_instructor_id')
def sections_by_instructor_id():
    """Show sections for a specific instructor ID."""
    if not init_app():
        return render_template('error.html',
                             error="Failed to initialize Canvas configuration.")

    term_id = request.args.get('term_id', type=int)
    instructor_id = request.args.get('instructor_id', type=int)
    search_term = request.args.get('search_term', '')

    if not term_id or not instructor_id:
        return render_template('error.html', error="Missing required parameters")

    try:
        # Get term info
        terms_list = fetch_active_terms(config, token_provider, use_cache=True)
        term_info = next((t for t in terms_list if t['id'] == term_id), None)
        if not term_info:
            return render_template('error.html', error="Invalid term ID")

        # Get sections for instructor
        sections_list = get_course_sections(config, token_provider, term_id, user_id=instructor_id)

        # Check permissions
        course_ids = list(set(s['course_id'] for s in sections_list if not s.get('published')))
        permissions_map = check_course_permissions(config, token_provider, course_ids) if course_ids else {}

        # Format sections
        formatted_sections = format_sections_for_ui(sections_list, permissions_map)

        return render_template('sections.html',
                             term=term_info,
                             sections=formatted_sections,
                             instructor_info={'id': instructor_id},
                             search_term=search_term)

    except Exception as e:
        app.logger.error(f"Error fetching sections by instructor ID: {traceback.format_exc()}")
        return render_template('error.html', error=get_error_message(e))

def normalize_course_to_section_format(course: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a course object to section-like dict for validation.
    This allows validate_cross_listing_candidates to work with parent=course.
    """
    return {
        'course_id': course.get('id'),
        'course_name': course.get('name'),
        'course_code': course.get('course_code'),
        'section_id': course.get('id'),  # For parent, section_id = course_id
        'section_name': course.get('name'),
        'published': course.get('workflow_state') == 'available',
        'workflow_state': course.get('workflow_state'),
        'cross_listed': False,  # Parent course cannot be cross-listed
        'total_students': course.get('total_students', 0),
        'enrollment_term_id': course.get('enrollment_term_id'),
        'teachers': course.get('teachers', []),
        'subaccount_id': course.get('account_id'),
        'term': course.get('term')
    }


def normalize_section_with_course(section: Dict[str, Any], course: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge a section object with its parent course object for validation.

    The section object from GET /sections/{id} doesn't include workflow_state or publish status,
    so we need to merge it with course data to get accurate validation.

    Args:
        section: Raw section object from Canvas API (from get_section)
        course: Course object from Canvas API (from get_course)

    Returns:
        Dict with section data merged with course metadata for validation
    """
    return {
        'section_id': section.get('id'),
        'section_name': section.get('name'),
        'course_id': course.get('id'),
        'course_name': course.get('name'),
        'course_code': course.get('course_code'),
        'published': course.get('workflow_state') == 'available',
        'workflow_state': course.get('workflow_state'),
        'cross_listed': (section.get('nonxlist_course_id') is not None and
                        section.get('nonxlist_course_id') != section.get('course_id')),
        'nonxlist_course_id': section.get('nonxlist_course_id'),
        'total_students': course.get('total_students', 0),
        'enrollment_term_id': course.get('enrollment_term_id'),
        'teachers': course.get('teachers', []),
        'subaccount_id': course.get('account_id'),
        'term': course.get('term')
    }


def build_course_artifacts_json(courses: list) -> list:
    """
    Normalize raw Canvas API course data into clean, deduplicated course artifacts.

    This function:
    - Skips orphaned courses (courses with no sections)
    - Deduplicates courses by course_id
    - Deduplicates sections by section_id
    - Preserves accurate workflow_state from Canvas
    - Handles cross-listed sections properly

    Args:
        courses: Raw list of course objects from Canvas API with sections included

    Returns:
        List of normalized course artifacts with schema:
        {
            "course_id": int,
            "name": str,
            "course_code": str,
            "workflow_state": str,
            "total_students": int,
            "sections": [
                {
                    "id": int,
                    "name": str,
                    "course_id": int,
                    "cross_listed": bool,
                    "nonxlist_course_id": int | None
                }
            ]
        }
    """
    course_map = {}  # course_id -> course artifact
    seen_sections = set()  # Track section IDs to avoid duplicates
    seen_course_ids = set()  # Track course IDs to prevent duplicates
    orphaned_courses = []  # Track courses with no sections

    app.logger.info(f"Starting course artifact normalization for {len(courses)} raw courses")

    # First pass: Build course map and collect sections
    for course in courses:
        course_id = course.get('id')

        # Skip if we've already processed this course ID
        if course_id in seen_course_ids:
            app.logger.debug(f"Skipping duplicate course {course_id} '{course.get('name')}'")
            continue
        seen_course_ids.add(course_id)

        course_sections = course.get('sections', [])

        # Skip courses with no sections (orphaned after cross-listing)
        if not course_sections:
            orphaned_courses.append({
                'id': course_id,
                'name': course.get('name'),
                'code': course.get('course_code')
            })
            app.logger.debug(f"Skipping orphan course {course_id} '{course.get('name')}' - no sections")
            continue

        # Check if ALL sections are explicitly cross-listed to OTHER courses
        # A section is cross-listed elsewhere if:
        # 1. It has a nonxlist_course_id (meaning it was moved)
        # 2. The nonxlist_course_id equals THIS course_id (meaning it originated here)
        # 3. The section's course_id is different (meaning it now belongs elsewhere)
        all_sections_crosslisted_elsewhere = True
        for section in course_sections:
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
                all_sections_crosslisted_elsewhere = False
                break

        if all_sections_crosslisted_elsewhere:
            orphaned_courses.append({
                'id': course_id,
                'name': course.get('name'),
                'code': course.get('course_code')
            })
            app.logger.debug(f"Skipping orphan course {course_id} '{course.get('name')}' - "
                           f"all {len(course_sections)} sections belong to other courses")
            continue

        # Initialize course artifact if not seen
        if course_id not in course_map:
            course_map[course_id] = {
                'course_id': course_id,
                'name': course.get('name'),
                'course_code': course.get('course_code'),
                'workflow_state': course.get('workflow_state'),
                'total_students': course.get('total_students', 0),
                'sections': []
            }

        # Process sections that belong to THIS course
        for section in course_sections:
            section_id = section.get('id')
            section_course_id = section.get('course_id')
            nonxlist_course_id = section.get('nonxlist_course_id')

            # If course_id is None/missing, treat section as belonging to parent course
            if section_course_id is None:
                section_course_id = course_id

            # Skip duplicate sections
            if section_id in seen_sections:
                app.logger.debug(f"Skipping duplicate section {section_id} '{section.get('name')}'")
                continue

            # Only add section if it CURRENTLY belongs to this course
            # (section.course_id matches the course we're iterating)
            if section_course_id != course_id:
                app.logger.debug(f"Skipping section {section_id} - belongs to course {section_course_id}, not {course_id}")
                continue

            seen_sections.add(section_id)

            # Determine if section is cross-listed
            # A section is cross-listed if it has a nonxlist_course_id that differs from current course_id
            is_cross_listed = (nonxlist_course_id is not None and
                             nonxlist_course_id != section_course_id)

            section_artifact = {
                'id': section_id,
                'name': section.get('name'),
                'course_id': section_course_id,
                'cross_listed': is_cross_listed,
                'nonxlist_course_id': nonxlist_course_id
            }

            course_map[course_id]['sections'].append(section_artifact)

            if is_cross_listed:
                app.logger.info(f"Cross-listed section {section_id} '{section.get('name')}': "
                              f"currently in course {section_course_id}, "
                              f"originally from course {nonxlist_course_id}")

    # Second pass: Remove courses with no sections (can happen after filtering)
    final_courses = []
    for course_id, course_artifact in course_map.items():
        if not course_artifact['sections']:
            app.logger.debug(f"Removing course {course_id} '{course_artifact['name']}' - "
                           f"no sections after filtering")
            orphaned_courses.append({
                'id': course_id,
                'name': course_artifact['name'],
                'code': course_artifact['course_code']
            })
        else:
            final_courses.append(course_artifact)

    # Log summary
    app.logger.info(f"Normalization complete: {len(final_courses)} active courses, "
                   f"{len(orphaned_courses)} orphaned courses skipped, "
                   f"{len(seen_sections)} unique sections")

    if orphaned_courses:
        app.logger.debug(f"Orphaned courses: {orphaned_courses}")

    return final_courses


def build_courses_state_json(user_id: int, term_id: int, user_name: str = '') -> Dict[str, Any]:
    """
    Build full refreshed courses state JSON for a given instructor and term.
    This is the single source of truth for frontend state after crosslist/uncrosslist.

    Uses the normalization layer to deduplicate courses and sections.

    Args:
        user_id: Canvas user ID
        term_id: Enrollment term ID
        user_name: Optional user name for display

    Returns:
        Dict with courses_state containing sections, term info, and user info
    """
    # Get courses for this user in the selected term
    # Always fetches fresh data from Canvas API (no cache)
    raw_courses = get_user_courses(config, token_provider, user_id, term_id)

    if not raw_courses:
        return {
            'sections': [],
            'term_id': term_id,
            'term_name': f'Term {term_id}',
            'user_id': user_id,
            'user_name': user_name
        }

    # Normalize courses using the artifact builder
    # This handles deduplication, orphan removal, and cross-listing detection
    course_artifacts = build_course_artifacts_json(raw_courses)

    # Flatten normalized artifacts into sections list for backward compatibility
    sections = []
    for course_artifact in course_artifacts:
        for section in course_artifact['sections']:
            sections.append({
                'section_id': section['id'],
                'section_name': section['name'],
                'course_id': section['course_id'],
                'course_name': course_artifact['name'],
                'course_code': course_artifact['course_code'],
                'published': 'Yes' if course_artifact['workflow_state'] == 'available' else 'No',
                'workflow_state': course_artifact['workflow_state'],
                'cross_listed': 'Yes' if section['cross_listed'] else 'No',
                'nonxlist_course_id': section['nonxlist_course_id'],
                'total_students': course_artifact['total_students']
            })

    # Get term info
    term_obj = raw_courses[0].get('term') if raw_courses else None
    term_name = term_obj.get('name') if term_obj else f'Term {term_id}'

    return {
        'sections': sections,
        'term_id': term_id,
        'term_name': term_name,
        'user_id': user_id,
        'user_name': user_name
    }


@app.route('/validate_crosslist', methods=['POST'])
def validate_crosslist():
    """
    Validate cross-listing and show confirmation page with warnings and acknowledgments.
    Replaces: crosslisting_gui.py WarningConfirmDialog, MultipleChildWarningDialog, CrosslistingConfirmDialog

    SOP Rules and Parent/Child Logic:
    - Parent must be a COURSE (unpublished) - identified by course_id
    - Child must be a SECTION (from published course) - identified by section_id
    - parent_course_id != child_section_id (courses.html enforces this)
    - Uses get_course() to fetch parent, get_section() to fetch child
    - normalize_course_to_section_format() converts parent course to section-like format for validation
    - Never calls get_section() for parent
    """
    if not init_app():
        return render_template('error.html',
                             error="Failed to initialize Canvas configuration.")

    try:
        parent_course_id = request.form.get('parent_course_id', type=int)
        child_section_ids = request.form.getlist('child_section_id')  # Support multiple children
        term_id = request.form.get('term_id', type=int)
        instructor_id = request.form.get('instructor_id', type=int)
        user_name = request.form.get('user_name', '')
        term_name = request.form.get('term_name', '')

        # Developer tools flags
        dry_run = request.form.get('dry_run') == 'on'
        bypass_cache = request.form.get('bypass_cache') == 'on'
        override_sis = request.form.get('override_sis') == 'on'

        if not parent_course_id or not child_section_ids:
            return render_template('error.html', error='Missing required parameters')

        # Check for multiple children (blocking warning)
        if len(child_section_ids) > 1:
            flash("Multiple child sections selected. Please select only one child section at a time.", "danger")
            return redirect(request.referrer or url_for('home'))

        child_section_id = int(child_section_ids[0])

        # Fetch parent COURSE and child SECTION
        # Parent = course (GET /api/v1/courses/{id})
        # Child = section (GET /api/v1/sections/{id})
        parent_course = get_course(config, token_provider, parent_course_id,
                                    include=['teachers', 'total_students', 'term'])
        child_section_raw = get_section(config, token_provider, child_section_id)

        if not parent_course or not child_section_raw:
            return render_template('error.html', error="Failed to fetch course/section details")

        # Fetch the child section's parent course to get workflow_state and publish status
        # The section API doesn't include this, so we need the course object
        child_course = get_course(config, token_provider, child_section_raw.get('course_id'),
                                   include=['teachers', 'total_students', 'term'])

        if not child_course:
            return render_template('error.html', error="Failed to fetch child section's course details")

        # Normalize both parent and child for validation
        parent_section = normalize_course_to_section_format(parent_course)
        child_section = normalize_section_with_course(child_section_raw, child_course)

        # Debug logging to verify parent/child states before validation
        app.logger.debug(f"Parent normalized: course_id={parent_section['course_id']}, "
                        f"published={parent_section['published']}, "
                        f"workflow_state={parent_section['workflow_state']}")
        app.logger.debug(f"Child normalized: section_id={child_section['section_id']}, "
                        f"course_id={child_section['course_id']}, "
                        f"published={child_section['published']}, "
                        f"workflow_state={child_section['workflow_state']}")

        # Run validation
        errors, warnings = validate_cross_listing_candidates(config, parent_section, child_section)

        # If there are blocking errors, show them and stop
        if errors:
            return render_template('error.html',
                                 error="Cannot proceed with cross-listing:<br>" + "<br>".join(f"• {e}" for e in errors))

        # Show confirmation page with warnings (if any) and required acknowledgments
        return render_template('confirm_crosslist.html',
                             parent_section=parent_section,
                             child_section=child_section,
                             warnings=warnings,
                             parent_course_id=parent_course_id,
                             child_section_id=child_section_id,
                             term_id=term_id,
                             instructor_id=instructor_id,
                             user_name=user_name,
                             term_name=term_name,
                             dry_run=dry_run,
                             bypass_cache=bypass_cache,
                             override_sis=override_sis)

    except Exception as e:
        app.logger.error(f"Error during validation: {traceback.format_exc()}")
        return render_template('error.html', error=get_error_message(e))


@app.route('/crosslist', methods=['POST'])
def crosslist():
    """
    Perform cross-listing operation after validation and acknowledgment.
    Replaces: crosslisting_gui.py execute_crosslisting()

    SOP Endpoint:
    POST /api/v1/sections/{child_section_id}/crosslist/{parent_course_id}
    - Parent = course_id (unpublished)
    - Child = section_id (from published course)
    """
    if not init_app():
        return jsonify({'success': False, 'message': 'Failed to initialize Canvas configuration'})

    try:
        parent_course_id = request.form.get('parent_course_id', type=int)
        child_section_id = request.form.get('child_section_id', type=int)
        term_id = request.form.get('term_id', type=int)
        instructor_id = request.form.get('instructor_id', type=int)

        # Developer tools flags
        dry_run = request.form.get('dry_run') == 'on'
        bypass_cache = request.form.get('bypass_cache') == 'on'
        override_sis = request.form.get('override_sis') == 'on'

        # Verify acknowledgments (required checkboxes)
        # Note: ack_sis is currently commented out in the template
        ack_approval = request.form.get('ack_approval') == 'on'
        ack_syllabus = request.form.get('ack_syllabus') == 'on'

        if not (ack_approval and ack_syllabus):
            flash("All acknowledgments must be checked before proceeding.", "danger")
            return redirect(request.referrer or url_for('home'))

        if not parent_course_id or not child_section_id:
            return jsonify({'success': False, 'message': 'Missing required parameters'})

        # Perform cross-listing
        success = cross_list_section(
            config, token_provider, child_section_id, parent_course_id,
            dry_run=dry_run, term_id=term_id, instructor_id=instructor_id
        )

        # Log audit action
        log_audit_action(
            actor_as_user_id=instructor_id,
            term_id=term_id,
            instructor_id=instructor_id,
            action='crosslist',
            parent_course_id=parent_course_id,
            child_section_id=child_section_id,
            result='success' if success else 'failed',
            dry_run=dry_run,
            message=f"Flask web interface: Cross-listed section {child_section_id} into course {parent_course_id}" + (" (dry run)" if dry_run else ""),
            child_section_ids=[child_section_id]
        )

        # Check if this is a JSON request (AJAX) or traditional form submission
        is_json_request = request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html

        if success:
            message = f"Successfully cross-listed section {child_section_id} into course {parent_course_id}"
            if dry_run:
                message += " (DRY RUN - no changes made)"

            # For JSON/AJAX requests, return full refreshed state
            if is_json_request:
                # Add delay to allow Canvas to process the crosslisting operation
                # Canvas may take a moment to update section relationships
                time.sleep(2)

                # Re-fetch full updated state from Canvas
                user_name = request.form.get('user_name', '')
                courses_state = build_courses_state_json(instructor_id, term_id, user_name)

                return jsonify({
                    'status': 'success',
                    'message': 'Crosslisting successful. You may now undo this action from the table.',
                    'courses_state': courses_state
                })

            # For traditional form submissions, render template
            return render_template('result.html',
                                 success=True,
                                 message=message,
                                 operation="Cross-list",
                                 instructor_id=instructor_id,
                                 term_id=term_id)
        else:
            error_message = "Cross-listing operation failed. Please check the logs for details."

            if is_json_request:
                return jsonify({
                    'status': 'error',
                    'message': error_message
                }), 400

            return render_template('result.html',
                                 success=False,
                                 message=error_message,
                                 operation="Cross-list",
                                 instructor_id=instructor_id,
                                 term_id=term_id)

    except Exception as e:
        app.logger.error(f"Error during cross-listing: {traceback.format_exc()}")
        error_message = get_error_message(e)

        is_json_request = request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html
        if is_json_request:
            return jsonify({
                'status': 'error',
                'message': error_message
            }), 500

        return render_template('result.html',
                             success=False,
                             message=error_message,
                             operation="Cross-list")

@app.route('/uncrosslist', methods=['POST'])
def uncrosslist():
    """
    Perform un-cross-listing operation (undo).
    Replaces: crosslisting_gui.py undo_specific_section()
    """
    if not init_app():
        return jsonify({'success': False, 'message': 'Failed to initialize Canvas configuration'})

    try:
        section_id = request.form.get('section_id', type=int)
        term_id = request.form.get('term_id', type=int)
        instructor_id = request.form.get('instructor_id', type=int)

        if not section_id:
            return jsonify({'success': False, 'message': 'Missing section ID'})

        # Perform un-cross-listing
        success = un_cross_list_section(
            config, token_provider, section_id,
            dry_run=False, term_id=term_id, instructor_id=instructor_id
        )

        # Log audit action
        log_audit_action(
            actor_as_user_id=instructor_id,
            term_id=term_id,
            instructor_id=instructor_id,
            action='uncrosslist',
            parent_course_id=None,
            child_section_id=section_id,
            result='success' if success else 'failed',
            dry_run=False,
            message=f"Flask web interface: Un-cross-listed section {section_id}"
        )

        # Check if this is a JSON request (AJAX) or traditional form submission
        is_json_request = request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html

        if success:
            message = f"Successfully un-cross-listed section {section_id}"

            # For JSON/AJAX requests, return full refreshed state
            if is_json_request:
                # Add delay to allow Canvas to process the un-crosslisting operation
                # Canvas may take a moment to update section relationships
                time.sleep(2)

                # Re-fetch full updated state from Canvas
                user_name = request.form.get('user_name', '')
                courses_state = build_courses_state_json(instructor_id, term_id, user_name)

                return jsonify({
                    'status': 'success',
                    'message': 'Un-crosslisting successful. The section has been restored to its original course.',
                    'courses_state': courses_state
                })

            # For traditional form submissions, render template
            return render_template('result.html',
                                 success=True,
                                 message=message,
                                 operation="Un-cross-list",
                                 instructor_id=instructor_id,
                                 term_id=term_id)
        else:
            error_message = "Un-cross-listing operation failed. Please check the logs for details."

            if is_json_request:
                return jsonify({
                    'status': 'error',
                    'message': error_message
                }), 400

            return render_template('result.html',
                                 success=False,
                                 message=error_message,
                                 operation="Un-cross-list",
                                 instructor_id=instructor_id,
                                 term_id=term_id)

    except Exception as e:
        app.logger.error(f"Error during un-cross-listing: {traceback.format_exc()}")
        error_message = get_error_message(e)

        is_json_request = request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html
        if is_json_request:
            return jsonify({
                'status': 'error',
                'message': error_message
            }), 500

        return render_template('result.html',
                             success=False,
                             message=error_message,
                             operation="Un-cross-list")

@app.route('/export')
def export():
    """Export sections to CSV."""
    if not init_app():
        return render_template('error.html',
                             error="Failed to initialize Canvas configuration.")

    term_id = request.args.get('term_id', type=int)
    instructor = request.args.get('instructor', '')
    search_term = request.args.get('search_term', '')

    if not term_id:
        return render_template('error.html', error="Term ID is required")

    try:
        # Get term info
        terms_list = fetch_active_terms(config, token_provider, use_cache=True)
        term_info = next((t for t in terms_list if t['id'] == term_id), None)
        if not term_info:
            return render_template('error.html', error="Invalid term ID")

        user_id = None

        # Resolve instructor if provided
        if instructor:
            resolution = resolve_instructor(config, term_id, instructor, token_provider)
            candidates = resolution.get('candidates', [])
            if candidates:
                user_id = candidates[0]['id']  # Use first candidate for export

        # Get sections
        if user_id:
            sections_list = get_course_sections(config, token_provider, term_id, user_id=user_id)
        elif search_term:
            sections_list = get_course_sections(
                config, token_provider, term_id,
                search_term=search_term,
                staff_max_pages=5
            )
        else:
            return render_template('error.html', error="Please provide instructor or search term for export")

        if not sections_list:
            return render_template('error.html', error="No sections found to export")

        # Create temporary CSV file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"crosslisting_sections_{term_info['name'].replace(' ', '_')}_{timestamp}.csv"

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', encoding='utf-8') as tmp_file:
            export_sections_to_csv(sections_list, term_info, tmp_file.name)
            temp_path = tmp_file.name

        # Track temporary file for cleanup
        global _temp_files
        _temp_files.append(temp_path)

        # Send file (cleanup will happen later via cleanup handlers)
        return send_file(temp_path, as_attachment=True, download_name=filename,
                        mimetype='text/csv')

    except Exception as e:
        app.logger.error(f"Error during export: {traceback.format_exc()}")
        return render_template('error.html', error=get_error_message(e))

@app.route('/debug/instructor', methods=['GET'])
def debug_instructor():
    """Debug endpoint to test instructor search without term filtering."""
    if not init_app():
        return jsonify({'error': 'Failed to initialize Canvas configuration'})

    search_term = request.args.get('search', '')
    term_id = request.args.get('term_id', type=int)

    if not search_term:
        return jsonify({'error': 'Please provide search parameter'})

    try:
        # Try to resolve instructor
        result = resolve_instructor(config, term_id, search_term, token_provider) if term_id else None

        # Also try direct Canvas API search without term filtering
        client = CanvasAPIClient(token_provider, config)
        path = f"/api/v1/accounts/{config.account_id}/users"

        # Try different search methods
        direct_results = {}

        # Method 1: Login ID
        if "@" in search_term:
            try:
                resp = client._make_request('GET', path, {"login_id": search_term})
                direct_results['login_id_search'] = resp if isinstance(resp, list) else [resp]
            except Exception as e:
                direct_results['login_id_search'] = f"Error: {str(e)}"

        # Method 2: Search term
        try:
            resp = client._make_request('GET', path, {"search_term": search_term})
            direct_results['search_term'] = resp if isinstance(resp, list) else [resp]
        except Exception as e:
            direct_results['search_term'] = f"Error: {str(e)}"

        return jsonify({
            'search_input': search_term,
            'term_id': term_id,
            'resolve_instructor_result': result,
            'direct_api_searches': direct_results
        })

    except Exception as e:
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()})


@app.route('/export_audit')
def export_audit():
    """
    Export the audit log CSV.
    Replaces: crosslisting_gui.py export_audit_log()
    """
    if not init_app():
        return render_template('error.html',
                             error="Failed to initialize Canvas configuration.")

    try:
        from pathlib import Path

        audit_path = Path('./logs/crosslist_audit.csv')

        if not audit_path.exists():
            return render_template('error.html',
                                 error="No audit log file found yet. Perform some cross-listing operations first.")

        # Create filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"crosslist_audit_{timestamp}.csv"

        # Send the file
        return send_file(audit_path, as_attachment=True, download_name=filename,
                        mimetype='text/csv')

    except Exception as e:
        app.logger.error(f"Error exporting audit log: {traceback.format_exc()}")
        return render_template('error.html', error=get_error_message(e))


@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html', error="Page not found"), 404

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f"Internal server error: {traceback.format_exc()}")
    return render_template('error.html', error="Internal server error"), 500

if __name__ == '__main__':
    # Register cleanup handlers early
    register_cleanup()

    # Check for required environment variables
    if not os.getenv('CANVAS_API_TOKEN') or not os.getenv('CANVAS_BASE_URL'):
        print("Missing required environment variables:")
        print("   CANVAS_API_TOKEN - Your Canvas API token")
        print("   CANVAS_BASE_URL - Your Canvas instance URL")
        sys.exit(1)

    # Run in debug mode for development
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    port = int(os.getenv('FLASK_PORT', 5000))

    print(f"Starting Flask application on port {port}")
    print(f"Debug mode: {debug_mode}")
    print("Press Ctrl+C to shutdown gracefully")

    try:
        app.run(host='0.0.0.0', port=port, debug=debug_mode)
    except KeyboardInterrupt:
        print("\nReceived keyboard interrupt, shutting down gracefully...")
        cleanup_temp_files()
    except Exception as e:
        print(f"Application error: {e}")
        cleanup_temp_files()
        sys.exit(1)
    finally:
        cleanup_temp_files()