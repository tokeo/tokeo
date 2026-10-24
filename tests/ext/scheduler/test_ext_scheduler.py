"""
Tests for tokeo.ext.scheduler.

The shell gets its keys from a pipe; one test runs a real app in a pseudo
terminal and reads its screen by pyte, a terminal emulator.
"""

import sys

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from tokeo.core.exc import TokeoError
from tests.utils import Terminal
from tests.ext.scheduler.scheduler_tests_helper import SchedulerTest

CTRL_D = '\x04'


class SchedulerWithoutTuiTest(SchedulerTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.tui.input',
            'tokeo.ext.scheduler',
        ]


@pytest.fixture
def started():
    """An app with its scheduler running in the background, no shell."""
    with SchedulerTest() as app:
        app.scheduler.startup(interactive=True)
        yield app


def test_needs_the_tui_extensions():
    """Without tokeo.ext.tui.output the app names the missing extension."""
    with pytest.raises(TokeoError, match='The extension tokeo.ext.scheduler needs tokeo.ext.tui.output, add it to the extensions'):
        with SchedulerWithoutTuiTest():
            pass


def test_launch_without_shell_by_default(monkeypatch):
    """launch starts without shell, with interactive=True the shell follows."""
    calls = []
    with SchedulerTest() as app:
        monkeypatch.setattr(app.scheduler, 'startup', lambda interactive, paused: calls.append(('startup', interactive, paused)))
        monkeypatch.setattr(app.scheduler, 'shell', lambda: calls.append(('shell',)))
        app.scheduler.launch()
        app.scheduler.launch(interactive=True, paused=True)
    assert calls == [('startup', False, False), ('startup', True, True), ('shell',)]


@pytest.mark.parametrize('argv, expected', [([], (False, False)), (['--shell'], (True, False)), (['--shell', '--paused'], (True, True))])
def test_launch_command(monkeypatch, argv, expected):
    """scheduler launch hands --shell and --paused to launch."""
    calls = []
    with SchedulerTest(argv=['scheduler', 'launch', *argv]) as app:
        monkeypatch.setattr(app.scheduler, 'launch', lambda interactive, paused: calls.append((interactive, paused)))
        app.run()
    assert calls == [expected]


@pytest.mark.parametrize('switch', ['--background', '--no-colors'])
def test_launch_switches_removed(switch):
    """--background and --no-colors are no switches any more."""
    with SchedulerTest(argv=['scheduler', 'launch', switch]) as app:
        with pytest.raises(SystemExit):
            app.run()


def test_list_table(started, capsys, monkeypatch):
    """list shows the state of the scheduler and a table of the tasks."""
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
    """list shows a paused task and a paused scheduler."""
    started.scheduler.command('tasks pause 1')
    started.scheduler.command('pause')
    capsys.readouterr()
    started.scheduler.command('list')
    lines = [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines[0].endswith('scheduler not running') and lines[4].split()[-1] == 'paused'


def test_shell_runs_commands(started):
    """The shell runs "tasks pause 1", the task has no next run time."""
    with create_pipe_input() as keyboard:
        keyboard.send_text('tasks pause 1\rexit\r')
        started.scheduler.shell(input=keyboard, output=DummyOutput())
    assert started.scheduler.scheduler.get_job('1').next_run_time is None


def test_shell_keeps_a_wrong_command(started):
    """An unknown command is tried once, Ctrl-C clears it."""
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
    """A failing command is logged as "Shell command failed: ..."."""
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
    """
    The probe runs the scheduler shell; "tasks fire 1" makes its task log,
    the log line stands above the prompt and exit ends with 0.
    """
    probe = 'tests.ext.scheduler.scheduler_tests_shell_probe'
    with Terminal([sys.executable, '-m', probe, 'scheduler', 'launch', '--shell'], rows=16, columns=100) as terminal:
        assert terminal.wait_for(lambda lines: lines[-1] == 'Scheduler>'), '\n'.join(terminal.screen.display)
        terminal.send(b'tasks fire 1\r')
        shown = terminal.wait_for(lambda lines: any(line.endswith('MARK-PING') for line in lines) and lines[-1] == 'Scheduler>')
        assert shown is not None, '\n'.join(terminal.screen.display)
        terminal.send(b'exit\r')
        code = terminal.wait()
    # the log line of the task stands on its own line, the prompt below it
    assert any(line.endswith('MARK-PING') and 'Scheduler>' not in line for line in shown)
    assert shown[-1] == 'Scheduler>' and code == 0
