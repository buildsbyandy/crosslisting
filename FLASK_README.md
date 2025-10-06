# Flask Canvas Cross-Listing Web Application

This Flask web application provides a user-friendly interface for the Canvas cross-listing tool, wrapping the CLI functionality into a minimal skeleton UI.

## Features

- **Terms Management**: Browse available enrollment terms
- **Section Search**: Find sections by instructor or staff search criteria
- **Cross-listing Operations**: Perform cross-list and un-cross-list operations
- **CSV Export**: Export section data for documentation
- **Bootstrap UI**: Clean, responsive interface with Bootstrap styling
- **Error Handling**: Friendly error messages and validation

## Setup

### Prerequisites

1. Python 3.7 or higher
2. Flask and python-dotenv packages
3. Canvas API token with cross-listing permissions
4. Access to Canvas LMS instance

### Installation

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set environment variables:**
   Create a `.env` file in the project root with:
   ```
   CANVAS_API_TOKEN=your_canvas_api_token_here
   CANVAS_BASE_URL=https://your-canvas-instance.instructure.com
   CANVAS_ACCOUNT_ID=415
   ```

3. **Test the application:**
   ```bash
   python test_app.py
   ```

4. **Start the Flask server:**
   ```bash
   python app.py
   ```

5. **Open browser to:**
   ```
   http://localhost:5000
   ```

## Application Structure

### Routes

- `/` - Home page with navigation links
- `/terms` - Display available enrollment terms
- `/sections` - Show sections for a term (with search filters)
- `/crosslist` (POST) - Perform cross-listing operation
- `/uncrosslist` (POST) - Perform un-cross-listing operation
- `/export` - Export sections to CSV

### Templates

- `base.html` - Bootstrap layout with navigation
- `home.html` - Landing page with instructions
- `terms.html` - Terms listing with search options
- `sections.html` - Section management interface
- `result.html` - Operation success/failure results
- `error.html` - Error display page
- `instructor_select.html` - Multiple instructor selection

### Key Functions

The Flask app imports and uses these core functions from `standalone_crosslisting_tool.py`:

- `fetch_active_terms()` - Get available terms
- `resolve_instructor()` - Find instructor by various identifiers
- `get_course_sections()` - Retrieve course sections
- `cross_list_section()` - Perform cross-listing
- `un_cross_list_section()` - Undo cross-listing
- `export_sections_to_csv()` - Export data
- `format_sections_for_ui()` - Format for web display
- `validate_cross_listing_candidates()` - Validation rules

## Usage Workflow

1. **Select a Term**: Navigate to Terms page and choose an enrollment term
2. **Search Sections**: Either:
   - Enter instructor identifier for faculty mode
   - Use search term for staff mode (e.g., "MATH", "1405", "BIO")
3. **View Results**: Browse sections with color-coded eligibility:
   - Green border: Parent candidate (unpublished)
   - Blue border: Child candidate (published)
   - Yellow border: Already cross-listed
   - Gray border: Not eligible
4. **Perform Operations**: Use the action buttons to cross-list or un-cross-list
5. **Export Data**: Download CSV for documentation

## Cross-listing Requirements

### Parent Course (Target)
- Must be unpublished (no student activity)
- Cannot already be cross-listed
- User must have permissions to manage the course

### Child Course (Source)
- Must be published
- Cannot already be cross-listed
- Must be a different course than the parent

### Validation
- Both sections must be in the same term
- Different courses (not same course)
- Appropriate permissions required

## Configuration Options

Environment variables can be used to customize behavior:

```
CANVAS_API_TOKEN=your_token              # Required
CANVAS_BASE_URL=https://canvas.edu       # Required
CANVAS_ACCOUNT_ID=415                    # Optional (default: 415)
FLASK_SECRET_KEY=your_secret_key         # Optional (for sessions)
FLASK_DEBUG=true                         # Optional (for development)
FLASK_PORT=5000                          # Optional (default: 5000)
```

## Development

### File Structure
```
├── app.py                    # Flask application
├── standalone_crosslisting_tool.py  # Core CLI functions
├── test_app.py              # Test script
├── requirements.txt         # Python dependencies
├── templates/               # Jinja2 templates
│   ├── base.html
│   ├── home.html
│   ├── terms.html
│   ├── sections.html
│   ├── result.html
│   ├── error.html
│   └── instructor_select.html
├── logs/                    # Audit logs (created automatically)
└── cache/                   # API response cache (created automatically)
```

### Testing
Run the test suite to verify functionality:
```bash
python test_app.py
```

This tests:
- Flask app import
- Route accessibility
- Template availability

### API Integration
The Flask app maintains the same audit logging and validation as the CLI tool. All operations are logged to `logs/crosslist_audit.csv` with timestamps and details.

## Security Notes

- API tokens are loaded from environment variables only
- No credentials are stored in code or templates
- All operations require appropriate Canvas permissions
- Audit trail maintained for all actions

## Stage 2 Enhancements (Future)

This is Stage 1 implementation focusing on basic functionality. Stage 2 will add:
- Modal confirmations for warnings
- Checkbox selection for multiple operations
- Enhanced validation displays
- Acknowledgment text requirements
- Additional GUI restrictions

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure all dependencies are installed
2. **Configuration Errors**: Check environment variables
3. **Permission Denied**: Verify API token has cross-listing permissions
4. **No Sections Found**: Check instructor spelling and term validity
5. **Template Not Found**: Ensure templates directory exists with all files

### Logs
Check these locations for debugging:
- Console output (Flask debug messages)
- `logs/crosslist_audit.csv` (operation audit trail)
- Canvas API error responses (in console)