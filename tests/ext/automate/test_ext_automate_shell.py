"""
Tests for the shell of tokeo.ext.automate and its lists.

The shell gets its keys from a pipe; one test runs a real app in a pseudo
terminal and reads its screen by pyte, a terminal emulator.
"""

import sys
import json

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from tokeo.core.exc import TokeoError
from tokeo.ext.automate import TokeoAutomateShell
from tests.utils import Terminal
from tests.ext.automate.automate_tests_helper import AutomateTest

CTRL_C, CTRL_D = '\x03', '\x04'


class AutomateWithoutTuiTest(AutomateTest):

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.automate',
        ]


@pytest.fixture
def shell(monkeypatch):
    # the shell of an app with a wide output
    monkeypatch.setenv('COLUMNS', '200')
    with AutomateTest() as app:
        yield TokeoAutomateShell(app)


def _lines(capsys):
    return [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_needs_the_tui_extensions():
    """Without the tui extensions the app names both missing ones."""
    with pytest.raises(TokeoError, match='The extension tokeo.ext.automate needs tokeo.ext.tui.input and tokeo.ext.tui.output, add them'):
        with AutomateWithoutTuiTest():
            pass


def test_shell_switch_no_colors_removed():
    """--no-colors is no switch of automate shell any more."""
    with AutomateTest(argv=['automate', 'shell', '--no-colors']) as app:
        with pytest.raises(SystemExit):
            app.run()


def test_list_tasks(shell, capsys):
    """list shows a table of the tasks with function and number of hosts."""
    capsys.readouterr()
    assert shell.command('list')
    lines = _lines(capsys)
    assert lines[1] == 'tasks' and lines[2].split() == ['id', 'name', 'function', 'hosts']
    task2 = next(line for line in lines if line.startswith('task2'))
    hosts = str(len(shell.app.automate.tasks['task2']['connection']['hosts']))
    assert task2.split() == ['task2', 'Ping', 'our', 'hosts', 'tests.ext.automate.automate_tests_helper:task2', hosts]


def test_list_hosts_without_passwords(shell, capsys):
    """hosts list shows the hosts without passwords, sudo or identity."""
    capsys.readouterr()
    shell.command('hosts list')
    text = capsys.readouterr().out
    assert 'password1' not in text and 'sudo1' not in text and 'identity1' not in text
    host1 = next(line for line in text.splitlines() if line.strip().startswith('host1'))
    assert host1.split() == ['host1', 'host1', 'ip_address1', '22', 'user1', 'identity,', 'password,', 'sudo']


def test_list_hostgroups(shell, capsys):
    """hostgroups list shows each group with its hosts."""
    capsys.readouterr()
    shell.command('hostgroups list')
    group1 = next(line for line in _lines(capsys) if line.startswith('group1'))
    assert group1.split() == ['group1', 'host1,', 'host2']


def test_list_connections_defaults_first(shell, capsys):
    """connections list shows _defaults first, without passwords."""
    capsys.readouterr()
    shell.command('connections list')
    lines = _lines(capsys)
    assert lines[3].startswith('_defaults') and 'password_connect_base' not in '\n'.join(lines)
    assert any(line.startswith('con1') for line in lines[4:])


def test_show_as_json(shell, capsys):
    """show task2 prints the task as json."""
    capsys.readouterr()
    shell.command('show task2')
    text = capsys.readouterr().out
    start = text.index('{')
    task = json.loads(text[start:])
    assert (task['id'], task['name']) == ('task2', 'Ping our hosts')


def test_show_unknown_task(shell, monkeypatch):
    """show of an unknown task logs "Task nope not found!"."""
    from cement.ext.ext_logging import LoggingLogHandler

    errors = []
    monkeypatch.setattr(LoggingLogHandler, 'error', lambda self, msg, **kw: errors.append(msg))
    shell.command('show nope')
    assert errors == ['Task nope not found!']


def test_shell_shows_a_failing_command(shell, monkeypatch):
    """A failing command is logged as "Shell command failed: ..."."""
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
    """
    The probe runs the automate shell; list shows the tasks, show nope
    logs its error above the prompt and exit ends with 0.
    """
    probe = 'tests.ext.automate.automate_tests_shell_probe'
    # each input waits for the output of the one before and the prompt below it
    steps = [(None, b'list\r'), ('function', b'show nope\r'), ('Task nope not found!', b'exit\r')]
    with Terminal([sys.executable, '-m', probe, 'automate', 'shell'], rows=24, columns=120) as terminal:
        for wait, keys in steps:
            shown = terminal.wait_for(lambda lines: lines[-1] == 'Automate>' and (wait is None or any(wait in line for line in lines)))
            assert shown is not None, '\n'.join(terminal.screen.display)
            terminal.send(keys)
        code = terminal.wait()
    # the table of the tasks, then the error of show above the prompt
    assert any(line.split()[:4] == ['id', 'name', 'function', 'hosts'] for line in shown)
    assert any(line.endswith('Task nope not found!') and 'Automate>' not in line for line in shown)
    assert shown[-1] == 'Automate>' and code == 0
