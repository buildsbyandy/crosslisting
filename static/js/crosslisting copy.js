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

            // Wait for minimum display time, then hide modal and refresh
            setTimeout(() => {
                hideLoadingModal();

                if (data.status === 'success') {
                    // Reload the page with success message
                    const redirectForm = document.createElement('form');
                    redirectForm.method = 'POST';
                    redirectForm.action = window.location.pathname;

                    const userIdInput = document.createElement('input');
                    userIdInput.type = 'hidden';
                    userIdInput.name = 'user_id';
                    userIdInput.value = instructorId;
                    redirectForm.appendChild(userIdInput);

                    const termIdInput = document.createElement('input');
                    termIdInput.type = 'hidden';
                    termIdInput.name = 'term_id';
                    termIdInput.value = termId;
                    redirectForm.appendChild(termIdInput);

                    const userNameInput = document.createElement('input');
                    userNameInput.type = 'hidden';
                    userNameInput.name = 'user_name';
                    userNameInput.value = '';
                    redirectForm.appendChild(userNameInput);

                    const flashInput = document.createElement('input');
                    flashInput.type = 'hidden';
                    flashInput.name = 'flash_success';
                    flashInput.value = data.message;
                    redirectForm.appendChild(flashInput);

                    document.body.appendChild(redirectForm);
                    redirectForm.submit();
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
