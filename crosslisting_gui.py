#!/usr/bin/env python3
"""
Canvas Cross-Listing Tool - GUI Interface for Staff
Desktop GUI wrapper for the Canvas crosslisting tool with staff capabilities.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import shutil
import threading
import json
import sys
import atexit
from typing import List, Dict, Any, Optional
import re
from datetime import datetime
from pathlib import Path

# Import our existing Canvas API functionality
from standalone_crosslisting_tool import (
    get_config, fetch_active_terms, get_course_sections,
    CrosslistingService, validate_cross_listing_candidates,
    CanvasAPIError, resolve_instructor, list_user_term_courses_via_enrollments,
    list_account_courses_filtered, list_sections_for_courses,
    format_sections_for_ui, cross_list_section, un_cross_list_section,
        check_course_permissions, EnvTokenProvider, extract_course_number,
    export_sections_to_csv, get_section, summarize_crosslist_changes,
    get_course_prefix
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


class InstructorSelectionDialog:
    """Dialog for selecting instructor when multiple candidates found."""

    def __init__(self, parent, candidates):
        self.parent = parent
        self.candidates = candidates
        self.result = None
        self.create_dialog()

    def create_dialog(self):
        """Create the instructor selection dialog."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Select Instructor")
        self.dialog.geometry("600x400")
        self.dialog.resizable(True, True)

        # Make it modal
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # Main frame
        main_frame = ttk.Frame(self.dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # Title
        title_label = ttk.Label(main_frame, text="Multiple instructors found. Please select one:", font=('Arial', 12, 'bold'))
        title_label.pack(pady=(0, 10))

        # Create treeview for candidates
        columns = ('name', 'login_id', 'email')
        self.tree = ttk.Treeview(main_frame, columns=columns, show='headings', height=10)

        self.tree.heading('name', text='Name')
        self.tree.heading('login_id', text='Login ID')
        self.tree.heading('email', text='Email')

        self.tree.column('name', width=200, anchor=tk.W)
        self.tree.column('login_id', width=150, anchor=tk.W)
        self.tree.column('email', width=200, anchor=tk.W)

        # Add candidates
        for i, candidate in enumerate(self.candidates):
            self.tree.insert('', 'end', iid=str(i), values=(
                candidate.get('name', ''),
                candidate.get('login_id', ''),
                candidate.get('email', '')
            ))

        # Scrollbar
        scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        # Pack tree and scrollbar
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X)

        ttk.Button(button_frame, text="Cancel", command=self.cancel).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(button_frame, text="Select", command=self.select).pack(side=tk.RIGHT)

        # Center the dialog
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (600 // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (400 // 2)
        self.dialog.geometry(f"600x400+{x}+{y}")

    def select(self):
        """Handle select button click."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select an instructor.")
            return

        try:
            index = int(selection[0])
            self.result = self.candidates[index]
            self.dialog.destroy()
        except (ValueError, IndexError):
            messagebox.showerror("Selection Error", "Invalid selection.")

    def cancel(self):
        """Handle cancel button click."""
        self.result = None
        self.dialog.destroy()

    def show(self):
        """Show the dialog and return result."""
        self.dialog.wait_window()
        return self.result


class WarningConfirmDialog:
    """Warning confirmation dialog for cross-listing operations with warnings."""

    def __init__(self, parent, warnings, parent_section, child_section):
        self.parent = parent
        self.warnings = warnings
        self.parent_section = parent_section
        self.child_section = child_section
        self.result = False
        self.create_dialog()

    def create_dialog(self):
        """Create the warning confirmation dialog."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Cross-listing Warnings")
        self.dialog.geometry("600x400")
        self.dialog.resizable(False, False)

        # Make it modal
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # Main frame
        main_frame = ttk.Frame(self.dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # Check if this is specifically a course name mismatch
        has_name_mismatch = any("Course name mismatch" in warning for warning in self.warnings)
        has_teacher_mismatch = any("Teachers do not match" in warning for warning in self.warnings)
        has_subaccount_mismatch = any("Subaccounts don't match" in warning for warning in self.warnings)
        has_student_activity = any("published and has student activity" in warning for warning in self.warnings)

        if has_name_mismatch:
            # Special handling for course name mismatch
            title_label = ttk.Label(
                main_frame,
                text="Course Name Mismatch Warning",
                font=('Arial', 14, 'bold'),
                foreground="orange"
            )
            title_label.pack(pady=(0, 15))

            parent_name = self.parent_section.get('course_name', '')
            child_name = self.child_section.get('course_name', '')

            message_text = f"""You have selected a child course that does not match in name.

Parent: {parent_name}
Child: {child_name}

If you intend to do this, click Yes to continue. Otherwise, click No to cancel."""

            message_label = ttk.Label(
                main_frame,
                text=message_text,
                font=('Arial', 11),
                justify=tk.LEFT
            )
            message_label.pack(pady=(0, 20))

        else:
            # Generic warning dialog
            title_label = ttk.Label(
                main_frame,
                text="Cross-listing Warnings Detected",
                font=('Arial', 14, 'bold'),
                foreground="orange"
            )
            title_label.pack(pady=(0, 15))

            if has_teacher_mismatch:
                warning_text = "The courses do not have matching teachers."
            elif has_subaccount_mismatch:
                warning_text = "The courses are in different subaccounts."
            elif has_student_activity:
                warning_text = "The parent course is published and has student activity."
            else:
                warning_text = "The following warnings were detected:"

            message_label = ttk.Label(
                main_frame,
                text=warning_text,
                font=('Arial', 11),
                justify=tk.LEFT
            )
            message_label.pack(pady=(0, 10))

            # Show all warnings
            if not (has_teacher_mismatch or has_subaccount_mismatch or has_student_activity):
                for warning in self.warnings:
                    warning_label = ttk.Label(
                        main_frame,
                        text=f"• {warning}",
                        font=('Arial', 10),
                        foreground="red"
                    )
                    warning_label.pack(anchor=tk.W, padx=(20, 0), pady=2)

            question_label = ttk.Label(
                main_frame,
                text="\nDo you want to proceed with cross-listing anyway?",
                font=('Arial', 11, 'bold')
            )
            question_label.pack(pady=(20, 20))

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X)

        no_btn = ttk.Button(
            button_frame,
            text="No - Cancel",
            command=self.cancel
        )
        no_btn.pack(side=tk.RIGHT, padx=(5, 0))

        yes_btn = ttk.Button(
            button_frame,
            text="Yes - Continue",
            command=self.confirm
        )
        yes_btn.pack(side=tk.RIGHT)

        # Center the dialog
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (600 // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (400 // 2)
        self.dialog.geometry(f"600x400+{x}+{y}")

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


class MultipleChildWarningDialog:
    """Warning dialog for multiple child selection attempts."""

    def __init__(self, parent):
        self.parent = parent
        self.create_dialog()

    def create_dialog(self):
        """Create the multiple child warning dialog."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Multiple Cross-listing Not Allowed")
        self.dialog.geometry("500x200")
        self.dialog.resizable(False, False)

        # Make it modal
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # Main frame
        main_frame = ttk.Frame(self.dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        title_label = ttk.Label(
            main_frame,
            text="Multiple Cross-listing Requires Approval",
            font=('Arial', 14, 'bold'),
            foreground="red"
        )
        title_label.pack(pady=(0, 15))

        message_text = """Cross-listing 2+ courses for a single course requires approval.

Please reach out to the eLC for assistance with multiple course cross-listing."""

        message_label = ttk.Label(
            main_frame,
            text=message_text,
            font=('Arial', 11),
            justify=tk.CENTER
        )
        message_label.pack(pady=(0, 20))

        # OK Button
        ok_btn = ttk.Button(
            main_frame,
            text="OK",
            command=self.dialog.destroy
        )
        ok_btn.pack()

        # Center the dialog
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (500 // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (200 // 2)
        self.dialog.geometry(f"500x200+{x}+{y}")

    def show(self):
        """Show the dialog."""
        self.dialog.wait_window()


class CrosslistingConfirmDialog:
    """Confirmation dialog for cross-listing operations."""

    def __init__(self, parent, parent_section, child_section, validation_errors, dry_run=False):
        self.parent = parent
        self.parent_section = parent_section
        self.child_section = child_section
        self.validation_errors = validation_errors
        self.dry_run = dry_run
        self.result = False

        self.create_dialog()
    
    def create_dialog(self):
        """Create the confirmation dialog."""
        self.dialog = tk.Toplevel(self.parent)
        title = "Confirm Cross-listing (DRY RUN)" if self.dry_run else "Confirm Cross-listing"
        self.dialog.title(title)
        self.dialog.geometry("600x500")
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
        parent_code = self.parent_section.get('course_code', '')
        parent_label = ttk.Label(
            info_frame,
            text=f"Parent: {parent_code}"
        )
        parent_label.pack(anchor=tk.W, padx=10, pady=5)

        # Child info
        child_code = self.child_section.get('course_code', '')
        child_name = self.child_section.get('section_name', '')
        child_label = ttk.Label(
            info_frame,
            text=f"Child: {child_code} Section {child_name}"
        )
        child_label.pack(anchor=tk.W, padx=10, pady=5)

        # Validation status
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(0, 15))

        if not self.validation_errors:
            status_text = "✓ All validation checks passed"
            status_color = "green"
            is_valid = True
        else:
            status_text = "❌ Validation failed:"
            status_color = "red"
            is_valid = False

            status_label = ttk.Label(status_frame, text=status_text, foreground=status_color)
            status_label.pack(anchor=tk.W)

            # Show validation errors
            for error in self.validation_errors:
                error_label = ttk.Label(status_frame, text=f"  • {error}", foreground="red", font=('Arial', 9))
                error_label.pack(anchor=tk.W, padx=(10, 0))

        if is_valid:
            status_label = ttk.Label(status_frame, text=status_text, foreground=status_color)
            status_label.pack(anchor=tk.W)

            # Acknowledgment checkboxes
            ack_frame = ttk.LabelFrame(main_frame, text="Before proceeding, you must acknowledge the following:")
            ack_frame.pack(fill=tk.X, pady=(15, 15))

            self.ack1_var = tk.BooleanVar()
            self.ack2_var = tk.BooleanVar()
            self.ack3_var = tk.BooleanVar()

            ack1_text = "I have approval from my associate dean or director."
            ack1_cb = ttk.Checkbutton(ack_frame, text=ack1_text, variable=self.ack1_var, command=self.update_confirm_button)
            ack1_cb.pack(anchor=tk.W, padx=10, pady=5)

            ack2_text = "I have completed my Concourse syllabus for both courses and understand that I will no longer have access to the child course."
            ack2_cb = ttk.Checkbutton(ack_frame, text=ack2_text, variable=self.ack2_var, command=self.update_confirm_button)
            ack2_cb.pack(anchor=tk.W, padx=10, pady=5)

            ack3_text = "⚠️ Important: Copy your Concourse syllabus template before cross-listing. If you don't, your template will be overwritten and lost."
            ack3_cb = ttk.Checkbutton(ack_frame, text=ack3_text, variable=self.ack3_var, command=self.update_confirm_button)
            ack3_cb.pack(anchor=tk.W, padx=10, pady=5)

            if self.dry_run:
                dry_run_note = ttk.Label(
                    status_frame,
                    text="DRY RUN: No actual changes will be made to Canvas",
                    font=('Arial', 10, 'bold'),
                    foreground='orange'
                )
                dry_run_note.pack(anchor=tk.W, pady=(10, 0))

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
            confirm_text = "Confirm (Dry Run)" if self.dry_run else "Confirm Cross-listing"
            self.confirm_btn = ttk.Button(
                button_frame,
                text=confirm_text,
                command=self.confirm,
                state=tk.DISABLED
            )
            self.confirm_btn.pack(side=tk.RIGHT)

        # Center the dialog
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (600 // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (500 // 2)
        self.dialog.geometry(f"600x500+{x}+{y}")

    def update_confirm_button(self):
        """Update confirm button state based on acknowledgments."""
        if hasattr(self, 'confirm_btn') and hasattr(self, 'ack1_var') and hasattr(self, 'ack2_var') and hasattr(self, 'ack3_var'):
            if self.ack1_var.get() and self.ack2_var.get() and self.ack3_var.get():
                self.confirm_btn.config(state=tk.NORMAL)
            else:
                self.confirm_btn.config(state=tk.DISABLED)
    
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


def validate_course_match(parent_code: str, child_code: str) -> bool:
    """Validate that courses have matching numbers"""
    return extract_course_number(parent_code) == extract_course_number(child_code)


def get_friendly_error_message(error_code: int, message: str) -> str:
    """Map Canvas API errors to friendly messages."""
    if error_code == 401:
        return "Check your Canvas token - authentication failed"
    elif error_code == 403:
        return "You do not have permission for that course"
    elif error_code == 404:
        return "Course or section not found"
    elif error_code in [409, 422]:
        return "Already cross-listed or invalid pair"
    else:
        return f"API Error {error_code}: {message}"


def create_tooltip(widget, text):
    """Create a tooltip for a widget."""
    tooltip = None
    
    def show_tooltip(event):
        nonlocal tooltip
        if tooltip is not None:
            return
        tooltip = tk.Toplevel()
        tooltip.wm_overrideredirect(True)
        tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")

        label = ttk.Label(tooltip, text=text, background="yellow", font=('Arial', 9))
        label.pack()

    def hide_tooltip(event):
        nonlocal tooltip
        if tooltip is not None:
            tooltip.destroy()
            tooltip = None

    widget.bind('<Enter>', show_tooltip)
    widget.bind('<Leave>', hide_tooltip)


class CrosslistingGUI:
    """Main GUI application for Canvas Cross-listing."""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Canvas Cross-Listing Tool (Staff)")
        
        # Set window size and center it on screen
        window_width = 1000
        window_height = 700
        
        # Get screen dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # Calculate position to center the window horizontally, but place it higher than center vertically
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2 - 50  # 50px higher than center
        
        # Set geometry with centered position
        self.root.geometry(f"{window_width}x{window_height}+{x}+{y}")
        self.root.minsize(900, 700)
        
        # Application state
        self._is_closing = False
        self._active_threads = []
        
        # Configuration and data
        self.config = None
        self.token_provider = None
        self.service = None
        self.terms = []
        self.sections = []
        self.ui_rows = []  # UI-formatted rows
        self.permissions_map = {}  # Course permissions
        self.selected_term_id = None
        self.current_instructor = None  # Selected instructor info
        self.cached_sections = {}  # Cache sections by term_id
        self.instructor_dropdown = None
        self.instructor_candidates = []
        self._last_parent_index = None
        self._last_child_index = None
        self._last_undo_index = None

        # GUI variables
        self.selected_term = tk.StringVar()
        self.instructor_search = tk.StringVar()
        self.course_search = tk.StringVar()
        self.published_only = tk.BooleanVar()
        self.staff_mode = tk.BooleanVar()
        self.dry_run = tk.BooleanVar()
        self.bypass_cache = tk.BooleanVar()
        self.as_user_id = None
        self.override_sis_stickiness = tk.BooleanVar(value=True)
        
        # Section selection
        self.parent_var = tk.StringVar()
        self.child_var = tk.StringVar()
        self.selected_children = set()  # Track multiple child selections
        
        # Setup proper cleanup
        self.setup_cleanup_handlers()
        
        # Initialize GUI
        self.create_gui()
        self.load_configuration()
        
        # Initially disable all form elements until term is selected
        self.set_form_state(False)
        
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
        filters_frame = ttk.LabelFrame(main_frame, text="Course Selection Filters")
        filters_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Filter controls
        filter_grid = ttk.Frame(filters_frame)
        filter_grid.pack(fill=tk.X, padx=10, pady=10)

        # Row 1: Instructor input (instructor-first)
        ttk.Label(filter_grid, text="Instructor:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))

        self.instructor_entry = ttk.Entry(
            filter_grid,
            textvariable=self.instructor_search,
            width=40
        )
        self.instructor_entry.grid(row=0, column=1, columnspan=2, sticky=tk.EW, padx=(0, 10))
        # Pressing Enter resolves the instructor
        self.instructor_entry.bind('<Return>', lambda e: self.resolve_instructor_input())

        # Add placeholder text for Instructor
        self.add_placeholder(self.instructor_entry, "Canvas ID, @collin.edu email, SIS ID, or name. Leave blank only if you intend staff mode.")

        self.resolve_btn = ttk.Button(
            filter_grid,
            text="Resolve Instructor",
            command=self.resolve_instructor_input,
            state=tk.DISABLED
        )
        self.resolve_btn.grid(row=0, column=3, padx=(5, 0))

        # Green check indicator for instructor selection
        self.instructor_status = ttk.Label(filter_grid, text="", foreground="green")
        self.instructor_status.grid(row=0, column=4, padx=(5, 0), sticky=tk.W)
        
        # Instructor info display will be moved to above the table

        # Remove typeahead per UX: rely on explicit Resolve -> modal selection

        # Visual separator
        separator = ttk.Separator(filter_grid, orient='horizontal')
        separator.grid(row=1, column=0, columnspan=4, sticky=tk.EW, pady=(10, 5))

        # Row 2: Staff mode toggle and search
        self.staff_toggle = ttk.Checkbutton(
            filter_grid,
            text="Search courses in term (staff mode)",
            variable=self.staff_mode,
            command=self.on_staff_mode_toggle
        )
        self.staff_toggle.grid(row=2, column=0, sticky=tk.W, pady=(5, 0))

        # Search section with better labeling
        search_frame = ttk.Frame(filter_grid)
        search_frame.grid(row=2, column=1, columnspan=2, sticky=tk.EW, padx=(20, 10), pady=(5, 0))
        
        self.search_label = ttk.Label(search_frame, text="Search for courses:", font=('Arial', 9, 'bold'))
        self.search_label.pack(anchor=tk.W)

        self.course_entry = ttk.Entry(
            search_frame,
            textvariable=self.course_search,
            width=25,
            state=tk.DISABLED
        )
        self.course_entry.pack(fill=tk.X, pady=(2, 0))

        # Add placeholder text for Course ID - use the same approach as instructor field
        self.add_placeholder(self.course_entry, "Canvas ID, course code, or subject (e.g. MATH, 1405, BIO)")
        
        # Help text for search
        self.search_help = ttk.Label(
            search_frame,
            text="Requires at least 2 characters. Searches by course code or name. Results depend on your account-level permissions.",
            font=('Arial', 8),
            foreground='gray'
        )
        self.search_help.pack(anchor=tk.W, pady=(2, 0))

        # Row 3: Load button with better context
        load_frame = ttk.Frame(filter_grid)
        load_frame.grid(row=3, column=0, columnspan=4, sticky=tk.EW, pady=(15, 0))

        # Mode indicator
        self.mode_indicator = ttk.Label(
            load_frame, 
            text="Instructor Mode: Loading courses for selected instructor", 
            foreground="blue", 
            font=('Arial', 9, 'italic')
        )
        self.mode_indicator.pack(side=tk.LEFT, padx=(0, 10))

        self.staff_info_label = ttk.Label(load_frame, text="", foreground="blue", font=('Arial', 9))
        self.staff_info_label.pack(side=tk.LEFT, padx=(0, 10))

        self.load_btn = ttk.Button(
            load_frame,
            text="Load Course Sections",
            command=self.load_sections,
            state=tk.DISABLED
        )
        self.load_btn.pack(side=tk.RIGHT)

        # Configure grid weights to give more space to search area
        filter_grid.columnconfigure(1, weight=2)
        filter_grid.columnconfigure(2, weight=1)
        
        # Developer Tools section (collapsible)
        self.dev_tools_frame = ttk.LabelFrame(main_frame, text="Developer Tools (Advanced)")
        self.dev_tools_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Initially hide dev tools
        self.dev_tools_visible = False
        self.dev_tools_frame.pack_forget()
        
        # Dev tools content
        dev_content_frame = ttk.Frame(self.dev_tools_frame)
        dev_content_frame.pack(fill=tk.X, padx=10, pady=10)
        
        dry_run_cb = ttk.Checkbutton(
            dev_content_frame,
            text="Test Run",
            variable=self.dry_run
        )
        dry_run_cb.pack(side=tk.LEFT, padx=(0, 15))
        create_tooltip(dry_run_cb, "Preview changes without actually modifying Canvas")

        bypass_cache_cb = ttk.Checkbutton(
            dev_content_frame,
            text="Bypass cache",
            variable=self.bypass_cache
        )
        bypass_cache_cb.pack(side=tk.LEFT, padx=(0, 15))
        create_tooltip(bypass_cache_cb, "Skip cached data and fetch fresh information from Canvas")

        override_sis_cb = ttk.Checkbutton(
            dev_content_frame,
            text="Override SIS stickiness (staff/admin)",
            variable=self.override_sis_stickiness
        )
        override_sis_cb.pack(side=tk.LEFT, padx=(0, 15))
        create_tooltip(override_sis_cb, "Allow cross-listing even when SIS data would normally prevent it (staff/admin only)")
        
        # About cross-listing link
        self.about_frame = ttk.Frame(main_frame)
        self.about_frame.pack(fill=tk.X, pady=(0, 10))
        
        about_link = ttk.Button(
            self.about_frame,
            text="About Cross-listing",
            command=self.show_about_dialog
        )
        about_link.pack(side=tk.LEFT)
        
        # Toggle dev tools button
        self.toggle_dev_btn = ttk.Button(
            self.about_frame,
            text="Show Developer Tools",
            command=self.toggle_dev_tools
        )
        self.toggle_dev_btn.pack(side=tk.RIGHT)
        
        # Instructor info display above the table
        self.instructor_info_frame = ttk.Frame(main_frame)
        self.instructor_info_frame.pack(fill=tk.X, pady=(0, 5))
        
        self.instructor_info_label = ttk.Label(
            self.instructor_info_frame, 
            text="", 
            foreground="darkblue", 
            font=('Arial', 10, 'bold')
        )
        self.instructor_info_label.pack()
        
        # Sections table frame
        table_frame = ttk.LabelFrame(main_frame, text="Course Sections")
        table_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create treeview for sections
        self.create_sections_table(table_frame)
        
        # Action buttons
        action_frame = ttk.Frame(table_frame)
        action_frame.pack(fill=tk.X, padx=10, pady=10)

        # Left side - main action button
        self.crosslist_btn = ttk.Button(
            action_frame,
            text="Confirm Cross-listing",
            command=self.confirm_crosslisting,
            state=tk.DISABLED
        )
        self.crosslist_btn.pack(side=tk.LEFT, padx=(0, 10))

        # Right side - export buttons
        export_frame = ttk.Frame(action_frame)
        export_frame.pack(side=tk.RIGHT)

        self.export_btn = ttk.Button(
            export_frame,
            text="Export CSV",
            command=self.export_csv,
            state=tk.DISABLED
        )
        self.export_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.export_audit_btn = ttk.Button(
            export_frame,
            text="Export Audit Log (CSV)",
            command=self.export_audit_log,
            state=tk.NORMAL
        )
        self.export_audit_btn.pack(side=tk.LEFT, padx=(0, 10))

        # Load more button for staff mode (initially hidden)
        self.load_more_btn = ttk.Button(
            action_frame,
            text="Load More (may be slow)",
            command=self.load_more_pages,
            state=tk.DISABLED
        )
        # Don't pack initially - will be shown in staff mode
        
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
        
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=8)
        
        # Define headings
        self.tree.heading('parent', text='Parent', command=lambda: self.sort_by_column('parent', False))
        self.tree.heading('child', text='Child', command=lambda: self.sort_by_column('child', False))  
        self.tree.heading('course_title', text='Course Title', command=lambda: self.sort_by_column('course_title', False))
        self.tree.heading('published', text='Published', command=lambda: self.sort_by_column('published', False))
        self.tree.heading('cross_listed', text='Cross-listed', command=lambda: self.sort_by_column('cross_listed', False))
        self.tree.heading('undo', text='Undo', command=lambda: self.sort_by_column('undo', False))
        
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

        # Highlight cross-listed rows
        self.tree.tag_configure('xlisted', background='#fff4e5')
    
    def load_configuration(self):
        """Load Canvas API configuration."""
        try:
            self.config = get_config()
            self.token_provider = EnvTokenProvider()
            self.service = CrosslistingService(self.config, self.token_provider, self.as_user_id)
            self.status_var.set("Configuration loaded successfully")
        except Exception as e:
            messagebox.showerror("Configuration Error", f"Failed to load configuration:\n{str(e)}")
            self.status_var.set("Configuration error")
    
    def load_terms(self):
        """Load enrollment terms from Canvas."""
        if not self.config or not self.token_provider:
            return

        def load_terms_thread():
            try:
                if self._is_closing:
                    return

                self.status_var.set("Loading terms...")
                self.progress.pack(fill=tk.X, pady=5)
                self.progress.start()

                terms = fetch_active_terms(self.config, self.token_provider, use_cache=True)

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

    def show_error_toast(self, message: str):
        """Display a temporary red toast popup with the given message."""
        try:
            toast = tk.Toplevel(self.root)
            toast.overrideredirect(True)
            toast.attributes('-topmost', True)
            frame = ttk.Frame(toast)
            frame.pack(fill=tk.BOTH, expand=True)
            label = ttk.Label(frame, text=message, background='#ffdddd', foreground='red', padding=10)
            label.pack(fill=tk.BOTH, expand=True)
            # Position top-right of main window
            self.root.update_idletasks()
            x = self.root.winfo_rootx() + self.root.winfo_width() - 350
            y = self.root.winfo_rooty() + 50
            toast.geometry(f"300x80+{x}+{y}")
            self.root.after(4000, toast.destroy)
        except Exception:
            pass
    
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
            if not entry_widget.get().strip():
                entry_widget.insert(0, placeholder_text)
                entry_widget.config(foreground=placeholder_fg)
        
        def on_key_press(event):
            # If placeholder is showing and user starts typing, clear it
            if entry_widget.get() == placeholder_text and entry_widget.cget('foreground') == placeholder_fg:
                entry_widget.delete(0, tk.END)
                entry_widget.config(foreground=original_fg)
        
        def on_key_release(event):
            # Check if field becomes empty while typing
            if not entry_widget.get().strip() and entry_widget.cget('foreground') == original_fg:
                entry_widget.insert(0, placeholder_text)
                entry_widget.config(foreground=placeholder_fg)
        
        def on_click(event):
            # If placeholder is showing and user clicks anywhere in the field, clear it
            if entry_widget.get() == placeholder_text and entry_widget.cget('foreground') == placeholder_fg:
                entry_widget.delete(0, tk.END)
                entry_widget.config(foreground=original_fg)
        
        # Set initial placeholder
        entry_widget.insert(0, placeholder_text)
        entry_widget.config(foreground=placeholder_fg)
        
        # Bind events
        entry_widget.bind('<FocusIn>', on_focus_in)
        entry_widget.bind('<FocusOut>', on_focus_out)
        entry_widget.bind('<KeyPress>', on_key_press)
        entry_widget.bind('<KeyRelease>', on_key_release)
        entry_widget.bind('<Button-1>', on_click)
        
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
        # Note: Keep "Default Term" entries so sandbox courses remain visible per SOP/testing needs.
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
            self.status_var.set(f"Term selected: {self.terms[selection]['name']}")
            
            # Enable form elements
            self.set_form_state(True)

            # Clear previous sections and instructor
            self.clear_sections_table()
            self.current_instructor = None
            self.instructor_info_label.config(text="")
            self.update_load_button_state()
        else:
            # No valid term selected
            self.selected_term_id = None
            self.set_form_state(False)
            self.clear_sections_table()

    def on_staff_mode_toggle(self):
        """Handle staff mode toggle."""
        if self.staff_mode.get():
            # Enable staff mode - clear instructor info since we're browsing all courses
            self.current_instructor = None
            self.instructor_info_label.config(text="")
            self.instructor_status.config(text="")
            
            if self.selected_term_id:  # Only enable if term is selected
                self.course_entry.config(state=tk.NORMAL)
                # Ensure placeholder is properly set when field becomes enabled
                if not self.get_entry_value(self.course_entry):
                    self.course_entry.delete(0, tk.END)
                    self.course_entry.insert(0, "Canvas ID, course code, or subject (e.g. MATH, 1405, BIO)")
                    self.course_entry.config(foreground='grey')
            self.mode_indicator.config(
                text="Staff Mode: Searching all courses in term", 
                foreground="green"
            )
            self.staff_info_label.config(text="Page limit: 5 (use 'Load More' for additional pages)")
            self.load_more_btn.pack(side=tk.RIGHT, padx=(10, 0))
        else:
            # Disable staff mode
            self.course_entry.config(state=tk.DISABLED)
            self.mode_indicator.config(
                text="Instructor Mode: Loading courses for selected instructor", 
                foreground="blue"
            )
            self.staff_info_label.config(text="")
            self.load_more_btn.pack_forget()

        self.update_load_button_state()

    def update_load_button_state(self):
        """Update the load button state based on current selections."""
        if not self.selected_term_id:
            self.load_btn.config(state=tk.DISABLED)
            return

        # Instructor mode: need instructor resolved
        if not self.staff_mode.get():
            if self.current_instructor:
                self.load_btn.config(state=tk.NORMAL)
            else:
                self.load_btn.config(state=tk.DISABLED)
        else:
            # Staff mode: need search term
            search_val = self.get_entry_value(self.course_entry).strip()
            if search_val:
                self.load_btn.config(state=tk.NORMAL)
            else:
                self.load_btn.config(state=tk.DISABLED)

    def resolve_instructor_input(self):
        """Resolve instructor from input."""
        if not self.selected_term_id:
            messagebox.showwarning("No Term", "Please select a term first.")
            return

        instructor_input = self.get_entry_value(self.instructor_entry).strip()
        if not instructor_input:
            messagebox.showwarning("No Input", "Please enter an instructor identifier.")
            return

        # Validate email format if it contains @
        if "@" in instructor_input and not instructor_input.lower().endswith("@collin.edu"):
            result = messagebox.askyesno(
                "Email Format",
                f"Email should end with @collin.edu.\n\nYou entered: {instructor_input}\n\nProceed anyway?"
            )
            if not result:
                return

        def resolve_thread():
            try:
                if self._is_closing:
                    return

                self.root.after(0, lambda: self.status_var.set(f"Resolving instructor '{instructor_input}'..."))
                self.root.after(0, lambda: self.progress.pack(fill=tk.X, pady=5))
                self.root.after(0, self.progress.start)

                resolution = resolve_instructor(self.config, self.selected_term_id, instructor_input, self.token_provider)

                if not self._is_closing:
                    self.root.after(0, self.handle_instructor_resolution, resolution)

            except Exception as e:
                if not self._is_closing:
                    self.root.after(0, self.handle_error, "Failed to resolve instructor", str(e))
            finally:
                if not self._is_closing:
                    self.root.after(0, self.hide_progress)

        self.start_thread(resolve_thread, "ResolveInstructor")

    # Typeahead removed

    # Dropdown-based typeahead removed

    # hide_instructor_dropdown removed (no-op)

    # on_dropdown_select removed (no-op)

    def set_current_instructor(self, instructor):
        self.current_instructor = instructor
        name = instructor.get('name', '')
        email = instructor.get('email', '')
        canvas_id = instructor.get('id', '')
        login_id = instructor.get('login_id', '')
        
        self.instructor_status.config(text="✔")
        self.status_var.set(f"Instructor selected: {name} ({email})")
        
        # Display detailed instructor information
        info_text = f"Current Instructor: {name}"
        if login_id:
            info_text += f" | Login: {login_id}"
        if canvas_id:
            info_text += f" | Canvas ID: {canvas_id}"
        if email:
            info_text += f" | Email: {email}"
        
        self.instructor_info_label.config(text=info_text)
        
        # Reflect selection in entry
        try:
            self.instructor_entry.delete(0, tk.END)
            self.instructor_entry.insert(0, name or str(canvas_id))
        except Exception:
            pass
        self.update_load_button_state()

    def handle_instructor_resolution(self, resolution):
        """Handle instructor resolution result."""
        candidates = resolution.get('candidates', [])
        raw_matches = resolution.get('raw_matches', None)

        if raw_matches == 0:
            # No user matched at all
            messagebox.showerror("No Match", "No instructor found with that ID/email/name.")
            self.status_var.set("No instructor found with that ID/email/name.")
            self.current_instructor = None
        elif not candidates and (raw_matches is None or raw_matches > 0):
            # Found user(s) but none with active enrollments in term
            messagebox.showerror("No Active Courses", "Instructor found, but no active courses in this term.")
            self.status_var.set("Instructor found, but no active courses in this term.")
            self.current_instructor = None
        elif len(candidates) == 1:
            self.current_instructor = candidates[0]
            self.set_current_instructor(self.current_instructor)
            # Auto-load sections after successful resolution
            self.update_load_button_state()
            self.load_sections()
        else:
            # Multiple candidates - show selection dialog
            dialog = InstructorSelectionDialog(self.root, candidates)
            selected = dialog.show()

            if selected:
                self.current_instructor = selected
                self.set_current_instructor(self.current_instructor)
                # Auto-load after selection
                self.update_load_button_state()
                self.load_sections()
            else:
                self.current_instructor = None
                self.instructor_info_label.config(text="")
                self.status_var.set("Instructor selection cancelled")

        self.update_load_button_state()
    
    def load_sections(self):
        """Load sections based on current mode (instructor or staff)."""
        if not self.selected_term_id:
            return

        # Disable load during fetch
        self.load_btn.config(state=tk.DISABLED)

        def load_sections_thread():
            try:
                if self._is_closing:
                    return

                # Show loading state
                self.root.after(0, lambda: self.status_var.set("Loading sections..."))
                self.root.after(0, lambda: self.progress.pack(fill=tk.X, pady=5))
                self.root.after(0, self.progress.start)

                if self.staff_mode.get():
                    # Staff mode
                    search_term = self.get_entry_value(self.course_entry).strip()
                    if not search_term:
                        raise ValueError("Search term required for staff mode")

                    courses = list_account_courses_filtered(
                        self.config, self.token_provider, self.selected_term_id,
                        search_term=search_term, staff_max_pages=5
                    )
                    sections = list_sections_for_courses(self.config, self.token_provider, courses)
                else:
                    # Instructor mode
                    if not self.current_instructor:
                        raise ValueError("No instructor selected")

                    courses = list_user_term_courses_via_enrollments(
                        self.config, self.token_provider, self.current_instructor['id'], self.selected_term_id
                    )
                    sections = list_sections_for_courses(self.config, self.token_provider, courses)

                # Check permissions for potential parent courses
                # SOP: a parent can be unpublished OR published with zero students. Include both in checks.
                course_ids = list(set(
                    s['course_id'] for s in sections
                    if (not s.get('published')) or (s.get('total_students', 0) == 0)
                ))
                permissions_map = check_course_permissions(self.config, self.token_provider, course_ids) if course_ids else {}

                # Format for UI
                ui_rows = format_sections_for_ui(sections, permissions_map)

                if not self._is_closing:
                    self.root.after(0, self.update_sections_display, sections, ui_rows, permissions_map)

            except Exception as e:
                if not self._is_closing:
                    self.root.after(0, self.handle_error, "Failed to load sections", str(e))
            finally:
                if not self._is_closing:
                    self.root.after(0, self.hide_progress)
                    self.root.after(0, self.update_load_button_state)

        self.start_thread(load_sections_thread, "LoadSections")

    def load_more_pages(self):
        """Load more pages in staff mode."""
        # Implementation for loading more pages with higher limit
        messagebox.showinfo("Load More", "This may be slow. Increasing page limit to 10...")
        # Similar to load_sections but with higher page limit

    def update_sections_display(self, sections, ui_rows, permissions_map):
        """Update the sections display with new data."""
        self.sections = sections
        self.ui_rows = ui_rows
        self.permissions_map = permissions_map
        self.populate_sections_table()
        self.export_btn.config(state=tk.NORMAL)
        self.status_var.set(f"Loaded {len(sections)} sections")

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
        """Populate the sections table using UI-formatted rows."""
        # Clear existing items
        self.clear_sections_table()

        if not self.sections:
            # Show placeholder row with appropriate message based on mode
            if self.staff_mode.get():
                placeholder = "No results found for this search term. Try broadening your search or check your account permissions."
            else:
                placeholder = "No courses found for this instructor in the selected term."
            self.tree.insert('', 'end', values=('', '', placeholder, '', '', ''))
            self.status_var.set("No sections found")
            return

        # Group sections by course number for validation
        course_groups = {}
        for section in self.sections:
            course_num = extract_course_number(section.get('course_code', ''))
            if course_num not in course_groups:
                course_groups[course_num] = []
            course_groups[course_num].append(section)

        # Add sections to tree using UI rows
        for i, section in enumerate(self.sections):
            ui_row = self.ui_rows[i] if i < len(self.ui_rows) else {}

            # Determine radio states and permissions
            can_be_parent = ui_row.get('parent_candidate', False)
            can_be_child = ui_row.get('child_candidate', False)
            permission_block = ui_row.get('permission_block')

            # Create parent radio (disabled if permission blocked)
            parent_radio = '○' if can_be_parent and not permission_block else ''
            if permission_block:
                parent_radio = '🚫'  # Show blocked icon

            # Create item
            item_id = self.tree.insert('', 'end', iid=str(i), values=(
                parent_radio,  # Parent radio
                '○' if can_be_child else '',   # Child radio
                ui_row.get('course', section.get('full_title', '')),
                ui_row.get('published', 'No'),
                ui_row.get('cross_listed', 'No'),
                'Undo' if ui_row.get('undo_allowed', False) else ''
            ))

            # Tag cross-listed rows for highlight
            if section.get('cross_listed'):
                self.tree.item(item_id, tags=('xlisted',))

            # Add visual indicators
            if section.get('published'):
                if section.get('cross_listed'):
                    self.tree.set(item_id, 'published', '🟢 Yes')
                    self.tree.set(item_id, 'cross_listed', '🔗 Yes')
                else:
                    self.tree.set(item_id, 'published', '🟢 Yes')
                    self.tree.set(item_id, 'cross_listed', '❌ No')
            else:
                if not section.get('cross_listed'):
                    self.tree.set(item_id, 'published', '🔴 No')
                    self.tree.set(item_id, 'cross_listed', '❌ No')

            # Store permission info for tooltips
            if permission_block:
                # Create tooltip would go here if implemented
                pass
    
    def clear_sections_table(self):
        """Clear the sections table."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Reset selections
        self.parent_var.set('')
        self.child_var.set('')
        self.selected_children.clear()
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
            ui_row = self.ui_rows[section_index] if section_index < len(self.ui_rows) else {}
            permission_block = ui_row.get('permission_block')

            if permission_block:
                messagebox.showwarning("Permission Denied", permission_block)
                return

            if ui_row.get('parent_candidate', False):
                self.select_parent(section_index)
        
        # Handle Child column clicks
        elif column == '#2':  # Child column
            ui_row = self.ui_rows[section_index] if section_index < len(self.ui_rows) else {}
            if ui_row.get('child_candidate', False):
                self.select_child(section_index)
        
        # Handle Undo column clicks
        elif column == '#6':  # Undo column
            ui_row = self.ui_rows[section_index] if section_index < len(self.ui_rows) else {}
            if ui_row.get('undo_allowed', False):
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
                ui_row = self.ui_rows[i] if i < len(self.ui_rows) else {}
                permission_block = ui_row.get('permission_block')

                if permission_block:
                    self.tree.set(item, 'parent', '🚫')
                elif ui_row.get('parent_candidate', False):
                    self.tree.set(item, 'parent', '○')
                else:
                    self.tree.set(item, 'parent', '')

        # Reset child selection when parent changes
        self.child_var.set('')
        self.selected_children.clear()

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

        # Check for multiple child selection attempt
        if section_index in self.selected_children:
            # Deselect if already selected
            self.selected_children.remove(section_index)
        else:
            # Check if this would be a second child
            if len(self.selected_children) >= 1:
                dialog = MultipleChildWarningDialog(self.root)
                dialog.show()
                return

            # Add to selected children
            self.selected_children.add(section_index)

        # Validate course match using course prefix comparison
        parent_section = self.sections[parent_index]
        parent_code = parent_section.get('course_code', '')
        child_code = child_section.get('course_code', '')
        parent_prefix = get_course_prefix(parent_code)
        child_prefix = get_course_prefix(child_code)

        # Only warn if different prefixes (not same prefix like MATH vs MATH)
        if parent_prefix != child_prefix:
            # This will be handled by the warning system, but we can proceed with selection
            pass

        # Update child selection display
        for i, item in enumerate(self.tree.get_children()):
            if i in self.selected_children:
                self.tree.set(item, 'child', '●')
            else:
                # Show valid child options
                ui_row = self.ui_rows[i] if i < len(self.ui_rows) else {}
                parent_course_prefix = get_course_prefix(parent_section.get('course_code', ''))
                section_course_prefix = get_course_prefix(self.sections[i].get('course_code', ''))

                if (ui_row.get('child_candidate', False) and
                    section_course_prefix == parent_course_prefix and i != parent_index):
                    self.tree.set(item, 'child', '○')
                else:
                    self.tree.set(item, 'child', '')

        # Update child_var for backward compatibility (use first selected child)
        if self.selected_children:
            self.child_var.set(str(list(self.selected_children)[0]))
        else:
            self.child_var.set('')

        self.update_button_states()
    
    def update_child_options(self):
        """Update available child options based on parent selection and course prefix matching."""
        parent_index = self.get_parent_index()

        if parent_index is not None:
            parent_section = self.sections[parent_index]
            parent_course_prefix = get_course_prefix(parent_section.get('course_code', ''))

            for i, item in enumerate(self.tree.get_children()):
                if i == parent_index:
                    # Don't show child option for parent
                    self.tree.set(item, 'child', '')
                elif i in self.selected_children:
                    # Keep selected children marked
                    self.tree.set(item, 'child', '●')
                else:
                    ui_row = self.ui_rows[i] if i < len(self.ui_rows) else {}
                    section_course_prefix = get_course_prefix(self.sections[i].get('course_code', ''))

                    # Can be child if: child candidate and course prefixes match
                    can_be_child = ui_row.get('child_candidate', False)
                    course_prefixes_match = section_course_prefix == parent_course_prefix

                    if can_be_child and course_prefixes_match:
                        self.tree.set(item, 'child', '○')
                    else:
                        self.tree.set(item, 'child', '')
        else:
            # No parent selected, show all potential children
            for i, item in enumerate(self.tree.get_children()):
                if i in self.selected_children:
                    self.tree.set(item, 'child', '●')
                else:
                    ui_row = self.ui_rows[i] if i < len(self.ui_rows) else {}
                    if ui_row.get('child_candidate', False):
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

        # Enable crosslist button if parent is selected and exactly one child is selected
        if parent_index is not None and len(self.selected_children) == 1:
            self.crosslist_btn.config(state=tk.NORMAL)
        else:
            self.crosslist_btn.config(state=tk.DISABLED)

    def export_csv(self):
        """Export sections to CSV file."""
        if not self.sections:
            messagebox.showwarning("No Data", "No sections to export.")
            return

        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        term_name = "unknown_term"
        if self.selected_term_id:
            for term in self.terms:
                if term['id'] == self.selected_term_id:
                    term_name = term['name'].replace(' ', '_')
                    break

        default_filename = f"crosslist_{self.selected_term_id}_{timestamp}.csv"

        # Ask user for save location
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialvalue=default_filename,
            title="Export Sections to CSV"
        )

        if not filename:
            return

        try:
            # Get term info for export
            term_info = None
            if self.selected_term_id:
                for term in self.terms:
                    if term['id'] == self.selected_term_id:
                        term_info = term
                        break

            export_sections_to_csv(self.sections, term_info, filename)
            messagebox.showinfo("Export Complete", f"Sections exported to:\n{filename}")

        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to export CSV:\n{str(e)}")
    
    def confirm_crosslisting(self):
        """Perform cross-listing operation."""
        parent_index = self.get_parent_index()
        child_index = self.get_child_index()
        
        if parent_index is None or child_index is None:
            messagebox.showwarning("Selection Error", "Please select both parent and child sections.")
            return
        
        parent_section = self.sections[parent_index]
        child_section = self.sections[child_index]
        
        # Pre-move authoritative check to block no-ops/already moved
        try:
            pre_child = get_section(self.config, self.token_provider, child_section['section_id'], self.as_user_id)
            if pre_child.get('course_id') == parent_section['course_id']:
                messagebox.showinfo("No-op", "This section already belongs to the selected parent course.")
                return
            if pre_child.get('nonxlist_course_id') is not None and pre_child.get('nonxlist_course_id') != pre_child.get('course_id'):
                messagebox.showerror(
                    "Already Cross-listed",
                    f"This section is already cross-listed (original course {pre_child.get('nonxlist_course_id')})."
                )
                return
        except Exception:
            # If the pre-check fails, proceed to validation to show errors later
            pass

        # Validate the cross-listing using new validation function
        errors, warnings = validate_cross_listing_candidates(self.config, parent_section, child_section)

        if errors:
            # Show blocking errors
            message = "\n".join(errors)
            if not message:
                message = "Validation failed - please check course requirements"
            messagebox.showerror("Validation Failed", message)
            return

        # Handle warnings with modal confirmation
        if warnings:
            warning_dialog = WarningConfirmDialog(self.root, warnings, parent_section, child_section)
            if not warning_dialog.show():
                return  # User cancelled after seeing warnings

        # Show final confirmation dialog (warnings already handled)
        dialog = CrosslistingConfirmDialog(
            self.root,
            parent_section,
            child_section,
            [],  # No validation errors at this point (handled above)
            self.dry_run.get()
        )

        if not dialog.show():
            return
        
        # Perform the cross-listing
        self._last_parent_index = parent_index
        self._last_child_index = child_index
        def crosslist_thread():
            try:
                if self._is_closing:
                    return
                    
                self.root.after(0, lambda: self.status_var.set("Cross-listing sections..."))
                self.root.after(0, lambda: self.progress.pack(fill=tk.X, pady=5))
                self.root.after(0, self.progress.start)
                
                # Get instructor and term info for audit
                instructor_id = self.current_instructor['id'] if self.current_instructor else None

                success = cross_list_section(
                    self.config, self.token_provider,
                    child_section['section_id'],
                    parent_section['course_id'],
                    dry_run=self.dry_run.get(),
                    term_id=self.selected_term_id,
                    instructor_id=instructor_id,
                    as_user_id=self.as_user_id,
                    override_sis_stickiness=self.override_sis_stickiness.get()
                )

                details_text = ""
                if not self.dry_run.get() and success:
                    try:
                        summary = summarize_crosslist_changes(self.config, self.token_provider, parent_section['course_id'], self.as_user_id)
                        new_title = summary.get('parent_course_name')
                        children = summary.get('children', [])
                        if new_title:
                            details_text += f"\nNew Course Title: {new_title}"
                        if children:
                            details_text += "\nChild Courses:" + "\n" + "\n".join([f"  • {code}: {name}" for code, name in children])
                    except Exception:
                        pass

                if self.dry_run.get():
                    message = f"DRY RUN: Would cross-list section {child_section['section_id']} into course {parent_section['course_id']}"
                else:
                    base_msg = f"Successfully cross-listed section {child_section['section_id']} into course {parent_section['course_id']}"
                    message = base_msg + (details_text if details_text else "")
                
                if not self._is_closing:
                    self.root.after(0, self.handle_crosslist_result, success, message, self.dry_run.get())
                
            except Exception as e:
                if not self._is_closing:
                    self.root.after(0, self.handle_error, "Cross-listing failed", str(e))
            finally:
                if not self._is_closing:
                    self.root.after(0, self.hide_progress)
        
        self.start_thread(crosslist_thread, "Crosslist")
    
    def handle_crosslist_result(self, success, message, was_dry_run):
        """Handle the result of cross-listing operation."""
        if success:
            if was_dry_run:
                # Show green banner for dry run
                self.show_success_banner(message)
                self.status_var.set("Dry run completed - check audit log")
            else:
                messagebox.showinfo("Success", message)
                self.status_var.set("Cross-listing completed successfully")
                # Immediate UI update without full reload
                try:
                    self.apply_crosslist_ui_update(self._last_parent_index, self._last_child_index)
                except Exception:
                    pass
                # Also kick off a refresh to fully sync
                self.refresh_sections()
        else:
            # Map error to friendly message if it's a CanvasAPIError
            try:
                if hasattr(message, 'status_code'):
                    friendly_msg = get_friendly_error_message(message.status_code, str(message))
                    messagebox.showerror("Error", friendly_msg)
                else:
                    messagebox.showerror("Error", message)
            except:
                messagebox.showerror("Error", str(message))
            self.status_var.set("Cross-listing failed")

    def show_success_banner(self, message):
        """Show a green success banner for dry run results."""
        # Create a temporary banner at the top
        banner = ttk.Frame(self.root)
        banner.pack(fill=tk.X, after=self.root.winfo_children()[0])  # After title

        banner_label = ttk.Label(
            banner,
            text=f"✓ {message}",
            background="lightgreen",
            foreground="darkgreen",
            font=('Arial', 10, 'bold'),
            padding=10
        )
        banner_label.pack(fill=tk.X)

        # Auto-hide after 5 seconds
        self.root.after(5000, banner.destroy)
    
    # Remove undo_crosslisting method - undo is now handled directly via table clicks
    
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
        
        self._last_undo_index = section_index
        def undo_thread():
            try:
                if self._is_closing:
                    return
                    
                self.root.after(0, lambda: self.status_var.set("Undoing cross-listing..."))
                self.root.after(0, lambda: self.progress.pack(fill=tk.X, pady=5))
                self.root.after(0, self.progress.start)
                
                instructor_id = self.current_instructor['id'] if self.current_instructor else None

                success = un_cross_list_section(
                    self.config, self.token_provider,
                    section['section_id'],
                    dry_run=self.dry_run.get(),
                    term_id=self.selected_term_id,
                    instructor_id=instructor_id,
                    as_user_id=self.as_user_id,
                    override_sis_stickiness=True
                )

                if self.dry_run.get():
                    message = f"DRY RUN: Would un-cross-list section {section['section_id']}"
                else:
                    message = f"Successfully un-cross-listed section {section['section_id']}"
                
                if not self._is_closing:
                    self.root.after(0, self.handle_undo_result, success, message, self.dry_run.get())
                
            except Exception as e:
                if not self._is_closing:
                    self.root.after(0, self.handle_error, "Undo failed", str(e))
            finally:
                if not self._is_closing:
                    self.root.after(0, self.hide_progress)
        
        self.start_thread(undo_thread, "UndoCrosslist")
    
    def handle_undo_result(self, success, message, was_dry_run):
        """Handle the result of undo operation."""
        if success:
            if was_dry_run:
                self.show_success_banner(message)
                self.status_var.set("Undo dry run completed - check audit log")
            else:
                messagebox.showinfo("Success", message)
                self.status_var.set("Undo completed successfully")
                # Immediate UI update, then refresh
                try:
                    self.apply_undo_ui_update(self._last_undo_index)
                except Exception:
                    pass
                self.refresh_sections()
        else:
            try:
                if hasattr(message, 'status_code'):
                    friendly_msg = get_friendly_error_message(message.status_code, str(message))
                    messagebox.showerror("Error", friendly_msg)
                else:
                    messagebox.showerror("Error", message)
            except:
                messagebox.showerror("Error", str(message))
            self.status_var.set("Undo failed")
    
    def refresh_sections(self):
        """Refresh the sections table by reloading data."""
        # Clear cache for current term if bypass cache is enabled
        if self.bypass_cache.get():
            self.cached_sections.clear()

        # Reload sections
        self.load_sections()

    def sort_by_column(self, col, reverse: bool):
        """Sort treeview by a given column."""
        try:
            data = [(self.tree.set(k, col), k) for k in self.tree.get_children('')]
            # Custom sort for icon columns
            if col in ('published', 'cross_listed'):
                def keyfunc(item):
                    v = item[0]
                    return 1 if ('Yes' in v) else 0
                data.sort(key=keyfunc, reverse=reverse)
            elif col in ('parent', 'child', 'undo'):
                data.sort(key=lambda t: t[0], reverse=reverse)
            else:
                data.sort(key=lambda t: t[0].lower() if isinstance(t[0], str) else t[0], reverse=reverse)
            for index, (val, k) in enumerate(data):
                self.tree.move(k, '', index)
            # Toggle sort next time
            self.tree.heading(col, command=lambda: self.sort_by_column(col, not reverse))
        except Exception:
            pass

    def apply_crosslist_ui_update(self, parent_index: int, child_index: int):
        """Update the two affected rows to reflect cross-listing without full reload."""
        if parent_index is None or child_index is None:
            return
        # Mark child as cross-listed and enable Undo
        try:
            child_item = str(child_index)
            self.tree.set(child_item, 'cross_listed', '🔗 Yes')
            self.tree.set(child_item, 'undo', 'Undo')
            self.tree.item(child_item, tags=('xlisted',))
            # Clear child radio options
            self.tree.set(child_item, 'child', '')
        except Exception:
            pass
        # Reset selections
        self.parent_var.set('')
        self.child_var.set('')
        self.selected_children.clear()
        self.update_button_states()

    def apply_undo_ui_update(self, section_index: int):
        """Update a row to reflect un-cross-listing without full reload."""
        try:
            item = str(section_index)
            self.tree.set(item, 'cross_listed', '❌ No')
            self.tree.set(item, 'undo', '')
            self.tree.item(item, tags=())
        except Exception:
            pass
    
    def toggle_dev_tools(self):
        """Toggle the visibility of developer tools section."""
        if self.dev_tools_visible:
            self.dev_tools_frame.pack_forget()
            self.toggle_dev_btn.config(text="Show Developer Tools")
            self.dev_tools_visible = False
        else:
            # Pack dev tools before the about frame
            self.dev_tools_frame.pack(fill=tk.X, pady=(0, 10), before=self.about_frame)
            self.toggle_dev_btn.config(text="Hide Developer Tools")
            self.dev_tools_visible = True
    
    def set_form_state(self, enabled):
        """Enable or disable form elements based on term selection."""
        state = tk.NORMAL if enabled else tk.DISABLED
        
        # Enable/disable form elements
        self.instructor_entry.config(state=state)
        self.resolve_btn.config(state=state if enabled else tk.DISABLED)
        self.staff_toggle.config(state=state)
        
        # Handle course entry state and placeholder
        if enabled and self.staff_mode.get():
            self.course_entry.config(state=tk.NORMAL)
            # Ensure placeholder is properly set when field becomes enabled
            if not self.get_entry_value(self.course_entry):
                self.course_entry.delete(0, tk.END)
                self.course_entry.insert(0, "Canvas ID, course code, or subject (e.g. MATH, 1405, BIO)")
                self.course_entry.config(foreground='grey')
        else:
            self.course_entry.config(state=tk.DISABLED)
            
        self.load_btn.config(state=state if enabled else tk.DISABLED)
        
        # Update load button state based on current selections
        if enabled:
            self.update_load_button_state()
    
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
        self.show_error_toast(message)
        self.status_var.set(f"Error: {title}")

    def export_audit_log(self):
        """Export the audit log CSV (logs/crosslist_audit.csv) to a chosen location."""
        try:
            audit_path = Path('./logs/crosslist_audit.csv')
            if not audit_path.exists():
                messagebox.showwarning("No Audit Log", "No audit log file found yet.")
                return
            default_name = f"crosslist_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            filename = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialvalue=default_name,
                title="Export Audit Log"
            )
            if not filename:
                return
            shutil.copyfile(str(audit_path), filename)
            messagebox.showinfo("Export Complete", f"Audit log exported to:\n{filename}")
        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to export audit log:\n{str(e)}")
    
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