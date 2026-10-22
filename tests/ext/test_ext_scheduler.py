"""
Tests for tokeo.ext.scheduler.

The shell gets its keys from a pipe; one test runs a real app in a pseudo
terminal and reads its screen by pyte, a terminal emulator.
"""

import os
import sys
import time
import subprocess

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.exc import TokeoError

CTRL_D = '\x04'


class SchedulerTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.tui.input',
            'tokeo.ext.tui.output',
            'tokeo.ext.scheduler',
        ]


class WithoutTuiTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.tui.input',
            'tokeo.ext.scheduler',
        ]


def _config():
    # time.time as task, it is never due during a test
    config = init_defaults('scheduler')
    config['scheduler']['tasks'] = {'time': dict(module='time', crontab='0 0 1 1 *', name='the time')}
    return config


@pytest.fixture
def started(monkeypatch):
    # an app with its scheduler running in the background, no shell
    with SchedulerTest(config_defaults=_config()) as app:
        app.scheduler.startup(interactive=True)
        yield app


def test_needs_the_tui_extensions():
    with pytest.raises(TokeoError, match='The extension tokeo.ext.scheduler needs tokeo.ext.tui.output, add it to the extensions'):
        with WithoutTuiTest():
            pass


def test_launch_without_shell_by_default(monkeypatch):
    calls = []
    with SchedulerTest(config_defaults=_config()) as app:
        monkeypatch.setattr(app.scheduler, 'startup', lambda interactive, paused: calls.append(('startup', interactive, paused)))
        monkeypatch.setattr(app.scheduler, 'shell', lambda: calls.append(('shell',)))
        app.scheduler.launch()
        app.scheduler.launch(interactive=True, paused=True)
    assert calls == [('startup', False, False), ('startup', True, True), ('shell',)]


@pytest.mark.parametrize('argv, expected', [([], (False, False)), (['--shell'], (True, False)), (['--shell', '--paused'], (True, True))])
def test_launch_command(monkeypatch, argv, expected):
    calls = []
    with SchedulerTest(argv=['scheduler', 'launch', *argv], config_defaults=_config()) as app:
        monkeypatch.setattr(app.scheduler, 'launch', lambda interactive, paused: calls.append((interactive, paused)))
        app.run()
    assert calls == [expected]


@pytest.mark.parametrize('switch', ['--background', '--no-colors'])
def test_launch_switches_removed(switch):
    with SchedulerTest(argv=['scheduler', 'launch', switch], config_defaults=_config()) as app:
        with pytest.raises(SystemExit):
            app.run()


def test_list_table(started, capsys, monkeypatch):
    monkeypatch.setenv('COLUMNS', '160')
    capsys.readouterr()
    assert started.scheduler.command('list')
    lines = [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]
    # the time and the state of the scheduler head the table
    assert lines[0].endswith('scheduler running')
    assert lines[1] == 'tasks'
    assert lines[2].split() == ['id', 'name', 'trigger', 'next', 'run', 'state']
    row = lines[4].split()
    assert row[:4] == ['1', '1:the', 'time', "cron[month='1',"] and row[-1] == 'active'


def test_list_shows_paused_tasks_and_scheduler(started, capsys):
    started.scheduler.command('tasks pause 1')
    started.scheduler.command('pause')
    capsys.readouterr()
    started.scheduler.command('list')
    lines = [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines[0].endswith('scheduler not running') and lines[4].split()[-1] == 'paused'


def test_shell_runs_commands(started):
    with create_pipe_input() as keyboard:
        keyboard.send_text('tasks pause 1\rexit\r')
        started.scheduler.shell(input=keyboard, output=DummyOutput())
    assert started.scheduler.scheduler.get_job('1').next_run_time is None


def test_shell_keeps_a_wrong_command(started):
    # an unknown command fails and stays, Ctrl-C clears it, Ctrl-D ends the shell
    calls = []
    original = started.scheduler.command

    def command(line):
        calls.append(line)
        return original(line)

    started.scheduler.command = command
    with create_pipe_input() as keyboard:
        keyboard.send_text(f'nonsense\r\x03{CTRL_D}')
        started.scheduler.shell(input=keyboard, output=DummyOutput())
    assert calls == ['nonsense']


def test_shell_shows_a_failing_command(started, monkeypatch):
    from cement.ext.ext_logging import LoggingLogHandler

    def pause():
        raise RuntimeError('cannot pause')

    errors = []
    monkeypatch.setattr(LoggingLogHandler, 'error', lambda self, msg, **kw: errors.append(msg))
    monkeypatch.setattr(started.scheduler.scheduler, 'pause', pause)
    with create_pipe_input() as keyboard:
        keyboard.send_text(f'pause\r\x03{CTRL_D}')
        started.scheduler.shell(input=keyboard, output=DummyOutput())
    assert errors == ['Shell command failed: cannot pause']


@pytest.mark.skipif(sys.platform == 'win32', reason='needs a pseudo terminal')
def test_shell_shows_logs_of_tasks_above_the_prompt():
    pyte = pytest.importorskip('pyte')
    import pty
    import fcntl
    import select
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 16, 100, 0, 0))
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ, TERM='xterm-256color', PYTHONPATH=os.pathsep.join(filter(None, [root, os.environ.get('PYTHONPATH')])))
    probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scheduler_shell_probe.py')
    process = subprocess.Popen([sys.executable, probe, 'scheduler', 'launch', '--shell'], stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    screen = pyte.Screen(100, 16)
    stream = pyte.ByteStream(screen)
    sent, shown = None, None
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
            if sent is None and lines and lines[-1] == 'Scheduler>':
                os.write(master, b'tasks fire 1\r')
                sent = time.monotonic()
            if shown is None and any(line.endswith('MARK-PING') for line in lines) and lines[-1] == 'Scheduler>':
                shown = lines
                os.write(master, b'exit\r')
        code = process.wait(10)
    finally:
        process.kill() if process.poll() is None else None
        os.close(master)
    assert shown is not None, '\n'.join(screen.display)
    # the log line of the task stands on its own line, the prompt below it
    assert any(line.endswith('MARK-PING') and 'Scheduler>' not in line for line in shown)
    assert shown[-1] == 'Scheduler>' and code == 0
