"""
Public API endpoint module exports for the application.

This module explicitly defines the available headless API functions using
the `__all__` list. This allows the programmatic router in `site/routes.py`
to import and bind them cleanly without triggering linter warnings for
unused imports.

### Notes:

: Every new API endpoint function created in this directory should be imported
  here and added to the `__all__` list
: This structure ensures `pdoc` correctly indexes and documents the public
  API methods of this package

"""

from .example import api_example


# Tell Python explicitly that these are the public exports
__all__ = [
    'api_example',
]
