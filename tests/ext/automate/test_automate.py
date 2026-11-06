"""
Tests for tokeo.ext.automate: the hosts, hostgroups, connections and tasks
read from the config and the results of a task run.
"""

import io
import copy
from unittest import mock

import invoke
import pytest
from cement.utils.misc import init_defaults
from tokeo.core.utils.props import hasprops, anyprop, getprop
from tokeo.ext.automate import TokeoAutomateError, TokeoAutomateResult, TokeoInvokeLocalContext, TokeoInvokeRemoteContext
from tests.ext.automate.automate_tests_helper import (
    AutomateTest,
    DEFAULTS,
    make_automate_hosts,
    make_automate_hostgroups,
    make_automate_connections,
    make_automate_tasks,
)


def test_hosts_are_completed_by_id_and_name():
    """The hosts are read and completed by their id and name."""
    with AutomateTest() as app:
        assert app.automate.hosts == make_automate_hosts(validate=True)


def test_hostgroups_are_expanded_to_their_hosts():
    """The hostgroups are read and expanded to their hosts."""
    with AutomateTest() as app:
        assert app.automate.hostgroups == make_automate_hostgroups(validate=True)


def test_local_as_id_of_a_host_or_hostgroup_raises():
    """ "local" as id of a host or a hostgroup raises TokeoAutomateError."""
    test_defaults = copy.deepcopy(DEFAULTS)
    test_defaults['automate']['hosts']['local'] = dict(host='local')
    test_defaults['automate']['hostgroups']['local'] = ('a', 'b')
    with AutomateTest(config_defaults=test_defaults) as app:
        with pytest.raises(TokeoAutomateError):
            app.automate.hosts
        with pytest.raises(TokeoAutomateError):
            app.automate.hostgroups


def test_connections_are_completed_by_their_defaults():
    """The connections are read and completed by their defaults."""
    with AutomateTest() as app:
        assert app.automate.connections == make_automate_connections(validate=True)


def test_tasks_are_read_and_expanded():
    """
    The tasks are read and expanded; func and module are left out,
    test_run_calls_a_task_with_its_runner runs one of the functions.
    """
    with AutomateTest() as app:
        tasks = app.automate.tasks
        for name in ('task1', 'task2', 'task3'):
            tasks[name].pop('func')
            tasks[name].pop('module')
        assert tasks == make_automate_tasks(validate=True)


def test_run_calls_a_task_with_its_runner(monkeypatch):
    """run calls the real task1 with the runner of the local host, exit 0."""
    test_defaults = init_defaults('automate')
    test_defaults['automate']['tasks'] = dict(task1=dict(module='tests.ext.automate.automate_tests_helper'))
    with AutomateTest(config_defaults=test_defaults) as app:
        with monkeypatch.context() as patch:
            # invoke hands stdin on, pytest blocks reading it
            patch.setattr('sys.stdin', io.StringIO(''))
            results = app.automate.run(app.automate.tasks['task1'])
        assert [(r.host_id, r.exited, r.exception) for r in results] == [('local', 0, None)]
        assert results[0].stdout.strip() == 'task1'
        # the result can be shown by inspect
        app.inspect(results[0], divider='*')


def test_run_reports_a_missing_task_function():
    """A task without a function of its name ends in a TokeoAutomateError result."""
    test_defaults = init_defaults('automate')
    test_defaults['automate']['tasks'] = dict(no_such_task=dict(module='tests.ext.automate.automate_tests_helper'))
    with AutomateTest(config_defaults=test_defaults) as app:
        results = app.automate.run(app.automate.tasks['no_such_task'])
    assert [(r.exited, r.exception) for r in results] == [(-1, 'TokeoAutomateError')]
    assert 'A function named "no_such_task" does not exist' in results[0].stderr


def _get_connect_timeout(connect_timeout):
    # the timeout a task hands to the ssh client of a remote host, no server
    # is contacted to build the connection
    test_defaults = init_defaults('automate')
    test_defaults['automate']['tasks'] = dict(
        task1=dict(module='tests.ext.automate.automate_tests_helper', connection=dict(hosts='10.0.0.1', connect_timeout=connect_timeout)),
    )
    with AutomateTest(config_defaults=test_defaults) as app:
        (run,) = app.automate._build_run_connections(app.automate.tasks['task1'])
        return run['context'].connect_kwargs.get('timeout')


@pytest.mark.parametrize('value, expected', [(30, 30), (2.5, 2.5), ('5.5', 5.5), (None, None)])
def test_connect_timeout_takes_numbers_and_numeric_strings(value, expected):
    """connect_timeout takes seconds as number or as text, null sets none."""
    assert _get_connect_timeout(value) == expected


@pytest.mark.parametrize('value', [True, 0, -1, 'x'])
def test_connect_timeout_that_is_no_number_greater_than_0_raises(value):
    """A connect_timeout that is no number of seconds greater than 0 raises."""
    with pytest.raises(TokeoAutomateError, match='connect_timeout must be a number of seconds greater than 0'):
        _get_connect_timeout(value)


def test_task_module_that_is_no_string_raises():
    """A module of a task that is no string raises instead of a TypeError."""
    test_defaults = init_defaults('automate')
    test_defaults['automate']['tasks'] = dict(task1=dict(module=42))
    with AutomateTest(config_defaults=test_defaults) as app:
        with pytest.raises(TokeoAutomateError, match='module of the task "task1"'):
            app.automate.tasks


def test_tasks_local_default_resolves_without_local_host():
    """Without a host "local" a task runs on the bare local host."""
    test_defaults = init_defaults('automate')
    test_defaults['automate']['tasks'] = dict(
        _defaults=dict(module='tests.ext.automate.automate_tests_helper'),
        task1=dict(),
    )
    with AutomateTest(config_defaults=test_defaults) as app:
        assert app.automate.tasks['task1']['connection']['hosts'] == (dict(id='local', name='local', host='local'),)


def test_tasks_local_default_resolves_with_local_host():
    """A configured host "local" is taken for a task by default."""
    test_defaults = init_defaults('automate')
    test_defaults['automate']['hosts'] = dict(local=dict(shell='/bin/sh'))
    test_defaults['automate']['tasks'] = dict(
        _defaults=dict(module='tests.ext.automate.automate_tests_helper'),
        task1=dict(),
    )
    with AutomateTest(config_defaults=test_defaults) as app:
        assert app.automate.tasks['task1']['connection']['hosts'] == (dict(id='local', name='local', host='local', shell='/bin/sh'),)


def test_result_is_read_by_attributes_not_as_dict():
    """A result is read by its fields like attributes, it is no dict."""
    with AutomateTest() as app:
        res = TokeoAutomateResult(
            'task1', 'con1', 'host1', result=dict(stdout='Out', stderr='Err', command='Cmd', exited=1, values=dict(a=1, b='2nd'))
        )
        assert getprop(res, 'unknown', fallback='Fallback') == 'Fallback'
        assert getprop(res, 'stdout', fallback='Fallback') == 'Out'
        assert getattr(res, 'stderr') == 'Err'
        assert hasprops(res, ('stdout', 'stderr')) is True
        assert anyprop(res, ('miss', 'stderr')) is True
        with pytest.raises(TypeError):
            app.inspect(res['stderr'])
        with pytest.raises(AttributeError):
            app.inspect(res.get('stderr'))


# config, connections and environment of a run


def test_tasks_build_leaves_the_config_as_it_is():
    """Building the tasks reads the config without changing it, also _defaults."""
    with AutomateTest() as app:
        before = copy.deepcopy(app.config.get('automate', 'tasks'))
        assert app.automate.tasks
        assert app.config.get('automate', 'tasks') == before


def test_run_closes_the_connections_of_every_host(monkeypatch):
    """A run closes each connection it opened, also of a host whose task failed."""
    closed = []
    monkeypatch.setattr(TokeoInvokeRemoteContext, 'close', lambda self: closed.append(self.host))
    monkeypatch.setattr(TokeoInvokeLocalContext, 'close', lambda self: closed.append('local'))

    def func(run, verbose=False, **kwargs):
        if getattr(run.__self__, 'host', None) == '10.0.0.2':
            raise RuntimeError('host failed')
        return dict(stdout='', stderr='', command='', exited=0)

    test_defaults = init_defaults('automate')
    test_defaults['automate']['tasks'] = dict(
        task1=dict(module='tests.ext.automate.automate_tests_helper', connection=dict(hosts=['local', '10.0.0.1', '10.0.0.2'])),
    )
    with AutomateTest(config_defaults=test_defaults) as app:
        task = app.automate.tasks['task1']
        task['func'] = func
        results = app.automate.run(task)
    assert sorted(closed) == ['10.0.0.1', '10.0.0.2', 'local']
    assert [result.exited for result in results] == [0, 0, -1]


def _make_task_ref(**kwprotected):
    # a task reference for a context, strict and sanitized unless given
    return dict(kwargs={}, kwprotected=dict(dict(protect='strict', sanitize=True, env={}), **kwprotected))


def test_local_run_gets_the_env_values_as_they_are(monkeypatch):
    """A local run gets the env values unquoted, a bad name is dropped."""
    env = {'GREETING': 'hello world', 'BAD-NAME': 'x'}
    context = TokeoInvokeLocalContext(config=invoke.Config(overrides={}), task_ref=_make_task_ref(env=env))
    monkeypatch.setattr('sys.stdin', io.StringIO(''))
    result = context.run('printenv GREETING; env | grep -c "^BAD-NAME=" ; true', hide=True)
    assert result.stdout == 'hello world\n0\n'


def _make_remote(exited, **kwprotected):
    # a remote context on a fake ssh client whose command ends with exited
    context = TokeoInvokeRemoteContext(
        host='h', config=invoke.Config(overrides={}), connect_kwargs={}, task_ref=_make_task_ref(**kwprotected)
    )
    context.client = mock.MagicMock()
    channel = context.client.get_transport.return_value.open_session.return_value
    channel.exit_status_ready.return_value = True
    channel.recv_ready.return_value = channel.recv_stderr_ready.return_value = False
    channel.recv_exit_status.return_value = exited
    return context, channel


def test_remote_run_exports_the_env_values_quoted():
    """The export line of a remote shell has the values quoted, no bad names."""
    context, channel = _make_remote(0, env={'GREETING': 'hello world', 'BAD-NAME': 'x'})
    context.run('echo "$GREETING"', hide=True)
    channel.exec_command.assert_called_once_with('export GREETING=\'hello world\' ; echo "$GREETING"')


def test_remote_run_failing_raises_unless_warn():
    """A failing remote command raises UnexpectedExit, with warn it returns."""
    context, _ = _make_remote(1)
    with pytest.raises(invoke.exceptions.UnexpectedExit):
        context.run('false', hide=True)
    assert context.run('false', hide=True, warn=True).exited == 1
