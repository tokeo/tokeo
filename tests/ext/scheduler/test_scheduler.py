"""
Tests for tokeo.ext.scheduler: the trigger and its delay, launch and its
switches, the list of the tasks, reload and the shell. The shell gets its
keys from a pipe; one test runs a real app in a pseudo terminal and reads its
screen by pyte, a terminal emulator.
"""

import re
import sys
import types
from datetime import datetime, timezone

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from tokeo.core.exc import TokeoError
from tokeo.ext.scheduler import TokeoCronAndFireTrigger, TokeoSchedulerError
from tests.utils.tui import Terminal
from tests.ext.scheduler.scheduler_tests_helper import SchedulerTest

CTRL_D = '\x04'

needs_pty = pytest.mark.skipif(sys.platform == 'win32', reason='needs a pseudo terminal')


class SchedulerWithoutTuiTest(SchedulerTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.tui.input',
            'tokeo.ext.scheduler',
        ]


@pytest.fixture
def started_app():
    """An app with its scheduler running in the background, no shell."""
    with SchedulerTest() as app:
        app.scheduler.startup(with_shell=True)
        yield app


@pytest.mark.parametrize(
    'crontab, delay_seconds, expected',
    [
        ('0 8 * * *', None, datetime(2026, 11, 1, 8, 0, 0, tzinfo=timezone.utc)),
        ('0 8 * * *', 5, datetime(2026, 11, 1, 8, 0, 5, tzinfo=timezone.utc)),
        ('0 0 30 2 *', None, None),
        ('0 0 30 2 *', 5, None),
    ],
    ids=['daily', 'daily_delayed', 'never', 'never_delayed'],
)
def test_trigger_puts_the_delay_on_a_next_fire_time_only(crontab, delay_seconds, expected):
    """The delay goes on top of a next fire time, a never firing cron stays None."""
    trigger = TokeoCronAndFireTrigger.from_crontab(crontab, delay_seconds=delay_seconds, timezone='UTC')
    assert trigger.get_next_fire_time(None, datetime(2026, 11, 1, tzinfo=timezone.utc)) == expected


def test_init_tasks_refuses_an_unknown_coalesce():
    """coalesce 'newest' raises TokeoSchedulerError, also a ValueError."""
    with SchedulerTest(tasks={'time': dict(module='time', crontab='0 0 1 1 *', coalesce='newest')}) as app:
        with pytest.raises(ValueError, match='^Unsupported value "newest" for coalesce option$') as err_info:
            app.scheduler.init_tasks()
    assert type(err_info.value) is TokeoSchedulerError


@pytest.mark.parametrize(
    'crontab, error',
    [
        ('0 0 1 1', 'Wrong number of fields; got 4, expected 5'),
        ('0 25 * * *', 'Error validating expression'),
    ],
    ids=['fields', 'hour'],
)
def test_init_tasks_names_the_task_of_a_wrong_crontab(crontab, error):
    """A wrong crontab entry raises TokeoSchedulerError naming task and entry."""
    # raised out of the app like in main, the app is not closed then
    with pytest.raises(TokeoSchedulerError, match='^' + re.escape(f'Task "time" with crontab "{crontab}": {error}')) as err_info:
        with SchedulerTest(tasks={'time': dict(module='time', crontab=['0 0 1 1 *', crontab])}) as app:
            app.scheduler.init_tasks()
    assert isinstance(err_info.value, ValueError)
    assert type(err_info.value.__cause__) is ValueError


def test_reload_keeps_the_ids_of_the_tasks(started_app):
    """After a reload the tasks have the same ids, pause 1 still means the first."""
    before = sorted(job.id for job in started_app.scheduler.scheduler.get_jobs())
    started_app.scheduler.handle_command_reload(types.SimpleNamespace(restart=False))
    assert sorted(job.id for job in started_app.scheduler.scheduler.get_jobs()) == before


def test_needs_the_tui_extensions():
    """Without tokeo.ext.tui.output the app names the missing extension."""
    with pytest.raises(TokeoError, match='The extension tokeo.ext.scheduler needs tokeo.ext.tui.output, add it to the extensions'):
        with SchedulerWithoutTuiTest():
            pass


def test_launch_without_shell_by_default(monkeypatch):
    """launch starts without shell, with with_shell=True the shell follows."""
    calls = []
    with SchedulerTest() as app:
        monkeypatch.setattr(app.scheduler, 'startup', lambda with_shell, paused: calls.append(('startup', with_shell, paused)))
        monkeypatch.setattr(app.scheduler, 'run_shell', lambda: calls.append(('run_shell',)))
        app.scheduler.launch()
        app.scheduler.launch(with_shell=True, paused=True)
    assert calls == [('startup', False, False), ('startup', True, True), ('run_shell',)]


@pytest.mark.parametrize('argv, expected', [([], (False, False)), (['--shell'], (True, False)), (['--shell', '--paused'], (True, True))])
def test_launch_command(monkeypatch, argv, expected):
    """scheduler launch hands --shell and --paused to launch."""
    calls = []
    with SchedulerTest(argv=['scheduler', 'launch', *argv]) as app:
        monkeypatch.setattr(app.scheduler, 'launch', lambda with_shell, paused: calls.append((with_shell, paused)))
        app.run()
    assert calls == [expected]


@pytest.mark.parametrize('switch', ['--background', '--no-colors'])
def test_launch_switches_removed(switch):
    """--background and --no-colors are no switches any more."""
    with SchedulerTest(argv=['scheduler', 'launch', switch]) as app:
        with pytest.raises(SystemExit):
            app.run()


def test_list_table(started_app, capsys, monkeypatch):
    """list shows the state of the scheduler and a table of the tasks."""
    monkeypatch.setenv('COLUMNS', '160')
    capsys.readouterr()
    assert started_app.scheduler.run_command('list')
    lines = [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]
    # the time and the state of the scheduler head the table
    assert lines[0].endswith('scheduler running')
    assert lines[1] == 'tasks'
    assert lines[2].split() == ['id', 'name', 'trigger', 'next', 'run', 'state']
    row = lines[4].split()
    assert row[:4] == ['1', '1:the', 'time', "cron[month='1',"] and row[-1] == 'active'


def test_list_shows_paused_tasks_and_scheduler(started_app, capsys):
    """list shows a paused task and a paused scheduler."""
    started_app.scheduler.run_command('tasks pause 1')
    started_app.scheduler.run_command('pause')
    capsys.readouterr()
    started_app.scheduler.run_command('list')
    lines = [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines[0].endswith('scheduler not running') and lines[4].split()[-1] == 'paused'


def test_shell_runs_commands(started_app):
    """The shell runs "tasks pause 1", the task has no next run time."""
    with create_pipe_input() as keyboard:
        keyboard.send_text('tasks pause 1\rexit\r')
        started_app.scheduler.run_shell(input=keyboard, output=DummyOutput())
    assert started_app.scheduler.scheduler.get_job('1').next_run_time is None


def test_shell_keeps_a_wrong_command(started_app):
    """An unknown command is tried once, Ctrl-C clears it."""
    calls = []
    original = started_app.scheduler.run_command

    def run_command(line):
        calls.append(line)
        return original(line)

    started_app.scheduler.run_command = run_command
    with create_pipe_input() as keyboard:
        keyboard.send_text(f'nonsense\r\x03{CTRL_D}')
        started_app.scheduler.run_shell(input=keyboard, output=DummyOutput())
    assert calls == ['nonsense']


def test_shell_shows_a_failing_command(started_app, monkeypatch):
    """A failing command is logged as "Shell command failed: ..."."""
    from cement.ext.ext_logging import LoggingLogHandler

    def pause():
        raise RuntimeError('cannot pause')

    errors = []
    monkeypatch.setattr(LoggingLogHandler, 'error', lambda self, msg, **kw: errors.append(msg))
    monkeypatch.setattr(started_app.scheduler.scheduler, 'pause', pause)
    with create_pipe_input() as keyboard:
        keyboard.send_text(f'pause\r\x03{CTRL_D}')
        started_app.scheduler.run_shell(input=keyboard, output=DummyOutput())
    assert errors == ['Shell command failed: cannot pause']


@needs_pty
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
