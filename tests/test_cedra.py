from pytest import raises
from cedra.main import CedraTest


def test_cedra():
    # test cedra without any subcommands or arguments
    with CedraTest() as app:
        app.run()
        assert app.exit_code == 0


def test_cedra_debug():
    # test that debug mode is functional
    argv = ['--debug']
    with CedraTest(argv=argv) as app:
        app.run()
        assert app.debug is True
