"""
Public page module exports for the application.

This module explicitly defines the available page functions using the __all__
list. This allows the programmatic router in site/routes.py to import and
index them cleanly without triggering linter warnings for unused imports.

### Notes:

- Every new page function created in this directory should be imported
  here and added to the __all__ list
- This structure ensures pdoc correctly indexes and documents the public
  page methods of this package

"""

from .index import page_root
from .hello_world import page_hello_world


# Tell Python explicitly that these are the public exports
__all__ = [
    'page_root',
    'page_hello_world',
]
