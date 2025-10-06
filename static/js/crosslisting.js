// Cross-listing Selection Logic

document.addEventListener('DOMContentLoaded', function() {
    const parentRadios = document.querySelectorAll('.parent-radio');
    const childCheckboxes = document.querySelectorAll('.child-checkbox');
    const submitBtn = document.getElementById('submitBtn');
    const tableBody = document.getElementById('coursesTableBody');
    const summaryParent = document.getElementById('summaryParent');
    const summaryChild = document.getElementById('summaryChild');

    let selectedParentCourseId = null;
    let selectedParentName = null;
    let selectedChildSections = [];

    // Store global state for re-rendering after operations
    window.coursesState = null;

    // Parent selection handler with de-selection support
    parentRadios.forEach(radio => {
        radio.addEventListener('click', function(e) {
            // If clicking the already-selected parent, allow de-selection
            if (this.checked && selectedParentCourseId === this.value) {
                this.checked = false;

                // Clear parent selection
                selectedParentCourseId = null;
                selectedParentName = null;

                // Remove parent highlighting
                document.querySelectorAll('.parent-selected').forEach(row => {
                    row.classList.remove('parent-selected');
                });

                // Clear and disable all child selections
                childCheckboxes.forEach(cb => {
                    cb.checked = false;
                    cb.disabled = true;
                });

                // Remove child highlighting
                document.querySelectorAll('.child-selected').forEach(row => {
                    row.classList.remove('child-selected');
                });

                selectedChildSections = [];

                // Update child availability (will disable all children)
                updateChildAvailability();

                // Update summary and button
                updateSummary();
                updateSubmitButton();

                return;
            }

            // Normal parent selection
            if (this.checked) {
                // Remove previous parent selection highlighting
                document.querySelectorAll('.parent-selected').forEach(row => {
                    row.classList.remove('parent-selected');
                });

                // Get parent info
                const row = this.closest('tr');
                selectedParentCourseId = this.value;
                selectedParentName = row.querySelector('td:nth-child(3) strong').textContent + ' - ' +
                                    row.querySelector('td:nth-child(4)').textContent;

                // Highlight selected parent row
                row.classList.add('parent-selected');

                // Clear any previous child selections (parent changed)
                childCheckboxes.forEach(cb => cb.checked = false);
                document.querySelectorAll('.child-selected').forEach(row => {
                    row.classList.remove('child-selected');
                });
                selectedChildSections = [];

                // Update child availability
                updateChildAvailability();

                // Update summary and button
                updateSummary();
                updateSubmitButton();
            }
        });
    });

    // Child selection handler - enforce single selection
    childCheckboxes.forEach(checkbox => {
        checkbox.addEventListener('change', function() {
            // Remove previous child selection highlighting
            document.querySelectorAll('.child-selected').forEach(row => {
                row.classList.remove('child-selected');
            });

            if (this.checked) {
                // Uncheck all other child checkboxes (single selection)
                childCheckboxes.forEach(cb => {
                    if (cb !== this) {
                        cb.checked = false;
                    }
                });

                // Highlight selected child row
                const row = this.closest('tr');
                row.classList.add('child-selected');

                // Get child info
                const sectionName = row.querySelector('td:nth-child(3) strong').textContent + ' - ' +
                                   row.querySelector('td:nth-child(4)').textContent + ' (' +
                                   row.querySelector('td:nth-child(5)').textContent + ')';
                selectedChildSections = [sectionName];
            } else {
                // No child selected
                selectedChildSections = [];
            }

            updateSummary();
            updateSubmitButton();
        });
    });

    function updateChildAvailability() {
        if (!selectedParentCourseId) {
            // No parent selected - disable all child checkboxes
            childCheckboxes.forEach(cb => {
                const row = cb.closest('tr');
                const published = row.dataset.published;
                const crossListed = row.dataset.crossListed;

                // Only disable if originally valid (published and not cross-listed)
                if (published === 'Yes' && crossListed !== 'Yes') {
                    cb.disabled = true;
                    cb.title = "Select a parent course first";
                }
            });
        } else {
            // Parent selected - enable eligible children
            const rows = tableBody.querySelectorAll('tr');
            rows.forEach(row => {
                const rowCourseId = row.dataset.courseId;
                const published = row.dataset.published;
                const crossListed = row.dataset.crossListed;
                const childCheckbox = row.querySelector('.child-checkbox');

                if (childCheckbox) {
                    // Check if this child was originally disabled
                    const wasOriginallyDisabled = (published !== 'Yes') || (crossListed === 'Yes');

                    if (wasOriginallyDisabled) {
                        // Keep originally disabled checkboxes disabled
                        childCheckbox.disabled = true;
                    } else {
                        // Enable if different from parent course
                        const shouldEnable = (rowCourseId !== selectedParentCourseId);

                        if (shouldEnable) {
                            childCheckbox.disabled = false;
                            childCheckbox.title = "Select as child section";
                            // Add ARIA label
                            childCheckbox.setAttribute('aria-label', 'Select as child section');
                        } else {
                            childCheckbox.disabled = true;
                            childCheckbox.checked = false;
                            childCheckbox.title = "Cannot select same course as parent";
                            row.classList.remove('child-selected');
                        }
                    }
                }
            });

            // Clear child selections if they became invalid
            const checkedChild = Array.from(childCheckboxes).find(cb => cb.checked);
            if (checkedChild) {
                const checkedRow = checkedChild.closest('tr');
                if (checkedRow.dataset.courseId === selectedParentCourseId) {
                    checkedChild.checked = false;
                    checkedRow.classList.remove('child-selected');
                    selectedChildSections = [];
                }
            }
        }
    }

    function updateSummary() {
        // Update parent summary
        if (selectedParentName) {
            summaryParent.innerHTML = `<span class="value">${selectedParentName}</span>`;
        } else {
            summaryParent.innerHTML = '<span class="none">None selected</span>';
        }

        // Update child summary
        if (selectedChildSections.length > 0) {
            summaryChild.innerHTML = `<span class="value">${selectedChildSections.join(', ')}</span>`;
        } else {
            summaryChild.innerHTML = '<span class="none">None selected</span>';
        }
    }

    function updateSubmitButton() {
        const parentSelected = selectedParentCourseId !== null;
        const childSelected = selectedChildSections.length > 0;

        if (parentSelected && childSelected) {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Validate & Confirm Cross-listing';
            submitBtn.className = 'btn btn-danger btn-lg px-5';
        } else {
            submitBtn.disabled = true;
            submitBtn.textContent = 'Select Parent and Child to Continue';
            submitBtn.className = 'btn btn-secondary btn-lg px-5';
        }
    }

    // Add ARIA labels to parent radios
    parentRadios.forEach(radio => {
        radio.setAttribute('aria-label', 'Select as parent course');
    });

    // Keyboard navigation support
    const allInputs = [...parentRadios, ...childCheckboxes];
    allInputs.forEach((input, index) => {
        input.addEventListener('keydown', function(e) {
            if (e.key === 'ArrowDown' && allInputs[index + 1]) {
                e.preventDefault();
                allInputs[index + 1].focus();
            } else if (e.key === 'ArrowUp' && allInputs[index - 1]) {
                e.preventDefault();
                allInputs[index - 1].focus();
            }
        });
    });

    // Initial state
    updateChildAvailability();
    updateSummary();
    updateSubmitButton();
});

// Function to render courses table from state
function renderCoursesTable(coursesState) {
    const tableBody = document.getElementById('coursesTableBody');
    if (!tableBody) {
        console.error('Table body not found');
        return;
    }

    // Clear existing rows
    tableBody.innerHTML = '';

    // Get term ID and user ID from hidden inputs
    const termId = document.getElementById('termId').value;
    const instructorId = document.querySelector('input[name="instructor_id"]').value;

    // Render each section
    coursesState.sections.forEach(section => {
        const row = document.createElement('tr');
        row.dataset.sectionId = section.section_id;
        row.dataset.published = section.published;
        row.dataset.crossListed = section.cross_listed;
        row.dataset.courseId = section.course_id;

        const isPublished = section.published === 'Yes';
        const isCrossListed = section.cross_listed === 'Yes';

        row.innerHTML = `
            <td class="text-center">
                <input type="radio" name="parent_course_id" value="${section.course_id}"
                       class="form-check-input parent-radio"
                       ${isPublished ? 'disabled title="Cannot select published course as parent"' : ''}
                       aria-label="Select ${section.course_code} as parent">
            </td>
            <td class="text-center">
                <input type="checkbox" name="child_section_id" value="${section.section_id}"
                       class="form-check-input child-checkbox"
                       ${!isPublished ? 'disabled title="Cannot select unpublished section as child"' : ''}
                       ${isCrossListed ? 'disabled title="Section already cross-listed"' : ''}
                       aria-label="Select ${section.course_code} as child">
            </td>
            <td><strong>${section.course_code}</strong></td>
            <td>${section.course_name}</td>
            <td><small class="text-muted">${section.section_name}</small></td>
            <td>
                ${isPublished ? 
                    '<span class="badge published-yes">Published</span>' : 
                    '<span class="badge published-no">Unpublished</span>'}
            </td>
            <td>${section.total_students || 0}</td>
            <td>
                ${isCrossListed ? 
                    '<span class="badge crosslisted-yes">Yes</span>' : 
                    '<span class="badge crosslisted-no">No</span>'}
            </td>
            <td>
                ${isCrossListed ? 
                    `<button type="button" class="btn btn-warning btn-sm btn-undo"
                            onclick="undoCrosslist(${section.section_id}, '${section.course_name.replace(/'/g, "\\'")}', ${instructorId})">
                        <i class="bi bi-arrow-counterclockwise"></i> Undo
                    </button>` : 
                    ''}
            </td>
        `;

        tableBody.appendChild(row);
    });

    // Re-initialize event listeners after re-rendering
    initializeTableEventListeners();
}

// Initialize table event listeners (called after rendering)
function initializeTableEventListeners() {
    const parentRadios = document.querySelectorAll('.parent-radio');
    const childCheckboxes = document.querySelectorAll('.child-checkbox');
    const submitBtn = document.getElementById('submitBtn');
    const summaryParent = document.getElementById('summaryParent');
    const summaryChild = document.getElementById('summaryChild');
    const tableBody = document.getElementById('coursesTableBody');

    let selectedParentCourseId = null;
    let selectedParentName = null;
    let selectedChildSections = [];

    // Parent selection handler
    parentRadios.forEach(radio => {
        radio.addEventListener('click', function(e) {
            if (this.checked && selectedParentCourseId === this.value) {
                this.checked = false;
                selectedParentCourseId = null;
                selectedParentName = null;
                document.querySelectorAll('.parent-selected').forEach(row => row.classList.remove('parent-selected'));
                childCheckboxes.forEach(cb => {
                    cb.checked = false;
                    cb.disabled = true;
                });
                document.querySelectorAll('.child-selected').forEach(row => row.classList.remove('child-selected'));
                selectedChildSections = [];
                updateChildAvailability();
                updateSummary();
                updateSubmitButton();
                return;
            }

            if (this.checked) {
                document.querySelectorAll('.parent-selected').forEach(row => row.classList.remove('parent-selected'));
                const row = this.closest('tr');
                selectedParentCourseId = this.value;
                selectedParentName = row.querySelector('td:nth-child(3) strong').textContent + ' - ' +
                                    row.querySelector('td:nth-child(4)').textContent;
                row.classList.add('parent-selected');
                childCheckboxes.forEach(cb => cb.checked = false);
                document.querySelectorAll('.child-selected').forEach(row => row.classList.remove('child-selected'));
                selectedChildSections = [];
                updateChildAvailability();
                updateSummary();
                updateSubmitButton();
            }
        });
    });

    // Child selection handler
    childCheckboxes.forEach(checkbox => {
        checkbox.addEventListener('change', function() {
            document.querySelectorAll('.child-selected').forEach(row => row.classList.remove('child-selected'));

            if (this.checked) {
                childCheckboxes.forEach(cb => {
                    if (cb !== this) cb.checked = false;
                });
                const row = this.closest('tr');
                row.classList.add('child-selected');
                const sectionName = row.querySelector('td:nth-child(3) strong').textContent + ' - ' +
                                   row.querySelector('td:nth-child(4)').textContent + ' (' +
                                   row.querySelector('td:nth-child(5)').textContent + ')';
                selectedChildSections = [sectionName];
            } else {
                selectedChildSections = [];
            }

            updateSummary();
            updateSubmitButton();
        });
    });

    function updateChildAvailability() {
        if (!selectedParentCourseId) {
            childCheckboxes.forEach(cb => {
                const row = cb.closest('tr');
                const published = row.dataset.published;
                const crossListed = row.dataset.crossListed;
                if (published === 'Yes' && crossListed !== 'Yes') {
                    cb.disabled = true;
                    cb.title = "Select a parent course first";
                }
            });
        } else {
            tableBody.querySelectorAll('tr').forEach(row => {
                const rowCourseId = row.dataset.courseId;
                const published = row.dataset.published;
                const crossListed = row.dataset.crossListed;
                const childCheckbox = row.querySelector('.child-checkbox');

                if (childCheckbox) {
                    const wasOriginallyDisabled = (published !== 'Yes') || (crossListed === 'Yes');
                    if (wasOriginallyDisabled) {
                        childCheckbox.disabled = true;
                    } else {
                        const shouldEnable = (rowCourseId !== selectedParentCourseId);
                        if (shouldEnable) {
                            childCheckbox.disabled = false;
                            childCheckbox.title = "Select as child section";
                            childCheckbox.setAttribute('aria-label', 'Select as child section');
                        } else {
                            childCheckbox.disabled = true;
                            childCheckbox.checked = false;
                            childCheckbox.title = "Cannot select same course as parent";
                            row.classList.remove('child-selected');
                        }
                    }
                }
            });
        }
    }

    function updateSummary() {
        if (selectedParentName) {
            summaryParent.innerHTML = `<span class="value">${selectedParentName}</span>`;
        } else {
            summaryParent.innerHTML = '<span class="none">None selected</span>';
        }

        if (selectedChildSections.length > 0) {
            summaryChild.innerHTML = `<span class="value">${selectedChildSections.join(', ')}</span>`;
        } else {
            summaryChild.innerHTML = '<span class="none">None selected</span>';
        }
    }

    function updateSubmitButton() {
        const parentSelected = selectedParentCourseId !== null;
        const childSelected = selectedChildSections.length > 0;

        if (parentSelected && childSelected) {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Validate & Confirm Cross-listing';
            submitBtn.className = 'btn btn-danger btn-lg px-5';
        } else {
            submitBtn.disabled = true;
            submitBtn.textContent = 'Select Parent and Child to Continue';
            submitBtn.className = 'btn btn-secondary btn-lg px-5';
        }
    }

    updateChildAvailability();
    updateSummary();
    updateSubmitButton();
}

// Undo cross-listing function with loading modal and JSON response
function undoCrosslist(sectionId, courseName, instructorId) {
    if (confirm(`Are you sure you want to undo cross-listing for:\n${courseName}?`)) {
        // Show loading modal
        showLoadingModal();

        const termId = document.getElementById('termId').value;
        const formData = new FormData();
        formData.append('section_id', sectionId);
        formData.append('term_id', termId);
        formData.append('instructor_id', instructorId);
        formData.append('user_name', '');

        // Minimum display time for modal (1.2 seconds)
        const minDisplayTime = 1200;
        const startTime = Date.now();

        // Submit via fetch with JSON response
        fetch(document.querySelector('[data-undo-url]').dataset.undoUrl, {
            method: 'POST',
            body: formData,
            headers: {
                'Accept': 'application/json'
            }
        })
        .then(response => {
            if (!response.ok) {
                return response.json().then(data => {
                    throw new Error(data.message || 'Un-crosslisting failed');
                });
            }
            return response.json();
        })
        .then(data => {
            // Calculate remaining time to show modal
            const elapsed = Date.now() - startTime;
            const remainingTime = Math.max(0, minDisplayTime - elapsed);

            // Wait for minimum display time, then hide modal and update table
            setTimeout(() => {
                hideLoadingModal();

                if (data.status === 'success') {
                    // Update global state with refreshed data
                    window.coursesState = data.courses_state;
                    
                    // Re-render the table with new state
                    renderCoursesTable(data.courses_state);
                    
                    // Show success message
                    showSuccessBanner(data.message);
                } else {
                    alert('Error: ' + data.message);
                }
            }, remainingTime);
        })
        .catch(error => {
            console.error('Error:', error);
            hideLoadingModal();
            alert('An error occurred during un-crosslisting: ' + error.message);
        });
    }
}

// Show success banner at top of page
function showSuccessBanner(message) {
    // Remove any existing success banners
    const existingBanners = document.querySelectorAll('.alert-success.alert-custom');
    existingBanners.forEach(banner => banner.remove());

    // Create new success banner
    const banner = document.createElement('div');
    banner.className = 'alert alert-success alert-dismissible fade show alert-custom';
    banner.setAttribute('role', 'alert');
    banner.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;

    // Insert at top of container
    const container = document.querySelector('.container');
    container.insertBefore(banner, container.firstChild);

    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        banner.classList.remove('show');
        setTimeout(() => banner.remove(), 150);
    }, 5000);
}
