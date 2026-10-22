"""
Tests for tokeo.ext.tui.input.

The shell gets its keys from a pipe; one test runs a real app in a pseudo
terminal and reads its screen by pyte, a terminal emulator.
"""

import os
import sys
import signal
import logging

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.history import InMemoryHistory
from cement.core.exc import CaughtSignal
from tokeo.ext.tui import TokeoTuiError
from tokeo.ext.tui.input import TokeoTuiInput
from tests.utils import Terminal
from tests.ext.tui.tui_tests_helper import TuiInputTest

CTRL_C, CTRL_D = '\x03', '\x04'


@pytest.fixture
def shell():
    # runs the shell of an app on the given keys, returns the entered lines
    def run(keys, command=None, **options):
        lines = []

        def record(line):
            lines.append(line)
            return command(line) if command else True

        with TuiInputTest() as app, create_pipe_input() as keyboard:
            keyboard.send_text(keys)
            app.tui.input.shell(record, input=keyboard, output=DummyOutput(), **options)
        return lines

    return run


def test_extension_registers_input_only():
    with TuiInputTest() as app:
        assert isinstance(app.tui.input, TokeoTuiInput)
        with pytest.raises(TokeoTuiError, match='Enable the extension tokeo.ext.tui.output'):
            app.tui.output


def test_lines_until_ctrl_d(shell):
    history = InMemoryHistory()
    assert shell(f'list\rtasks pause 1\r{CTRL_D}', history=history, completer={'list': None, 'tasks': {'pause': None}}) == [
        'list',
        'tasks pause 1',
    ]
    assert list(history.get_strings()) == ['list', 'tasks pause 1']


def test_history_from_a_list(shell):
    # arrow up brings the first line of the list
    assert shell(f'\x1b[A\r{CTRL_D}', history=['exit', 'list']) == ['list']


def test_failed_line_stays_until_ctrl_c(shell):
    # False keeps the line, the next enter sends it again; Ctrl-C clears it
    assert shell(f'typo\r\r{CTRL_C}ok\r{CTRL_D}', command=lambda line: line != 'typo') == ['typo', 'typo', 'ok']


def test_failing_command_is_logged_and_shell_goes_on(shell, monkeypatch):
    from cement.ext.ext_logging import LoggingLogHandler

    def command(line):
        if line == 'boom':
            raise ValueError('kaputt')
        return True

    errors = []
    monkeypatch.setattr(LoggingLogHandler, 'error', lambda self, msg, **kw: errors.append(msg))
    assert shell(f'boom\r{CTRL_C}ok\r{CTRL_D}', command=command) == ['boom', 'ok']
    assert errors == ['Shell command failed: kaputt']


def test_command_ends_shell_by_eof(shell):
    def command(line):
        if line == 'exit':
            raise EOFError()
        return True

    assert shell('one\rexit\rnever\r', command=command) == ['one', 'exit']


@pytest.mark.parametrize('signum', [signal.SIGINT, signal.SIGTERM])
def test_shutdown_signal_ends_shell(shell, signum):
    def command(line):
        raise CaughtSignal(signum, None)

    assert shell('one\rtwo\r', command=command) == ['one']


def test_other_signal_passes(shell):
    def command(line):
        raise CaughtSignal(signal.SIGUSR1, None)

    with pytest.raises(CaughtSignal):
        shell('one\r', command=command)


def test_log_handlers_write_to_the_shell_meanwhile(shell):
    # a handler set up on the console before the shell, as cement does
    handler = logging.StreamHandler(sys.stderr)
    logger = logging.getLogger('tokeo_test_tui')
    logger.addHandler(handler)
    streams = []
    try:
        shell(f'x\r{CTRL_D}', command=lambda line: streams.append((handler.stream, sys.stdout)) or True)
    finally:
        logger.removeHandler(handler)
    ((during, proxy),) = streams
    assert during is proxy and handler.stream is sys.stderr


@pytest.mark.skipif(sys.platform == 'win32', reason='needs a pseudo terminal')
def test_output_of_other_threads_appears_above_the_prompt():
    probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tui_tests_input_probe.py')
    with Terminal([sys.executable, probe], rows=12, columns=80) as terminal:
        shown = terminal.wait_for(lambda lines: 'MARK-APP-PRINT' in ''.join(lines) and lines[-1] == 'Probe>')
        assert shown is not None, '\n'.join(terminal.screen.display)
        terminal.send(b'exit\r')
        terminal.wait()
    # each output on its own line above the prompt, the prompt untouched
    assert 'MARK-PRINT' in shown
    assert any(line.endswith('MARK-LOG') and 'Probe>' not in line for line in shown)
    assert 'MARK-APP-PRINT' in shown
    assert shown[-1] == 'Probe>'
