from pytest import raises
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
