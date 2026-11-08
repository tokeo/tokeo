"""
Tests for tokeo.core.cement: the controller helpers get_command_meta,
get_default_command_meta and log_info_help inside a command and in the default
of a controller, and the check of the extensions an extension requires.
"""

import pytest
from cement import Controller, ex

from tokeo.main import TokeoTest
from tokeo.controllers.base import BaseController
from tokeo.core.exc import TokeoError
from tokeo.core.cement.controllers import get_command_meta, get_default_command_meta, log_info_help
from tokeo.core.cement.extensions import check_required_extensions


class HelpersController(Controller):
    """Records what the helpers see inside a command and its default."""

    class Meta:
        label = 'helpers'
        stacked_on = 'base'
        stacked_type = 'nested'
        default_func = 'standard'

    def _record(self):
        self.app.seen_metas.update(subcmd=get_command_meta(self), default=get_default_command_meta(self))
        if get_command_meta(self):
            log_info_help(self)

    @ex(help='the help of command')
    def command(self):
        self._record()

    @ex(help='the help of standard')
    def standard(self):
        self._record()


class NoDefaultController(Controller):
    """Keeps the stock default of cement, which is no exposed command."""

    class Meta:
        label = 'nodefault'
        stacked_on = 'base'
        stacked_type = 'nested'

    @ex(help='the help of command')
    def command(self):
        self.app.seen_metas.update(default=get_default_command_meta(self))


class CementTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.tui.output',
        ]
        handlers = [
            BaseController,
            HelpersController,
            NoDefaultController,
        ]


def _run(argv, monkeypatch=None):
    # run the app on argv, return what the command saw and the info log
    logged_lines = []
    with CementTest(argv=argv) as app:
        app.seen_metas = {}
        if monkeypatch:
            monkeypatch.setattr(app.log, 'info', lambda msg, **kw: logged_lines.append(msg))
        app.run()
        return app.seen_metas, logged_lines


# ---- controllers ------------------------------------------------------------


def test_controllers_get_command_meta_is_the_dispatched_command():
    """Inside a command get_command_meta is the meta of that command."""
    seen_metas, _ = _run(['helpers', 'command'])
    assert seen_metas['subcmd'].func_name == 'command'
    assert seen_metas['subcmd'].parser_options['help'] == 'the help of command'


def test_controllers_get_command_meta_is_none_in_the_default():
    """Inside the default of a controller get_command_meta is None."""
    seen_metas, _ = _run(['helpers'])
    assert seen_metas['subcmd'] is None


def test_controllers_get_default_command_meta_is_the_exposed_default():
    """get_default_command_meta is the exposed default, in a command and default."""
    for argv in (['helpers', 'command'], ['helpers']):
        seen_metas, _ = _run(argv)
        assert seen_metas['default'].func_name == 'standard'
        assert seen_metas['default'].parser_options['help'] == 'the help of standard'


def test_controllers_get_default_command_meta_is_none_without_exposed_default():
    """Without an exposed default get_default_command_meta is None."""
    seen_metas, _ = _run(['nodefault', 'command'])
    assert seen_metas['default'] is None


def test_controllers_log_info_help_logs_the_help(monkeypatch):
    """log_info_help logs the help of the command on info."""
    _, logged_lines = _run(['helpers', 'command'], monkeypatch)
    assert logged_lines == ['the help of command']


# ---- extensions -------------------------------------------------------------


def test_check_required_extensions_names_the_missing_ones():
    """required raises TokeoError naming every missing extension."""
    with CementTest() as app:
        check_required_extensions(app, 'tokeo.ext.probe', 'tokeo.ext.tui.output')
        with pytest.raises(TokeoError, match='needs tokeo.ext.tui.input and tokeo.ext.x, add them to the extensions of the app!'):
            check_required_extensions(app, 'tokeo.ext.probe', 'tokeo.ext.tui.input', 'tokeo.ext.tui.output', 'tokeo.ext.x')
