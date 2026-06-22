"""
The project's guard implementations: positioned steps in the agent loop
that observe, govern, mask, or shorten what the run carries.

The truncate guard is re-exported here, so it can be reached from the short
package path ```{{ app_label }}.core.ai.guards``` as well as its own module.
"""

from {{ app_label }}.core.ai.guards.truncate import {{ app_class_name }}AiTruncateGuard

__all__ = [
    '{{ app_class_name }}AiTruncateGuard',
]
