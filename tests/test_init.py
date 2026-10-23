"""
The imports from the cement namespace keep working.
"""


def test_cement_import():
    from cement import App, Controller, ex, init_defaults  # noqa: F401
