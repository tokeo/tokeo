"""
Web interface and routing package for the Tokeo application.

This package contains the complete NiceGUI web frontend, including dynamic
routing, isolated page rendering, REST API endpoints, and shared UI components.

### Architecture (NiceGUI 3.x):

This package strictly adheres to a stateless, multi-user safe architecture:

- **No Global UI State**: UI elements (`ui.label`, `ui.button`, etc.) are never
  instantiated in the global scope of any file
- **Isolated Contexts**: All pages and structural components are defined as
  pure functions and only execute when a user routes to them
- **Programmatic Routing**: Routes are mapped dynamically in `routes.py` rather
  than relying on scattered decorators

### Package Structure:

- **apis/**: Headless REST API endpoints (FastAPI integration)
- **components/**: Shared structural UI blocks (layouts, navbars, footers)
- **pages/**: Isolated UI page rendering functions
- **routes.py**: General programmatic routing dictionary and orchestrator
- **openapi.py**: Custom Swagger/ReDoc and OpenAPI documentation endpoints

"""
