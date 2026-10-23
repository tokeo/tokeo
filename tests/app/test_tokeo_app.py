"""
Tests for the app itself, its run and its debug mode.
"""

# test rules exception: single file in tests/app/ as in the app template

from tokeo.main import TokeoTest


def test_tokeo():
    # test tokeo without any subcommands or arguments
    with TokeoTest() as app:
        app.run()
        assert app.exit_code == 0


def test_tokeo_debug():
    # test that debug mode is functional
    argv = ['--debug']
    with TokeoTest(argv=argv) as app:
        app.run()
        assert app.debug is True
