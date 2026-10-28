"""
Tests for tokeo.ext.tui.input: the shell with its lines, history, signals and
the output of other threads. The shell gets its keys from a pipe; the tests
of the output and of the signals run a real app in a pseudo terminal and read
its screen by pyte, a terminal emulator. SIGTERM and SIGHUP end the shell,
also while the prompt waits; Ctrl-C and SIGINT abort only the line or the
running command.
"""

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

needs_pty = pytest.mark.skipif(sys.platform == 'win32', reason='needs a pseudo terminal')


@pytest.fixture
def shell():
    """Runs the shell of an app on the given keys, returns the entered lines."""

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
    """Only app.tui.input exists, app.tui.output names the missing extension."""
    with TuiInputTest() as app:
        assert isinstance(app.tui.input, TokeoTuiInput)
        with pytest.raises(TokeoTuiError, match='Enable the extension tokeo.ext.tui.output'):
            app.tui.output


def test_lines_until_ctrl_d(shell):
    """The shell hands each line to the command until Ctrl-D, into the history."""
    history = InMemoryHistory()
    assert shell(f'list\rtasks pause 1\r{CTRL_D}', history=history, completer={'list': None, 'tasks': {'pause': None}}) == [
        'list',
        'tasks pause 1',
    ]
    assert list(history.get_strings()) == ['list', 'tasks pause 1']


def test_history_from_a_list(shell):
    """With a list as history arrow up brings its last line."""
    assert shell(f'\x1b[A\r{CTRL_D}', history=['exit', 'list']) == ['list']


def test_failed_line_stays_until_ctrl_c(shell):
    """A line the command rejects stays for the next enter until Ctrl-C."""
    assert shell(f'typo\r\r{CTRL_C}ok\r{CTRL_D}', command=lambda line: line != 'typo') == ['typo', 'typo', 'ok']


def test_failing_command_is_logged_and_shell_goes_on(shell, monkeypatch):
    """An exception of the command is logged as error, the shell goes on."""
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
    """EOFError of the command ends the shell, later lines are not read."""

    def command(line):
        if line == 'exit':
            raise EOFError()
        return True

    assert shell('one\rexit\rnever\r', command=command) == ['one', 'exit']


@pytest.mark.parametrize('signum', [signal.SIGTERM, signal.SIGHUP])
def test_shutdown_signal_ends_shell(shell, signum):
    """SIGTERM or SIGHUP raised by the command ends the shell."""

    def command(line):
        raise CaughtSignal(signum, None)

    assert shell('one\rtwo\r', command=command) == ['one']


def test_sigint_of_a_command_aborts_only_the_command(shell):
    """SIGINT raised by the command aborts it, the shell reads the next line."""

    def command(line):
        if line == 'one':
            raise CaughtSignal(signal.SIGINT, None)
        return True

    assert shell(f'one\rtwo\r{CTRL_D}', command=command) == ['one', 'two']


def test_signal_handlers_of_the_app_return_after_the_shell(shell):
    """The shell wraps SIGTERM and SIGHUP of the app, then restores them."""
    handlers = []

    def command(line):
        handlers.append((signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)))
        return True

    with TuiInputTest() as app, create_pipe_input() as keyboard:
        before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))
        keyboard.send_text(f'x\r{CTRL_D}')
        app.tui.input.shell(command, input=keyboard, output=DummyOutput())
        after = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))
    assert callable(before[0]) and callable(before[1])
    assert handlers[0][0] is not before[0] and handlers[0][1] is not before[1]
    assert after == before


def test_default_signal_stays_as_it_is(shell):
    """A signal the app does not handle keeps its default during the shell."""
    seen = []
    with TuiInputTest() as app, create_pipe_input() as keyboard:
        previous = signal.signal(signal.SIGHUP, signal.SIG_DFL)
        try:
            keyboard.send_text(f'x\r{CTRL_D}')
            app.tui.input.shell(lambda line: seen.append(signal.getsignal(signal.SIGHUP)) or True, input=keyboard, output=DummyOutput())
        finally:
            signal.signal(signal.SIGHUP, previous)
    assert seen == [signal.SIG_DFL]


def test_other_signal_passes(shell):
    """Another signal raised by the command passes out of the shell."""

    def command(line):
        raise CaughtSignal(signal.SIGUSR1, None)

    with pytest.raises(CaughtSignal):
        shell('one\r', command=command)


def test_log_handlers_write_to_the_shell_meanwhile(shell):
    """A console log handler writes through the shell while it runs."""
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


@needs_pty
def test_output_of_other_threads_appears_above_the_prompt():
    """
    The probe prints, logs and calls app.print from another thread while
    its shell waits; each output stands on its own line above the prompt.
    """
    probe = 'tests.ext.tui.tui_tests_input_probe'
    with Terminal([sys.executable, '-m', probe], rows=12, columns=80) as terminal:
        shown = terminal.wait_for(lambda lines: 'MARK-APP-PRINT' in ''.join(lines) and lines[-1] == 'Probe>')
        assert shown is not None, '\n'.join(terminal.screen.display)
        terminal.send(b'exit\r')
        terminal.wait()
    # each output on its own line above the prompt, the prompt untouched
    assert 'MARK-PRINT' in shown
    assert any(line.endswith('MARK-LOG') and 'Probe>' not in line for line in shown)
    assert 'MARK-APP-PRINT' in shown
    assert shown[-1] == 'Probe>'


PROBE = 'tests.ext.tui.tui_tests_input_probe'


def _probe():
    """The probe in a pseudo terminal that is its controlling terminal."""
    return Terminal([sys.executable, '-m', PROBE], rows=12, columns=80, ctty=True)


@needs_pty
@pytest.mark.parametrize('signum', [signal.SIGTERM, signal.SIGHUP], ids=['sigterm', 'sighup'])
def test_shutdown_signal_ends_the_waiting_prompt(signum):
    """SIGTERM or SIGHUP at the waiting prompt end the probe by 0, no ENTER."""
    with _probe() as terminal:
        assert terminal.wait_for(lambda lines: lines[-1] == 'Probe>'), '\n'.join(terminal.screen.display)
        terminal.process.send_signal(signum)
        code = terminal.wait()
    assert code == 0
    assert b'Press ENTER' not in terminal.raw


@needs_pty
def test_sigint_and_ctrl_c_at_the_prompt_keep_the_shell():
    """A SIGINT and a Ctrl-C at the prompt clear the line, the shell goes on."""
    with _probe() as terminal:
        assert terminal.wait_for(lambda lines: lines[-1] == 'Probe>'), '\n'.join(terminal.screen.display)
        terminal.process.send_signal(signal.SIGINT)
        terminal.send(f'abc{CTRL_C}'.encode())
        assert terminal.wait_for(lambda lines: lines[-1] == 'Probe>' and 'Probe> abc' in lines)
        terminal.send(b'exit\r')
        code = terminal.wait()
    assert code == 0


@needs_pty
def test_ctrl_c_aborts_the_running_command():
    """Ctrl-C while the command slow runs aborts it, the prompt comes back."""
    with _probe() as terminal:
        assert terminal.wait_for(lambda lines: lines[-1] == 'Probe>'), '\n'.join(terminal.screen.display)
        terminal.send(b'slow\r')
        assert terminal.wait_for(lambda lines: 'MARK-SLOW-START' in lines)
        terminal.send(CTRL_C.encode())
        back = terminal.wait_for(lambda lines: lines[-1] == 'Probe>' and lines.index('MARK-SLOW-START') < len(lines) - 1)
        assert back is not None, '\n'.join(terminal.screen.display)
        terminal.send(b'exit\r')
        code = terminal.wait()
    assert code == 0
    assert b'MARK-SLOW-END' not in terminal.raw


@needs_pty
def test_sigterm_ends_the_running_command_and_the_shell():
    """SIGTERM while slow runs ends the probe by 0, the command does not finish."""
    with _probe() as terminal:
        assert terminal.wait_for(lambda lines: lines[-1] == 'Probe>'), '\n'.join(terminal.screen.display)
        terminal.send(b'slow\r')
        assert terminal.wait_for(lambda lines: 'MARK-SLOW-START' in lines)
        terminal.process.send_signal(signal.SIGTERM)
        code = terminal.wait()
    assert code == 0
    assert b'MARK-SLOW-END' not in terminal.raw
