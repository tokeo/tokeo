"""
Web page routes and API endpoint mapping for the application.

This module acts as the central registry for all web pages and API endpoints.
It imports isolated page and API functions and binds them to specific URL
paths using NiceGUI and FastAPI's programmatic routers.

**CRITICAL (NiceGUI 3.x Architecture):** Absolutely no UI elements (`ui.label`,
`ui.button`, etc.) may be instantiated in the global scope of this file.
All UI construction must happen strictly inside the registered page functions
to prevent memory leaks and cross-session state contamination.

### Route Structure:

Routes are defined as pure, stateless functions in the `pages/` and `apis/`
directories, and are mapped to URL paths in this file via dictionaries or
direct programmatic calls.

### Usage:

To add a new page or API endpoint to your application:

1. Create a pure function in `site/pages/` or `site/apis/`.
2. Import it into this module.
3. Add it to the `pages` mapping dictionary or API registry.

### Example of adding a new page dynamically:

```python
from .pages import page_catalog

def pages_map():
    pages = {
        '/': page_root,
        '/catalog': page_catalog,  # Newly mapped route
    }

    for path, method in pages.items():
        ui.page(path, title=f'Tokeo - {path[2:]}')(method)
```

### Notes:

- `ui.page()` and `fastapi_app.get()` are used as programmatic wrappers in a loop,
  not as decorators
- The `routes()` function is invoked by the `TokeoNicegui` engine during startup

"""

from tokeo.ext.appshare import app
from .apis import api_example
from .pages import page_root, page_hello_world


ui = app.nicegui.ui
fa = app.nicegui.fastapi_app


def apis_map():
    """
    Programmatically register all FastAPI REST endpoints.

    As an example implementation it maps URL paths programmatically directly.
    The API functions are isolated, by serving raw JSON and data via FastAPI.

    """
    # Set direct routes mapping URL paths to the actual api functions
    fa.get('/_/api/example')(api_example)


def pages_map():
    """
    Programmatically register all NiceGUI web pages.

    As an example implementation it iterates through a dictionary of
    routes and binds them to their respective UI generation functions.
    Additional it injects dynamic parameters like page titles.

    """
    # A dictionary mapping URL paths to the actual UI generation functions
    pages = {
        '/': page_root,
        '/hello-world': page_hello_world,
    }

    # register all of them in a loop
    for path, method in pages.items():
        # pass additional arguments like dark mode or title here!
        ui.page(path, title=f'Tokeo - {path[2:]}')(method)


def routes():
    """
    Activate the routes for APIs and pages.

    This function is called by the application orchestrator during startup
    to finalize the routing map before the web server binds to the port.

    """
    apis_map()
    pages_map()
