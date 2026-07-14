"""
The project's guard implementations: positioned steps in the agent loop that
check a tool call and may deny it.

This example ships no project-specific guard -- the core guards (trace_audit,
tool_policy, tool_schema_validate, regex_redact) are used straight from the
config. Add a project guard module here and re-export it below when you need one.
"""

__all__ = []
