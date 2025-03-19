"""
Web page routes and UI definitions for the application's website.

This module provides a centralized location for defining website routes and
page handlers in Tokeo and Cement applications using NiceGUI. It contains
the default (index) route and additional page routes that make up the website's
navigation structure, leveraging the components.blocks and components.layout
modules for consistent page structure and design.

### Features:

- **Default route** (`/`) serving as the application's homepage
- **Page definitions** with consistent layout and navigation
- **Route handlers** for different application URL paths
- **UI component organization** using a modular component approach
- **Responsive design** that works across different device types

### Route Structure:

Routes are defined either as simple functions (for the default route) or as
decorated functions using `@ui.page('/path')` for specific paths. Each route
handler typically uses the shared layout components to maintain design
consistency while implementing page-specific content.

### Usage:

Define a new page route by adding a function with the `@ui.page` decorator:

```python
from tokeo.ext.appshare import app
from .components import blocks

ui = app.nicegui.ui
ux = app.nicegui.ux

@ui.page('/products')
def products_page():
    '''Products catalog page showing available items.'''
    with blocks.page(title='Product Catalog'):
        # Page-specific content
        with ui.card().classes('w-full'):
            ui.label('Product Listings').classes('text-xl font-bold')

            # Product grid
            with ui.grid(columns=3).classes('gap-4 mt-4'):
                for i in range(6):
                    with ui.card():
                        ui.label(f'Product {i+1}').classes('text-lg font-semibold')
                        ui.label('$99.99').classes('text-blue-500')
                        ui.button(
                            'Add to Cart',
                            on_click=lambda: ui.notify('Added to cart'),
                        )
```

The routes module works with the blocks and layout modules to create a
consistent page structure while allowing page-specific content:

- **blocks.page()**: Provides the standard page container with title, navigation,
  and footer
- **layout module**: Lower-level components that define the overall page structure
- **ux element helper**: Provides access to HTML elements not directly exposed
  by NiceGUI

### Notes:

- The default route function (named 'default') is automatically registered as the
  index ('/')
- Routes are configured in the application's YAML configuration under nicegui.routes
- The layout and blocks modules abstract away page structure for consistent design
- Use Tailwind CSS classes for styling consistency and responsive design
- Each route handler should focus on its specific page content

"""

from tokeo.ext.appshare import app
from .components import blocks


ui = app.nicegui.ui
ux = app.nicegui.ux


def default():
    """
    Default route handler for the application homepage.

    This function defines the content of the website's landing page (index route).
    It uses the standard page layout from the blocks module and adds
    homepage-specific content.

    ### Notes:

    - Automatically registered as the '/' route in the application
    - Configured via the 'default_route' setting in nicegui config
    - Creates a dashboard view with introductory content

    """
    with blocks.page(title='This is the {{ app_name }} dashboard'):
        ux.p(
            """
            This single-page "app" style layout features a sidebar, main content area, and footer.
            This full-height layout is never more than viewport height. The content area scrolls
            independently as needed. For this example, we're using the Tailwind CSS utility framework.
            As part of it's default classes, Tailwind includes Flexbox classes which make this layout
            implementation simple!
            """
        ).classes('text-lg')


@ui.page('/hello-world')
def hello_function():
    """
    Example route showing a simple page with custom content.

    This function demonstrates how to create additional pages beyond
    the default route, using the @ui.page decorator to specify the URL path.

    ### Notes:

    - Accessible at the '/hello-world' URL path
    - Uses the standard page layout for consistency
    - Demonstrates adding custom styled content to a page

    """
    with blocks.page(title='Customers administration'):
        ui.label('Hello world!').classes('text-2xl text-orange-500')
