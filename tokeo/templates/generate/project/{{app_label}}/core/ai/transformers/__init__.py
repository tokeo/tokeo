"""
The project's transformer implementations: positioned steps in the agent loop
that reshape what the run carries, without ever denying a call.

The truncate transformer is re-exported here, so it can be reached from the short
package path ```{{ app_label }}.core.ai.transformers``` as well as its own module.
"""

from {{ app_label }}.core.ai.transformers.truncate import {{ app_class_name }}AiTruncateTransformer

__all__ = [
    '{{ app_class_name }}AiTruncateTransformer',
]
