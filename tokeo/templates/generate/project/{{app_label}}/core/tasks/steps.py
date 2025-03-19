"""
Reusable business functions 'steps' for modular application workflows.

This module provides a centralized location for defining atomic 'step' functions
that perform specific, reusable operations within business workflows in Tokeo and
Cement applications. Steps are modular, composable functions that implement
individual parts of larger business processes, such as PDF generation, data
transformation, or external service integration.

### Features:

- **Atomic business operations** for use across multiple workflows
- **Highly reusable components** with focused responsibility
- **External tool integration** (like document generation, image processing)
- **Standardized interfaces** for consistency and maintainability
- **Cross-module utility** for both synchronous and asynchronous contexts

### Usage:

Define step functions that implement specific business operations:

```python
from tokeo.ext.appshare import app
import subprocess
import os

def generate_pdf_from_html(html_content, output_path, options=None):
    '''
    Generate a PDF document from HTML content using puppeteer.

    This step uses a Node.js script with puppeteer to convert HTML content
    to a PDF document with customizable formatting options.

    '''
    if options is None:
        options = {'format': 'A4', 'margin': '1cm'}

    # Create temporary HTML file
    temp_html = os.path.join(app.utils.temp_dir, f"{app.utils.uuid()}.html")
    with open(temp_html, 'w') as f:
        f.write(html_content)

    # Prepare command for puppeteer script
    cmd = [
        'node',
        app.config.get('pdf', 'puppeteer_script'),
        '--input', temp_html,
        '--output', output_path,
        '--format', options.get('format', 'A4'),
        '--margin', options.get('margin', '1cm')
    ]

    try:
        # Execute puppeteer script
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        app.log.info(f"PDF generated successfully at {output_path}")

        # Clean up temporary file
        os.unlink(temp_html)

        return {
            'success': True,
            'path': output_path,
            'size': os.path.getsize(output_path)
        }
    except subprocess.CalledProcessError as e:
        app.log.error(f"PDF generation failed: {e.stderr}")
        return {
            'success': False,
            'error': e.stderr
        }
```

### Notes:

- Steps should have a single, well-defined responsibility
- Keep steps stateless and focused on a specific task
- Design steps to be reusable across different workflows
- Document steps thoroughly, especially external dependencies
- Steps can be used by performers, actors, agents, and automate functions
- Include appropriate error handling and logging in each step
- Keep steps independent of specific business logic when possible

"""

from tokeo.ext.appshare import app  # noqa: F401
