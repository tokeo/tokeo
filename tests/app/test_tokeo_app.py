"""
Tests for the app itself, its run, its debug mode and the errors main reports.
"""

# test rules exception: single file in tests/app/ as in the app template

import os

import pytest

import tokeo.main
from tokeo.core.exc import TokeoBaseException, TokeoError
from tokeo.main import TokeoTest
from tests.utils import ROOT


def test_tokeo():
    """The app without a command runs and exits with 0."""
    with TokeoTest() as app:
        app.run()
        assert app.exit_code == 0


def test_tokeo_reads_its_config_by_appenv(monkeypatch):
    """The tokeo app reads config/tokeo by appenv, with the files of TOKEO_ENV."""
    monkeypatch.setenv('TOKEO_ENV', 'prod')
    with tokeo.main.Tokeo(argv=[], exit_on_close=False) as app:
        assert app.env.IS_PROD_MODE and app.env.APP_CONFIG_DIR == os.path.join(ROOT, 'config', 'tokeo')
        config_files = app._meta.config_files
    assert os.path.join(ROOT, 'config', 'tokeo', 'base.yaml') in config_files
    assert os.path.join(ROOT, 'config', 'tokeo', 'production.yaml') in config_files


def test_tokeo_debug():
    """The app with --debug runs in debug mode."""
    argv = ['--debug']
    with TokeoTest(argv=argv) as app:
        app.run()
        assert app.debug is True


@pytest.mark.parametrize('kind', ['error', 'base'])
def test_main_reports_a_tokeo_exception_with_exit_code_1(monkeypatch, kind):
    """main reports TokeoError and an exception only of TokeoBaseException by 1."""

    class OnlyBase(TokeoBaseException):
        pass

    class FailingApp(TokeoTest):

        def run(self):
            raise TokeoError('failed') if kind == 'error' else OnlyBase('aborted')

    monkeypatch.setattr(tokeo.main, 'Tokeo', FailingApp)
    with pytest.raises(SystemExit) as exited:
        tokeo.main.main()
    assert exited.value.code == 1
