"""
Tests for tokeo.core.utils.cement.
"""

import pytest
from cement import Controller, ex

from tokeo.main import TokeoTest
from tokeo.controllers.base import BaseController
from tokeo.core.exc import TokeoError
from tokeo.core.utils.cement.controllers import subcmdmeta, defaultmeta, log_info_help
from tokeo.core.utils.cement.extensions import required as extensions_required


class HelpersController(Controller):
    """Records what the helpers see inside a command and its default."""

    class Meta:
        label = 'helpers'
        stacked_on = 'base'
        stacked_type = 'nested'
        default_func = 'standard'

    def _record(self):
        self.app.seen.update(subcmd=subcmdmeta(self), default=defaultmeta(self))
        if subcmdmeta(self):
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
        self.app.seen.update(default=defaultmeta(self))


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
    logged = []
    with CementTest(argv=argv) as app:
        app.seen = {}
        if monkeypatch:
            monkeypatch.setattr(app.log, 'info', lambda msg, **kw: logged.append(msg))
        app.run()
        return app.seen, logged


# --- controllers ----------------------------------------------------------------


def test_controllers_subcmdmeta_is_the_dispatched_command():
    seen, _ = _run(['helpers', 'command'])
    assert seen['subcmd'].func_name == 'command'
    assert seen['subcmd'].parser_options['help'] == 'the help of command'


def test_controllers_subcmdmeta_is_none_in_the_default():
    seen, _ = _run(['helpers'])
    assert seen['subcmd'] is None


def test_controllers_defaultmeta_is_the_exposed_default():
    # inside the command and inside the default itself
    for argv in (['helpers', 'command'], ['helpers']):
        seen, _ = _run(argv)
        assert seen['default'].func_name == 'standard'
        assert seen['default'].parser_options['help'] == 'the help of standard'


def test_controllers_defaultmeta_is_none_without_exposed_default():
    seen, _ = _run(['nodefault', 'command'])
    assert seen['default'] is None


def test_controllers_log_info_help_logs_the_help(monkeypatch):
    _, logged = _run(['helpers', 'command'], monkeypatch)
    assert logged == ['the help of command']


# --- extensions -----------------------------------------------------------------


def test_extensions_required_names_the_missing_ones():
    with CementTest() as app:
        extensions_required(app, 'tokeo.ext.probe', 'tokeo.ext.tui.output')
        with pytest.raises(TokeoError, match='needs tokeo.ext.tui.input and tokeo.ext.x, add them to the extensions of the app!'):
            extensions_required(app, 'tokeo.ext.probe', 'tokeo.ext.tui.input', 'tokeo.ext.tui.output', 'tokeo.ext.x')
