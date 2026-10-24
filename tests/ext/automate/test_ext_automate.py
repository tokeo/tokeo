"""
Tests for tokeo.ext.automate: the hosts, hostgroups, connections and tasks
read from the config and the results of a task run.
"""

import io
import copy

import invoke
import pytest
from cement.utils.misc import init_defaults
from tokeo.core.utils.base import hasprops, anyprop, getprop
from tokeo.ext.automate import TokeoAutomateError, TokeoAutomateResult
from tests.ext.automate.automate_tests_helper import (
    AutomateTest,
    defaults,
    defaults_automate_hosts,
    defaults_automate_hostgroups,
    defaults_automate_connections,
    defaults_automate_tasks,
)


def test_hosts_are_completed_by_id_and_name():
    """The hosts are read and completed by their id and name."""
    with AutomateTest() as app:
        assert app.automate.hosts == defaults_automate_hosts(validate=True)


def test_hostgroups_are_expanded_to_their_hosts():
    """The hostgroups are read and expanded to their hosts."""
    with AutomateTest() as app:
        assert app.automate.hostgroups == defaults_automate_hostgroups(validate=True)


def test_local_as_id_of_a_host_or_hostgroup_raises():
    """ "local" as id of a host or a hostgroup raises TokeoAutomateError."""
    test_defaults = copy.deepcopy(defaults)
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
        assert app.automate.connections == defaults_automate_connections(validate=True)


def test_tasks_are_read_and_expanded():
    """
    The tasks are read and expanded; func and module are left out,
    test_tasks_task1_runs_locally runs one of the functions.
    """
    with AutomateTest() as app:
        tasks = app.automate.tasks
        for name in ('task1', 'task2', 'task3'):
            tasks[name].pop('func')
            tasks[name].pop('module')
        assert tasks == defaults_automate_tasks(validate=True)


def test_tasks_task1_runs_locally(monkeypatch):
    """task1 runs uname by invoke on the local host and exits with 0."""
    with AutomateTest() as app:
        task = app.automate.tasks['task1']
        connection = invoke.Context(config=invoke.Config(overrides={}))
        with monkeypatch.context() as patch:
            # invoke hands stdin on, pytest blocks reading it
            patch.setattr('sys.stdin', io.StringIO(''))
            ran = task['func'](app, connection, **task['kwargs'])
        result = TokeoAutomateResult('task1', task['connection']['id'], 'local', result=ran)
        assert result.exited == 0
        assert 'darwin' in result.stdout.lower() or 'linux' in result.stdout.lower()
        # the result can be shown by inspect
        app.inspect(result, divider='*')


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
