"""
Tests for tokeo.ext.tui.input.

The shell gets its keys from a pipe; one test runs a real app in a pseudo
terminal and reads its screen by pyte, a terminal emulator.
"""

import os
import sys
import time
import signal
import logging
import subprocess

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.history import InMemoryHistory
from cement.core.exc import CaughtSignal
from tokeo.main import TokeoTest
from tokeo.ext.tui import TokeoTuiError
from tokeo.ext.tui.input import TokeoTuiInput

CTRL_C, CTRL_D = '\x03', '\x04'


class InputTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.tui.input',
        ]


@pytest.fixture
def shell():
    # runs the shell of an app on the given keys, returns the entered lines
    def run(keys, command=None, **options):
        lines = []

        def record(line):
            lines.append(line)
            return command(line) if command else True

        with InputTest() as app, create_pipe_input() as keyboard:
            keyboard.send_text(keys)
            app.tui.input.shell(record, input=keyboard, output=DummyOutput(), **options)
        return lines

    return run


def test_extension_registers_input_only():
    with InputTest() as app:
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
    pyte = pytest.importorskip('pyte')
    import pty
    import fcntl
    import select
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 12, 80, 0, 0))
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    env = dict(os.environ, TERM='xterm-256color', PYTHONPATH=os.pathsep.join(filter(None, [root, os.environ.get('PYTHONPATH')])))
    probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tui_shell_probe.py')
    process = subprocess.Popen([sys.executable, probe], stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    screen = pyte.Screen(80, 12)
    stream = pyte.ByteStream(screen)
    shown = None
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline and process.poll() is None:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    # the probe ended and closed the terminal
                    break
                stream.feed(data)
                # answer the cursor position requests like a terminal
                for _ in range(data.count(b'\x1b[6n')):
                    os.write(master, f'\x1b[{screen.cursor.y + 1};{screen.cursor.x + 1}R'.encode())
            lines = [line.rstrip() for line in screen.display if line.strip()]
            if shown is None and 'MARK-APP-PRINT' in ''.join(lines) and lines[-1] == 'Probe>':
                shown = lines
                os.write(master, b'exit\r')
    finally:
        process.kill() if process.poll() is None else None
        process.wait(10)
        os.close(master)
    assert shown is not None, '\n'.join(screen.display)
    # each output on its own line above the prompt, the prompt untouched
    assert 'MARK-PRINT' in shown
    assert any(line.endswith('MARK-LOG') and 'Probe>' not in line for line in shown)
    assert 'MARK-APP-PRINT' in shown
    assert shown[-1] == 'Probe>'
