"""
Tests for the shell of tokeo.ext.automate and its lists.

The shell gets its keys from a pipe; one test runs a real app in a pseudo
terminal and reads its screen by pyte, a terminal emulator.
"""

import os
import sys
import json
import time
import subprocess

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from tokeo.main import TokeoTest
from tokeo.ext.tui import TokeoTuiError
from tokeo.ext.automate import TokeoAutomateShell
from tests.ext.test_ext_automate import AutomateTest, defaults

CTRL_C, CTRL_D = '\x03', '\x04'


class WithoutTuiTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.automate',
        ]


@pytest.fixture
def shell(monkeypatch):
    # the shell of an app with a wide output
    monkeypatch.setenv('COLUMNS', '200')
    with AutomateTest(config_defaults=defaults) as app:
        yield TokeoAutomateShell(app)


def _lines(capsys):
    return [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_needs_the_tui_extensions():
    with pytest.raises(
        TokeoTuiError, match='The extension tokeo.ext.automate needs tokeo.ext.tui.input and tokeo.ext.tui.output, add them'
    ):
        with WithoutTuiTest(config_defaults=defaults):
            pass


def test_shell_switch_no_colors_removed():
    with AutomateTest(argv=['automate', 'shell', '--no-colors'], config_defaults=defaults) as app:
        with pytest.raises(SystemExit):
            app.run()


def test_list_tasks(shell, capsys):
    capsys.readouterr()
    assert shell.command('list')
    lines = _lines(capsys)
    assert lines[1] == 'tasks' and lines[2].split() == ['id', 'name', 'function', 'hosts']
    task2 = next(line for line in lines if line.startswith('task2'))
    hosts = str(len(shell.app.automate.tasks['task2']['connection']['hosts']))
    assert task2.split() == ['task2', 'Ping', 'our', 'hosts', 'tests.ext.test_ext_automate:task2', hosts]


def test_list_hosts_without_passwords(shell, capsys):
    capsys.readouterr()
    shell.command('hosts list')
    text = capsys.readouterr().out
    assert 'password1' not in text and 'sudo1' not in text and 'identity1' not in text
    host1 = next(line for line in text.splitlines() if line.strip().startswith('host1'))
    assert host1.split() == ['host1', 'host1', 'ip_address1', '22', 'user1', 'identity,', 'password,', 'sudo']


def test_list_hostgroups(shell, capsys):
    capsys.readouterr()
    shell.command('hostgroups list')
    group1 = next(line for line in _lines(capsys) if line.startswith('group1'))
    assert group1.split() == ['group1', 'host1,', 'host2']


def test_list_connections_defaults_first(shell, capsys):
    capsys.readouterr()
    shell.command('connections list')
    lines = _lines(capsys)
    assert lines[3].startswith('_defaults') and 'password_connect_base' not in '\n'.join(lines)
    assert any(line.startswith('con1') for line in lines[4:])


def test_show_as_json(shell, capsys):
    capsys.readouterr()
    shell.command('show task2')
    text = capsys.readouterr().out
    start = text.index('{')
    task = json.loads(text[start:])
    assert (task['id'], task['name']) == ('task2', 'Ping our hosts')


def test_show_unknown_task(shell, monkeypatch):
    from cement.ext.ext_logging import LoggingLogHandler

    errors = []
    monkeypatch.setattr(LoggingLogHandler, 'error', lambda self, msg, **kw: errors.append(msg))
    shell.command('show nope')
    assert errors == ['Task nope not found!']


def test_shell_shows_a_failing_command(shell, monkeypatch):
    from cement.ext.ext_logging import LoggingLogHandler

    def run(*args, **kwargs):
        raise RuntimeError('cannot run')

    errors = []
    monkeypatch.setattr(LoggingLogHandler, 'error', lambda self, msg, **kw: errors.append(msg))
    monkeypatch.setattr(shell.app.automate, 'run_sequential', run)
    with create_pipe_input() as keyboard:
        keyboard.send_text(f'run task1\r{CTRL_C}{CTRL_D}')
        shell.shell(input=keyboard, output=DummyOutput())
    assert errors == ['Shell command failed: cannot run']


@pytest.mark.skipif(sys.platform == 'win32', reason='needs a pseudo terminal')
def test_shell_in_a_terminal():
    pyte = pytest.importorskip('pyte')
    import pty
    import fcntl
    import select
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 120, 0, 0))
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ, TERM='xterm-256color', PYTHONPATH=os.pathsep.join(filter(None, [root, os.environ.get('PYTHONPATH')])))
    probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'automate_shell_probe.py')
    process = subprocess.Popen([sys.executable, probe, 'automate', 'shell'], stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    screen = pyte.Screen(120, 24)
    stream = pyte.ByteStream(screen)
    # each input waits for the output of the one before and the prompt below it
    steps = [(None, b'list\r'), ('function', b'show nope\r'), ('Task nope not found!', b'exit\r')]
    shown = None
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline and process.poll() is None:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                stream.feed(data)
                # answer the cursor position requests like a terminal
                for _ in range(data.count(b'\x1b[6n')):
                    os.write(master, f'\x1b[{screen.cursor.y + 1};{screen.cursor.x + 1}R'.encode())
            lines = [line.rstrip() for line in screen.display if line.strip()]
            if steps and lines and lines[-1] == 'Automate>' and (steps[0][0] is None or any(steps[0][0] in line for line in lines)):
                if len(steps) == 1:
                    shown = lines
                os.write(master, steps.pop(0)[1])
        code = process.wait(10)
    finally:
        process.kill() if process.poll() is None else None
        os.close(master)
    assert shown is not None, '\n'.join(screen.display)
    # the table of the tasks, then the error of show above the prompt
    assert any(line.split()[:4] == ['id', 'name', 'function', 'hosts'] for line in shown)
    assert any(line.endswith('Task nope not found!') and 'Automate>' not in line for line in shown)
    assert shown[-1] == 'Automate>' and code == 0
