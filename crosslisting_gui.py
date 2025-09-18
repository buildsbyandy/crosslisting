#!/usr/bin/env python3
"""
Canvas Cross-Listing Tool - GUI Interface for Staff
Desktop GUI wrapper for the Canvas crosslisting tool with staff capabilities.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import json
import sys
import atexit
from typing import List, Dict, Any, Optional
import re

# Import our existing Canvas API functionality
from standalone_crosslisting_tool import (
    get_config, fetch_active_terms, get_course_sections, 
    CrosslistingService, validate_cross_listing_candidates,
    CanvasAPIError
)


class AboutCrosslistingWindow:
    """Window to display cross-listing information and help."""
    
    def __init__(self, parent):
        self.window = tk.Toplevel(parent)
        self.window.title("About Cross-listing")
        self.window.geometry("600x500")
        self.window.resizable(True, True)
        
        # Make it modal
        self.window.transient(parent)
        self.window.grab_set()
        
        self.create_content()
        
        # Center the window
        self.window.update_idletasks()
        x = (self.window.winfo_screenwidth() // 2) - (600 // 2)
        y = (self.window.winfo_screenheight() // 2) - (500 // 2)
        self.window.geometry(f"600x500+{x}+{y}")
    
    def create_content(self):
        """Create the help content."""
        # Create scrollable text area
        text_frame = ttk.Frame(self.window)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        text_widget = scrolledtext.ScrolledText(
            text_frame, 
            wrap=tk.WORD, 
            font=('Arial', 10),
            state=tk.NORMAL
        )
        text_widget.pack(fill=tk.BOTH, expand=True)
        
        # Help content
        content = """ABOUT CANVAS CROSS-LISTING

What is Cross-listing?
Cross-listing combines multiple course sections into one Canvas course shell for easier management. This allows instructors teaching the same course content across different sections to manage everything in one place.

When to Use Cross-listing:
• An instructor teaches the same course content in multiple sections
• You want to combine gradebooks, announcements, and content
• Students need to see combined discussion boards and assignments
• Simplifying course management for instructors

Requirements for Cross-listing:
✓ Parent Course: Must be UNPUBLISHED (no student activity yet)
✓ Child Course: Must be PUBLISHED (ready for cross-listing)  
✓ Course Numbers: Must match (e.g., ENGL 1301 sections only)
✓ Different Sections: Cannot cross-list sections from same course
✓ Not Already Cross-listed: Neither section can be cross-listed already

What Happens After Cross-listing:
• Child section gets merged into Parent course
• Students from child section appear in parent course
• Child course becomes inactive (content moves to parent)
• Course title shows combined sections (e.g., "Section 010, 020")
• All gradebook entries, announcements, and content are merged

Canvas Validation Rules:
The tool automatically validates all requirements before allowing cross-listing. Invalid combinations will be grayed out and cannot be selected.

Undoing Cross-listing:
Cross-listing can be reversed using the "Undo cross-listing" button. This separates the sections back into individual courses.

Best Practices:
• Always cross-list BEFORE publishing the parent course
• Ensure both sections have the same course content
• Notify students about the cross-listing change
• Check that all section-specific content is appropriate for combined sections

Need Help?
Contact IT Support or your Canvas administrator for assistance with complex cross-listing scenarios.
"""
        
        text_widget.insert(tk.END, content)
        text_widget.config(state=tk.DISABLED)  # Make read-only
        
        # Close button
        button_frame = ttk.Frame(self.window)
        button_frame.pack(fill=tk.X, padx=10, pady=5)
        
        close_btn = ttk.Button(
            button_frame, 
            text="Close", 
            command=self.window.destroy
        )
        close_btn.pack(side=tk.RIGHT)


class CrosslistingConfirmDialog:
    """Confirmation dialog for cross-listing operations."""
    
    def __init__(self, parent, parent_section, child_section, validation_result):
        self.parent = parent
        self.parent_section = parent_section
        self.child_section = child_section
        self.validation_result = validation_result
        self.result = False
        
        self.create_dialog()
    
    def create_dialog(self):
        """Create the confirmation dialog."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Confirm Cross-listing")
        self.dialog.geometry("450x220")
        self.dialog.resizable(False, False)
        
        # Make it modal
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        
        # Main frame
        main_frame = ttk.Frame(self.dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        # Section information
        info_frame = ttk.LabelFrame(main_frame, text="Cross-listing Details")
        info_frame.pack(fill=tk.X, pady=(0, 15))
        
        # Parent info
        parent_label = ttk.Label(
            info_frame, 
            text=f"Parent: {self.parent_section['full_title']}"
        )
        parent_label.pack(anchor=tk.W, padx=10, pady=5)
        
        # Child info  
        child_label = ttk.Label(
            info_frame,
            text=f"Child:  {self.child_section['full_title']}"
        )
        child_label.pack(anchor=tk.W, padx=10, pady=5)
        
        # Validation status
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(0, 15))
        
        is_valid, message = self.validation_result
        if is_valid:
            status_text = "✓ All validation checks passed"
            status_color = "green"
        else:
            status_text = f"❌ Validation failed: {message}"
            status_color = "red"
        
        status_label = ttk.Label(status_frame, text=status_text, foreground=status_color)
        status_label.pack(anchor=tk.W)
        
        # Undo note (small font)
        if is_valid:
            undo_note = ttk.Label(
                status_frame, 
                text="Note: Can be undone using 'Undo cross-listing' button",
                font=('Arial', 8),
                foreground='gray'
            )
            undo_note.pack(anchor=tk.W, pady=(5, 0))
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X)
        
        cancel_btn = ttk.Button(
            button_frame, 
            text="Cancel", 
            command=self.cancel
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(5, 0))
        
        if is_valid:
            confirm_btn = ttk.Button(
                button_frame, 
                text="Confirm Cross-listing", 
                command=self.confirm
            )
            confirm_btn.pack(side=tk.RIGHT)
        
        # Center the dialog
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (450 // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (220 // 2)
        self.dialog.geometry(f"450x220+{x}+{y}")
    
    def confirm(self):
        """Handle confirm button click."""
        self.result = True
        self.dialog.destroy()
    
    def cancel(self):
        """Handle cancel button click."""
        self.result = False
        self.dialog.destroy()
    
    def show(self):
        """Show the dialog and return result."""
        self.dialog.wait_window()
        return self.result


def extract_course_number(course_code: str) -> str:
    """Extract course number from course code. E.g., 'ENGL 1301-010' → 'ENGL 1301'"""
    if not course_code:
        return ""
    # Remove section suffix (everything after last dash)
    base_code = course_code.rsplit('-', 1)[0] if '-' in course_code else course_code
    return base_code.strip()


def validate_course_match(parent_code: str, child_code: str) -> bool:
    """Validate that courses have matching numbers"""
    return extract_course_number(parent_code) == extract_course_number(child_code)


class CrosslistingGUI:
    """Main GUI application for Canvas Cross-listing."""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Canvas Cross-Listing Tool (Staff)")
        self.root.geometry("800x600")
        self.root.minsize(800, 600)
        
        # Application state
        self._is_closing = False
        self._active_threads = []
        
        # Configuration and data
        self.config = None
        self.service = None
        self.terms = []
        self.sections = []
        self.selected_term_id = None
        self.cached_sections = {}  # Cache sections by term_id
        
        # GUI variables
        self.selected_term = tk.StringVar()
        self.instructor_search = tk.StringVar()
        self.course_search = tk.StringVar()
        self.published_only = tk.BooleanVar()
        
        # Section selection
        self.parent_var = tk.StringVar()
        self.child_var = tk.StringVar()
        
        # Setup proper cleanup
        self.setup_cleanup_handlers()
        
        # Initialize GUI
        self.create_gui()
        self.load_configuration()
        
        # Load terms on startup
        self.load_terms()
    
    def setup_cleanup_handlers(self):
        """Setup proper cleanup handlers for application termination."""
        # Handle window close button
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Handle Ctrl+C and other interrupts
        self.root.bind('<Control-c>', lambda e: self.on_closing())
        
        # Register cleanup function for abnormal termination
        atexit.register(self.cleanup_resources)
    
    def on_closing(self):
        """Handle application closing."""
        if self._is_closing:
            return
            
        self._is_closing = True
        
        try:
            # Stop any running progress bars
            if hasattr(self, 'progress'):
                self.progress.stop()
            
            # Update status
            if hasattr(self, 'status_var'):
                self.status_var.set("Shutting down...")
            
            # Wait for active threads to complete (with timeout)
            self.cleanup_threads()
            
            # Clean up resources
            self.cleanup_resources()
            
            # Destroy the window
            self.root.quit()
            self.root.destroy()
            
        except Exception as e:
            # Force exit if cleanup fails
            print(f"Error during cleanup: {e}")
            sys.exit(0)
    
    def cleanup_threads(self):
        """Clean up active threads before closing."""
        if not self._active_threads:
            return
            
        # Give threads a short time to complete naturally
        import time
        cleanup_timeout = 2.0  # seconds
        start_time = time.time()
        
        while self._active_threads and (time.time() - start_time) < cleanup_timeout:
            # Remove completed threads
            self._active_threads = [t for t in self._active_threads if t.is_alive()]
            time.sleep(0.1)
            
            # Process any pending GUI events
            try:
                self.root.update_idletasks()
            except:
                break
        
        # Force cleanup any remaining threads
        remaining = [t for t in self._active_threads if t.is_alive()]
        if remaining:
            print(f"Warning: {len(remaining)} threads did not complete gracefully")
    
    def cleanup_resources(self):
        """Clean up application resources."""
        try:
            # Clear cached data
            if hasattr(self, 'cached_sections'):
                self.cached_sections.clear()
            
            # Clear sections data
            if hasattr(self, 'sections'):
                self.sections.clear()
            
            # Clear terms data  
            if hasattr(self, 'terms'):
                self.terms.clear()
            
            # Reset configuration
            self.config = None
            self.service = None
            
        except Exception as e:
            print(f"Error during resource cleanup: {e}")
    
    def start_thread(self, target, name=None):
        """Start a managed thread that will be tracked for cleanup."""
        if self._is_closing:
            return None
            
        thread = threading.Thread(target=target, daemon=True, name=name)
        self._active_threads.append(thread)
        thread.start()
        return thread
    
    def create_gui(self):
        """Create the main GUI interface."""
        # Main container
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Title
        title_label = ttk.Label(
            main_frame, 
            text="Canvas Cross-Listing Tool", 
            font=('Arial', 16, 'bold')
        )
        title_label.pack(pady=(0, 10))
        
        # Term selection
        term_frame = ttk.Frame(main_frame)
        term_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(term_frame, text="Term:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.term_combo = ttk.Combobox(
            term_frame, 
            textvariable=self.selected_term,
            state="readonly",
            width=30
        )
        self.term_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.term_combo.bind('<<ComboboxSelected>>', self.on_term_selected)
        
        # Filters frame
        filters_frame = ttk.LabelFrame(main_frame, text="Filters")
        filters_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Filter controls
        filter_grid = ttk.Frame(filters_frame)
        filter_grid.pack(fill=tk.X, padx=10, pady=10)
        
        # Row 1: Instructor and Course search
        ttk.Label(filter_grid, text="Instructor:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        
        self.instructor_entry = ttk.Entry(
            filter_grid, 
            textvariable=self.instructor_search,
            width=25
        )
        self.instructor_entry.grid(row=0, column=1, sticky=tk.W, padx=(0, 20))
        
        # Add placeholder text for Instructor
        self.add_placeholder(self.instructor_entry, "Name or Canvas User ID")
        
        ttk.Label(filter_grid, text="Course ID:").grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        
        self.course_entry = ttk.Entry(
            filter_grid,
            textvariable=self.course_search,
            width=20
        )
        self.course_entry.grid(row=0, column=3, sticky=tk.W, padx=(0, 20))
        
        # Add placeholder text for Course ID
        self.add_placeholder(self.course_entry, "e.g. ENGL 1301, MATH")
        
        # Row 2: Published only checkbox and Get Sections button
        published_cb = ttk.Checkbutton(
            filter_grid,
            text="Published Only",
            variable=self.published_only
        )
        published_cb.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(10, 0))
        
        self.get_sections_btn = ttk.Button(
            filter_grid,
            text="Get Canvas Sections",
            command=self.get_canvas_sections,
            state=tk.DISABLED
        )
        self.get_sections_btn.grid(row=1, column=3, sticky=tk.E, pady=(10, 0))
        
        # About cross-listing link
        about_frame = ttk.Frame(main_frame)
        about_frame.pack(fill=tk.X, pady=(0, 10))
        
        about_link = ttk.Button(
            about_frame,
            text="About Cross-listing",
            command=self.show_about_dialog
        )
        about_link.pack(side=tk.LEFT)
        
        # Sections table frame
        table_frame = ttk.LabelFrame(main_frame, text="Course Sections")
        table_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create treeview for sections
        self.create_sections_table(table_frame)
        
        # Action buttons
        action_frame = ttk.Frame(table_frame)
        action_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.crosslist_btn = ttk.Button(
            action_frame,
            text="Cross-list Sections",
            command=self.crosslist_sections,
            state=tk.DISABLED
        )
        self.crosslist_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.undo_btn = ttk.Button(
            action_frame,
            text="Undo Cross-listing",
            command=self.undo_crosslisting,
            state=tk.DISABLED
        )
        self.undo_btn.pack(side=tk.LEFT)
        
        # Status bar
        self.status_var = tk.StringVar()
        self.status_var.set("Ready - Select a term to begin")
        
        status_bar = ttk.Label(
            main_frame,
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor=tk.W
        )
        status_bar.pack(fill=tk.X, pady=(10, 0))
        
        # Progress bar (hidden initially)
        self.progress = ttk.Progressbar(
            main_frame,
            mode='indeterminate'
        )
    
    def create_sections_table(self, parent):
        """Create the sections table with treeview."""
        # Frame for table and scrollbars
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Define columns
        columns = ('parent', 'child', 'course_title', 'published', 'cross_listed', 'undo')
        
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=15)
        
        # Define headings
        self.tree.heading('parent', text='Parent')
        self.tree.heading('child', text='Child')  
        self.tree.heading('course_title', text='Course Title')
        self.tree.heading('published', text='Published')
        self.tree.heading('cross_listed', text='Cross-listed')
        self.tree.heading('undo', text='Undo')
        
        # Configure column widths
        self.tree.column('parent', width=60, anchor=tk.CENTER)
        self.tree.column('child', width=60, anchor=tk.CENTER)
        self.tree.column('course_title', width=400, anchor=tk.W)
        self.tree.column('published', width=80, anchor=tk.CENTER)
        self.tree.column('cross_listed', width=90, anchor=tk.CENTER)
        self.tree.column('undo', width=100, anchor=tk.CENTER)
        
        # Scrollbars
        v_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        h_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        
        self.tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        # Pack scrollbars and tree
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Bind selection events
        self.tree.bind('<ButtonRelease-1>', self.on_tree_click)
        self.tree.bind('<Double-1>', self.on_tree_double_click)
    
    def load_configuration(self):
        """Load Canvas API configuration."""
        try:
            self.config = get_config()
            self.service = CrosslistingService(self.config)
            self.status_var.set("Configuration loaded successfully")
        except Exception as e:
            messagebox.showerror("Configuration Error", f"Failed to load configuration:\n{str(e)}")
            self.status_var.set("Configuration error")
    
    def load_terms(self):
        """Load enrollment terms from Canvas."""
        if not self.config:
            return
            
        def load_terms_thread():
            try:
                if self._is_closing:
                    return
                    
                self.status_var.set("Loading terms...")
                self.progress.pack(fill=tk.X, pady=5)
                self.progress.start()
                
                terms = fetch_active_terms(self.config)
                
                # Update GUI in main thread
                if not self._is_closing:
                    self.root.after(0, self.update_terms, terms)
                
            except Exception as e:
                if not self._is_closing:
                    self.root.after(0, self.handle_error, "Failed to load terms", str(e))
            finally:
                if not self._is_closing:
                    self.root.after(0, self.hide_progress)
        
        self.start_thread(load_terms_thread, "LoadTerms")
    
    def add_placeholder(self, entry_widget, placeholder_text):
        """Add placeholder text to an Entry widget."""
        # Store original colors
        original_fg = entry_widget.cget('foreground')
        placeholder_fg = 'grey'
        
        def on_focus_in(event):
            if entry_widget.get() == placeholder_text:
                entry_widget.delete(0, tk.END)
                entry_widget.config(foreground=original_fg)
        
        def on_focus_out(event):
            if not entry_widget.get():
                entry_widget.insert(0, placeholder_text)
                entry_widget.config(foreground=placeholder_fg)
        
        # Set initial placeholder
        entry_widget.insert(0, placeholder_text)
        entry_widget.config(foreground=placeholder_fg)
        
        # Bind events
        entry_widget.bind('<FocusIn>', on_focus_in)
        entry_widget.bind('<FocusOut>', on_focus_out)
        
        # Store placeholder info for later retrieval
        entry_widget._placeholder_text = placeholder_text
        entry_widget._original_fg = original_fg
    
    def get_entry_value(self, entry_widget):
        """Get the actual value from an entry widget, ignoring placeholder text."""
        current_value = entry_widget.get()
        if hasattr(entry_widget, '_placeholder_text') and current_value == entry_widget._placeholder_text:
            return ""
        return current_value
    
    def update_terms(self, terms):
        """Update the terms dropdown with loaded data."""
        # Filter to current and recent terms (last 3 years for relevance)
        import datetime
        current_year = datetime.datetime.now().year
        recent_terms = []
        
        for term in terms:
            term_name = term.get('name', '')
            # Look for 4-digit years in term name and filter to recent years
            import re
            years = re.findall(r'20\d{2}', term_name)
            if years:
                term_year = int(years[0])
                if term_year >= current_year - 1:  # Current year and last year
                    recent_terms.append(term)
            else:
                # If no year found, include it (might be current)
                recent_terms.append(term)
        
        self.terms = recent_terms
        
        if recent_terms:
            term_names = [f"{term['name']} (ID: {term['id']})" for term in recent_terms]
            self.term_combo['values'] = term_names
            
            # Set placeholder text instead of auto-selecting first term
            self.term_combo.set("Select term...")
                
            self.status_var.set(f"Loaded {len(recent_terms)} recent terms")
        else:
            self.status_var.set("No recent terms found")
    
    def on_term_selected(self, event):
        """Handle term selection."""
        selection = self.term_combo.current()
        selected_text = self.term_combo.get()
        
        # Ignore placeholder text
        if selection >= 0 and selected_text != "Select term...":
            self.selected_term_id = self.terms[selection]['id']
            self.get_sections_btn.config(state=tk.NORMAL)
            self.status_var.set(f"Term selected: {self.terms[selection]['name']}")
            
            # Clear previous sections
            self.clear_sections_table()
        else:
            # No valid term selected
            self.selected_term_id = None
            self.get_sections_btn.config(state=tk.DISABLED)
            self.clear_sections_table()
    
    def get_canvas_sections(self):
        """Get Canvas sections based on current filters."""
        if not self.selected_term_id:
            return
        
        # Check if we have cached sections for this term with these filters
        filter_key = self.get_filter_key()
        cache_key = f"{self.selected_term_id}_{filter_key}"
        
        if cache_key in self.cached_sections:
            self.sections = self.cached_sections[cache_key]
            self.populate_sections_table()
            self.status_var.set(f"Loaded {len(self.sections)} sections (cached)")
            return
        
        def load_sections_thread():
            try:
                if self._is_closing:
                    return
                    
                # Show progress bar immediately
                def show_progress():
                    self.status_var.set("Loading sections...")
                    self.progress.pack(fill=tk.X, pady=5)
                    self.progress.start()
                    self.root.update_idletasks()
                
                self.root.after(0, show_progress)
                
                # Prepare filters
                teacher_ids = None
                instructor_val = self.get_entry_value(self.instructor_entry).strip()
                if instructor_val:
                    # Try to parse as user ID first, fall back to name search
                    if instructor_val.isdigit():
                        teacher_ids = [int(instructor_val)]
                
                search_term = self.get_entry_value(self.course_entry).strip() or None
                only_published = self.published_only.get()
                
                # Load sections - include both available and created states for cross-listing
                if only_published:
                    # Use existing function when filtering to published only
                    sections = get_course_sections(
                        self.config,
                        self.selected_term_id,
                        teacher_ids=teacher_ids,
                        search_term=search_term,
                        only_published=True
                    )
                else:
                    # Import the more specific functions for staff use
                    from standalone_crosslisting_tool import list_account_courses_filtered, list_sections_for_courses
                    
                    # Get courses with both available and created states
                    # Add pagination safety limit to prevent infinite loops
                    from standalone_crosslisting_tool import CanvasAPIClient
                    client = CanvasAPIClient(self.config)
                    
                    # Manually build the request with page limit
                    path = f"/api/v1/accounts/{self.config.account_id}/courses"
                    params = {
                        "enrollment_term_id": self.selected_term_id,
                        "with_enrollments": "true",
                        "include[]": ["teachers", "term", "account_name"],
                        "per_page": self.config.per_page,
                        "state[]": ["available", "created"]
                    }
                    
                    if teacher_ids:
                        for tid in teacher_ids:
                            params.setdefault("by_teachers[]", []).append(tid)
                        params.setdefault("enrollment_type[]", []).append("teacher")
                    
                    if search_term and len(search_term) >= 2:
                        params["search_term"] = search_term
                    
                    # Use limited pagination (max 10 pages to prevent infinite loops)
                    courses = client.get_paginated_data(path, params, max_pages=10)
                    print(f"Debug: Found {len(courses)} courses")
                    sections = list_sections_for_courses(self.config, courses)
                    print(f"Debug: Found {len(sections)} sections")
                
                # Cache the results
                self.cached_sections[cache_key] = sections
                
                # Update GUI in main thread
                if not self._is_closing:
                    self.root.after(0, self.update_sections, sections)
                
            except Exception as e:
                if not self._is_closing:
                    self.root.after(0, self.handle_error, "Failed to load sections", str(e))
            finally:
                if not self._is_closing:
                    self.root.after(0, self.hide_progress)
        
        self.start_thread(load_sections_thread, "LoadSections")
    
    def get_filter_key(self):
        """Generate a key for caching based on current filters."""
        instructor_val = self.get_entry_value(self.instructor_entry)
        course_val = self.get_entry_value(self.course_entry)
        return f"{instructor_val}_{course_val}_{self.published_only.get()}"
    
    def update_sections(self, sections):
        """Update sections table with loaded data."""
        print(f"Debug: update_sections called with {len(sections)} sections")
        self.sections = sections
        self.populate_sections_table()
        self.status_var.set(f"Loaded {len(sections)} sections")
    
    def populate_sections_table(self):
        """Populate the sections table."""
        # Clear existing items
        self.clear_sections_table()
        
        if not self.sections:
            self.status_var.set("No sections found")
            return
        
        # Group sections by course number for validation
        course_groups = {}
        for section in self.sections:
            course_num = extract_course_number(section.get('course_code', ''))
            if course_num not in course_groups:
                course_groups[course_num] = []
            course_groups[course_num].append(section)
        
        # Add sections to tree
        for i, section in enumerate(self.sections):
            # Determine if this section can be selected
            course_num = extract_course_number(section.get('course_code', ''))
            can_be_parent = not section.get('published', True) and not section.get('cross_listed', False)
            can_be_child = section.get('published', False) and not section.get('cross_listed', False)
            
            # Create item
            item_id = self.tree.insert('', 'end', iid=str(i), values=(
                '○' if can_be_parent else '',  # Parent radio
                '○' if can_be_child else '',   # Child radio  
                section.get('full_title', ''),
                'Yes' if section.get('published') else 'No',
                'Yes' if section.get('cross_listed') else 'No',
                'Undo cross-listing' if section.get('cross_listed') else ''
            ))
            
            # Color coding like the example
            if section.get('published'):
                if section.get('cross_listed'):
                    # Already cross-listed - neutral
                    pass
                else:
                    # Published (potential child) - light green background
                    self.tree.set(item_id, 'published', '🟢 Yes')
            else:
                if not section.get('cross_listed'):
                    # Unpublished (potential parent) - light red background
                    self.tree.set(item_id, 'published', '🔴 No')
            
            if section.get('cross_listed'):
                self.tree.set(item_id, 'cross_listed', '🔗 Yes')
            else:
                self.tree.set(item_id, 'cross_listed', '❌ No')
    
    def clear_sections_table(self):
        """Clear the sections table."""
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Reset selections
        self.parent_var.set('')
        self.child_var.set('')
        self.update_button_states()
    
    def on_tree_click(self, event):
        """Handle tree item clicks for radio button simulation."""
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
            
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        
        if not item:
            return
            
        try:
            section_index = int(item)
            section = self.sections[section_index]
        except (ValueError, IndexError):
            return
        
        # Handle Parent column clicks
        if column == '#1':  # Parent column
            if not section.get('published', True) and not section.get('cross_listed', False):
                self.select_parent(section_index)
        
        # Handle Child column clicks
        elif column == '#2':  # Child column
            if section.get('published', False) and not section.get('cross_listed', False):
                self.select_child(section_index)
        
        # Handle Undo column clicks
        elif column == '#6':  # Undo column
            if section.get('cross_listed', False):
                self.undo_specific_section(section_index)
    
    def on_tree_double_click(self, event):
        """Handle double-click events."""
        pass  # Placeholder for future functionality
    
    def select_parent(self, section_index):
        """Select a section as parent."""
        # Clear all parent selections
        for i, item in enumerate(self.tree.get_children()):
            if i == section_index:
                self.tree.set(item, 'parent', '●')
                self.parent_var.set(str(section_index))
            else:
                self.tree.set(item, 'parent', '○' if not self.sections[i].get('published', True) and not self.sections[i].get('cross_listed', False) else '')
        
        # Update child options based on parent selection
        self.update_child_options()
        self.update_button_states()
    
    def select_child(self, section_index):
        """Select a section as child."""
        parent_index = self.get_parent_index()
        if parent_index is None:
            messagebox.showwarning("Selection Error", "Please select a parent section first.")
            return
        
        parent_section = self.sections[parent_index]
        child_section = self.sections[section_index]
        
        # Validate course match
        if not validate_course_match(parent_section.get('course_code', ''), child_section.get('course_code', '')):
            messagebox.showwarning(
                "Invalid Selection",
                f"Course numbers don't match:\nParent: {extract_course_number(parent_section.get('course_code', ''))}\nChild: {extract_course_number(child_section.get('course_code', ''))}"
            )
            return
        
        # Clear all child selections
        for i, item in enumerate(self.tree.get_children()):
            if i == section_index:
                self.tree.set(item, 'child', '●')
                self.child_var.set(str(section_index))
            else:
                is_valid_child = (
                    self.sections[i].get('published', False) and 
                    not self.sections[i].get('cross_listed', False) and
                    validate_course_match(parent_section.get('course_code', ''), self.sections[i].get('course_code', ''))
                )
                self.tree.set(item, 'child', '○' if is_valid_child else '')
        
        self.update_button_states()
    
    def update_child_options(self):
        """Update available child options based on parent selection."""
        parent_index = self.get_parent_index()
        
        for i, item in enumerate(self.tree.get_children()):
            if parent_index is not None:
                parent_section = self.sections[parent_index]
                section = self.sections[i]
                
                # Can be child if: published, not cross-listed, and course numbers match
                is_valid_child = (
                    section.get('published', False) and
                    not section.get('cross_listed', False) and
                    validate_course_match(parent_section.get('course_code', ''), section.get('course_code', ''))
                )
                
                if i != parent_index:  # Don't show child option for parent
                    self.tree.set(item, 'child', '○' if is_valid_child else '')
                else:
                    self.tree.set(item, 'child', '')
            else:
                # No parent selected, show all potential children
                section = self.sections[i]
                if section.get('published', False) and not section.get('cross_listed', False):
                    self.tree.set(item, 'child', '○')
                else:
                    self.tree.set(item, 'child', '')
    
    def get_parent_index(self):
        """Get the currently selected parent index."""
        parent_val = self.parent_var.get()
        if parent_val.isdigit():
            return int(parent_val)
        return None
    
    def get_child_index(self):
        """Get the currently selected child index."""
        child_val = self.child_var.get()
        if child_val.isdigit():
            return int(child_val)
        return None
    
    def update_button_states(self):
        """Update button states based on current selections."""
        parent_index = self.get_parent_index()
        child_index = self.get_child_index()
        
        # Enable crosslist button if both parent and child are selected
        if parent_index is not None and child_index is not None:
            self.crosslist_btn.config(state=tk.NORMAL)
        else:
            self.crosslist_btn.config(state=tk.DISABLED)
        
        # Enable undo button if there are cross-listed sections
        has_crosslisted = any(section.get('cross_listed', False) for section in self.sections)
        self.undo_btn.config(state=tk.NORMAL if has_crosslisted else tk.DISABLED)
    
    def crosslist_sections(self):
        """Perform cross-listing operation."""
        parent_index = self.get_parent_index()
        child_index = self.get_child_index()
        
        if parent_index is None or child_index is None:
            messagebox.showwarning("Selection Error", "Please select both parent and child sections.")
            return
        
        parent_section = self.sections[parent_index]
        child_section = self.sections[child_index]
        
        # Validate the cross-listing
        validation_result = validate_cross_listing_candidates(parent_section, child_section)
        
        # Show confirmation dialog
        dialog = CrosslistingConfirmDialog(
            self.root, 
            parent_section, 
            child_section, 
            validation_result
        )
        
        if not dialog.show():
            return
        
        if not validation_result[0]:
            messagebox.showerror("Validation Error", validation_result[1])
            return
        
        # Perform the cross-listing
        def crosslist_thread():
            try:
                if self._is_closing:
                    return
                    
                self.root.after(0, lambda: self.status_var.set("Cross-listing sections..."))
                self.root.after(0, lambda: self.progress.pack(fill=tk.X, pady=5))
                self.root.after(0, self.progress.start)
                
                success, message = self.service.crosslist_sections(
                    child_section['section_id'],
                    parent_section['course_id']
                )
                
                if not self._is_closing:
                    self.root.after(0, self.handle_crosslist_result, success, message)
                
            except Exception as e:
                if not self._is_closing:
                    self.root.after(0, self.handle_error, "Cross-listing failed", str(e))
            finally:
                if not self._is_closing:
                    self.root.after(0, self.hide_progress)
        
        self.start_thread(crosslist_thread, "Crosslist")
    
    def handle_crosslist_result(self, success, message):
        """Handle the result of cross-listing operation."""
        if success:
            messagebox.showinfo("Success", message)
            self.status_var.set("Cross-listing completed successfully")
            
            # Refresh the sections table
            self.refresh_sections()
        else:
            messagebox.showerror("Error", message)
            self.status_var.set("Cross-listing failed")
    
    def undo_crosslisting(self):
        """Show undo options for cross-listed sections."""
        cross_listed_sections = [
            (i, section) for i, section in enumerate(self.sections)
            if section.get('cross_listed', False)
        ]
        
        if not cross_listed_sections:
            messagebox.showinfo("No Cross-listed Sections", "No cross-listed sections found to undo.")
            return
        
        # Simple selection dialog for undo
        undo_window = tk.Toplevel(self.root)
        undo_window.title("Undo Cross-listing")
        undo_window.geometry("500x300")
        undo_window.transient(self.root)
        undo_window.grab_set()
        
        ttk.Label(undo_window, text="Select section to undo cross-listing:").pack(pady=10)
        
        # Listbox for selections
        listbox = tk.Listbox(undo_window, height=8)
        listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        for i, (section_index, section) in enumerate(cross_listed_sections):
            listbox.insert(tk.END, section.get('full_title', ''))
        
        def perform_undo():
            selection = listbox.curselection()
            if not selection:
                messagebox.showwarning("Selection Error", "Please select a section to undo.")
                return
            
            section_index, section = cross_listed_sections[selection[0]]
            undo_window.destroy()
            self.undo_specific_section(section_index)
        
        # Buttons
        btn_frame = ttk.Frame(undo_window)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(btn_frame, text="Undo", command=perform_undo).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="Cancel", command=undo_window.destroy).pack(side=tk.RIGHT)
    
    def undo_specific_section(self, section_index):
        """Undo cross-listing for a specific section."""
        section = self.sections[section_index]
        
        if not section.get('cross_listed', False):
            messagebox.showwarning("Invalid Selection", "This section is not cross-listed.")
            return
        
        # Confirm undo
        if not messagebox.askyesno(
            "Confirm Undo", 
            f"Are you sure you want to undo cross-listing for:\n{section.get('full_title', '')}?"
        ):
            return
        
        def undo_thread():
            try:
                if self._is_closing:
                    return
                    
                self.root.after(0, lambda: self.status_var.set("Undoing cross-listing..."))
                self.root.after(0, lambda: self.progress.pack(fill=tk.X, pady=5))
                self.root.after(0, self.progress.start)
                
                success, message = self.service.uncrosslist_section(section['section_id'])
                
                if not self._is_closing:
                    self.root.after(0, self.handle_undo_result, success, message)
                
            except Exception as e:
                if not self._is_closing:
                    self.root.after(0, self.handle_error, "Undo failed", str(e))
            finally:
                if not self._is_closing:
                    self.root.after(0, self.hide_progress)
        
        self.start_thread(undo_thread, "UndoCrosslist")
    
    def handle_undo_result(self, success, message):
        """Handle the result of undo operation."""
        if success:
            messagebox.showinfo("Success", message)
            self.status_var.set("Undo completed successfully")
            
            # Refresh the sections table
            self.refresh_sections()
        else:
            messagebox.showerror("Error", message)
            self.status_var.set("Undo failed")
    
    def refresh_sections(self):
        """Refresh the sections table by reloading data."""
        # Clear cache for current term
        filter_key = self.get_filter_key()
        cache_key = f"{self.selected_term_id}_{filter_key}"
        if cache_key in self.cached_sections:
            del self.cached_sections[cache_key]
        
        # Reload sections
        self.get_canvas_sections()
    
    def show_about_dialog(self):
        """Show the about cross-listing dialog."""
        AboutCrosslistingWindow(self.root)
    
    def hide_progress(self):
        """Hide the progress bar."""
        self.progress.stop()
        self.progress.pack_forget()
    
    def handle_error(self, title, message):
        """Handle and display errors."""
        self.hide_progress()
        messagebox.showerror(title, message)
        self.status_var.set(f"Error: {title}")
    
    def run(self):
        """Start the GUI application."""
        self.root.mainloop()


def main():
    """Main function to run the GUI."""
    try:
        app = CrosslistingGUI()
        app.run()
    except Exception as e:
        messagebox.showerror("Application Error", f"Failed to start application:\n{str(e)}")


if __name__ == "__main__":
    main()