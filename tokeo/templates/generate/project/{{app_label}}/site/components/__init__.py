"""
Public component module exports for the Web application.

This module provides shared structural UI blocks (layouts, navbars, footers)
used to maintain design consistency across the application.

**CRITICAL (NiceGUI 3.x Architecture):** Functions and context managers in these
modules must only be invoked from inside an actively routed page function.
Do not instantiate these UI blocks in the global scope.

### Notes:

- This module needs not explicitly defines the inner component modules

"""
