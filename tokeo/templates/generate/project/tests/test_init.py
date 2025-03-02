# ensure we don't break imports from cement namespace


def test_cement_import():
    from cement import App, Controller, ex, init_defaults  # noqa: F401
