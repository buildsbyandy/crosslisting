# Canvas Cross-Listing Tool - Standalone Script

## Overview

This standalone script provides a comprehensive solution for managing Canvas course cross-listings. It automates the process of linking multiple course sections together, making it easier for instructors to manage multiple sections of the same course.

## Key Features

✅ **Complete Standalone Solution** - No external dependencies beyond Python standard library  
✅ **Interactive Term Selection** - Browse and select from available enrollment terms  
✅ **Section Management** - View all course sections with their cross-listing status  
✅ **Cross-Listing Operations** - Cross-list and un-cross-list sections with validation  
✅ **Comprehensive Validation** - Ensures proper cross-listing conditions are met  
✅ **Multiple Export Formats** - CSV export with detailed section information  
✅ **Professional Error Handling** - Robust API error handling and logging  
✅ **Rate Limiting Protection** - Respects Canvas API rate limits automatically  
✅ **Environment-Based Configuration** - Secure credential management  

## What is Cross-Listing?

Cross-listing in Canvas allows you to combine multiple course sections into a single course shell. This is useful when:

- An instructor teaches multiple sections of the same course
- Students need to see all sections in one place
- Administrative tasks need to be performed across multiple sections
- Course content is shared between sections

## Quick Start

### 1. Set Up Environment

Create a `.env` file in the same directory as the script:

```bash
# Required settings
CANVAS_API_TOKEN=your_actual_api_token_here
CANVAS_BASE_URL=https://your-institution.instructure.com

# Optional settings (defaults shown)
CANVAS_ACCOUNT_ID=415
CANVAS_PER_PAGE=100
CANVAS_TIMEOUT=30
CANVAS_MAX_RETRIES=3
CANVAS_REQUESTS_PER_MINUTE=60
CANVAS_RETRY_DELAY=1.0
```

### 2. Install Optional Dependency (Recommended)

```bash
pip install python-dotenv
```

### 3. Run the Script

```bash
python standalone_crosslisting_tool.py
```

## How It Works

### 1. **Term Selection**
The script fetches all active enrollment terms from Canvas and displays them for selection:

```
Available Terms (5 found):
------------------------------------------------------------
 1. Fall 2025                           (ID: 1234)
     Start: 2025-08-25T00:00:00Z        End: 2025-12-19T23:59:59Z
 2. Summer 2025                          (ID: 1235)
     Start: 2025-05-19T00:00:00Z        End: 2025-08-15T23:59:59Z
 3. Spring 2025                          (ID: 1236)
     Start: 2025-01-13T00:00:00Z        End: 2025-05-16T23:59:59Z
```

### 2. **Section Display**
After selecting a term, the script displays all course sections with their status:

```
========================================================================================================================
COURSE SECTIONS
========================================================================================================================
#   Course Code     Section    Published  Cross-listed Course Name
------------------------------------------------------------------------------------------------------------------------
1   CS61.11B       0720        No         No           Microsoft Excel, Part 2
2   CS61.11B       1324        No         No           Microsoft Excel, Part 2
3   CS63.11A       2160        Yes        No           Microsoft Access, Part 1
4   BBK53.2        1654        No         No           QuickBooks Level 2
```

### 3. **Cross-Listing Operations**
The script provides a menu-driven interface for cross-listing operations:

```
========================================================================================================================
Cross-Listing Operations
========================================================================================================================
1. Cross-list sections
2. Un-cross-list sections
3. Export sections to CSV
4. Refresh sections
5. Exit
------------------------------------------------------------------------------------------------------------------------
```

## Cross-Listing Process

### Prerequisites for Cross-Listing

1. **Parent Course**: Must be unpublished (no student activity)
2. **Child Course**: Must be published
3. **Different Courses**: Cannot cross-list sections from the same course
4. **Not Already Cross-listed**: Sections must not already be cross-listed

### Validation Checks

The script automatically validates cross-listing candidates:

- ✅ Parent course is unpublished
- ✅ Child course is published
- ✅ Sections are from different courses
- ✅ Neither section is already cross-listed
- ✅ Both sections exist and are accessible

### Cross-Listing Steps

1. **Select Parent Section**: Choose the main course that will contain the cross-listed section
2. **Select Child Section**: Choose the section to be cross-listed into the parent
3. **Validation**: Script checks all prerequisites
4. **Confirmation**: Review the cross-listing details
5. **Execution**: Perform the cross-listing operation
6. **Verification**: Refresh and display updated section status

## API Endpoints Used

The script utilizes these Canvas API endpoints:

- `GET /api/v1/accounts/{account_id}/terms` - Fetch enrollment terms
- `GET /api/v1/accounts/{account_id}/courses` - Fetch courses with sections
- `GET /api/v1/users/{user_id}/courses` - Fetch user's courses (if filtering by user)
- `POST /api/v1/sections/{section_id}/crosslist` - Cross-list a section
- `DELETE /api/v1/sections/{section_id}/crosslist` - Un-cross-list a section

## Use Cases

### **Faculty Self-Service**
- Instructors can cross-list their own course sections
- No need to submit service tickets for simple cross-listings
- Immediate feedback on cross-listing status

### **Administrative Support**
- Process cross-listing requests from service tickets
- Bulk cross-listing operations for department-wide course management
- Validate cross-listing requests before processing

### **Course Management**
- Standardize cross-listing procedures across the institution
- Maintain audit trail of cross-listing operations
- Export cross-listing data for reporting

## Service Ticket Integration

The script can be used to process cross-listing requests from service tickets:

### **Validation Steps**
1. Verify course numbers match (except for credit/non-credit variations)
2. Check Dean/AD approval in the ticket
3. Validate course IDs and CRN numbers
4. Ensure proper course states (parent unpublished, child published)

### **Processing Steps**
1. Use the script to perform the cross-listing
2. Update parent course code if needed
3. Export results for ticket documentation
4. Send confirmation to faculty

## Export Features

### **CSV Export**
The script can export section data to CSV format with the following fields:

- Section ID
- Section Name
- Course ID
- Course Name
- Course Code
- SIS Course ID
- SIS Section ID
- Published Status
- Cross-listed Status
- Parent Course ID
- Full Title

### **Sample Export**
```csv
Section ID,Section Name,Course ID,Course Name,Course Code,SIS Course ID,SIS Section ID,Published,Cross-listed,Parent Course ID,Full Title
12345,0720,67890,Microsoft Excel Part 2,CS61.11B,CS61.11B_2024_FA_0720,0720,No,No,,CS61.11B: Microsoft Excel Part 2: Section 0720
12346,1324,67890,Microsoft Excel Part 2,CS61.11B,CS61.11B_2024_FA_1324,1324,No,No,,CS61.11B: Microsoft Excel Part 2: Section 1324
```

## Error Handling

The script includes comprehensive error handling for:

- **Authentication Errors**: Invalid or expired API tokens
- **Permission Errors**: Insufficient permissions for cross-listing operations
- **Validation Errors**: Invalid cross-listing candidates
- **Network Errors**: Connection issues and timeouts
- **Rate Limiting**: Automatic handling of API rate limits

## Security Features

- **Environment Variables**: No hardcoded credentials
- **Secure Connections**: HTTPS-only API communication
- **Token Management**: Bearer token authentication
- **Error Sanitization**: No sensitive data in error messages
- **Permission Validation**: Checks user permissions before operations

## Performance Features

- **Rate Limiting**: Automatic API rate limit compliance
- **Pagination**: Efficient handling of large datasets
- **Caching**: LRU cache for repeated operations
- **Memory Management**: Efficient data structures
- **Progress Feedback**: Clear progress indicators during operations

## Troubleshooting

### Common Issues

1. **"Authentication failed"** - Check your API token and permissions
2. **"Permission denied"** - Ensure your API token has admin permissions
3. **"No sections found"** - Verify the term selection and course availability
4. **"Validation failed"** - Check cross-listing prerequisites

### Debug Mode

Enable debug output by setting the environment variable:

```bash
export CANVAS_DEBUG="true"
```

### API Permissions

Your Canvas API token needs these permissions:

- `url:GET|/api/v1/accounts/:account_id/terms`
- `url:GET|/api/v1/accounts/:account_id/courses`
- `url:POST|/api/v1/sections/:section_id/crosslist`
- `url:DELETE|/api/v1/sections/:section_id/crosslist`

## Best Practices

### **Before Cross-Listing**
1. Verify course numbers match (except credit/non-credit variations)
2. Ensure parent course is unpublished
3. Ensure child course is published
4. Check that sections are not already cross-listed

### **After Cross-Listing**
1. Verify the cross-listing was successful
2. Update parent course code if needed
3. Test the cross-listed course functionality
4. Document the cross-listing for future reference

### **Administrative Procedures**
1. Process cross-listing requests promptly
2. Validate all requests before processing
3. Maintain audit trails of cross-listing operations
4. Provide clear feedback to faculty

## Future Enhancements

Potential improvements for future versions:

1. **Bulk Operations**: Cross-list multiple sections at once
2. **User Filtering**: Filter sections by specific instructors
3. **Automated Validation**: Enhanced validation rules
4. **Integration APIs**: Connect with service ticket systems
5. **Reporting**: Generate cross-listing reports and analytics
6. **Scheduling**: Automated cross-listing based on rules

## Support

For issues or questions:

1. Check the troubleshooting section above
2. Review Canvas API documentation
3. Verify your API token permissions
4. Test with a small dataset first
5. Enable debug mode for detailed logging

This tool provides a robust, user-friendly solution for managing Canvas course cross-listings while maintaining security and performance standards. 