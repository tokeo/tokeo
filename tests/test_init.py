"""
The imports from the cement namespace keep working.
"""


def test_cement_import():
    """App, Controller, ex and init_defaults import from cement."""
    from cement import App, Controller, ex, init_defaults  # noqa: F401
