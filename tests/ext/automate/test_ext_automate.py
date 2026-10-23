"""
Tests for tokeo.ext.automate: the hosts, hostgroups, connections and tasks
read from the config and the results of a task run.
"""

import copy

import invoke
import pytest
from cement.utils.misc import init_defaults
from tokeo.core.utils.base import hasprops, anyprop, getprop
from tokeo.ext.automate import TokeoAutomateError, TokeoAutomateResult
from tests.utils import use_disabled_stdin_capture
from tests.ext.automate.automate_tests_helper import (
    AutomateTest,
    defaults,
    defaults_automate_hosts,
    defaults_automate_hostgroups,
    defaults_automate_connections,
    defaults_automate_tasks,
)


def test_hosts_simple():
    # the hosts are read and completed by their id and name
    with AutomateTest() as app:
        assert app.automate.hosts == defaults_automate_hosts(validate=True)


def test_hostgroups_simple():
    # the hostgroups are read and expanded to their hosts
    with AutomateTest() as app:
        assert app.automate.hostgroups == defaults_automate_hostgroups(validate=True)


def test_reserved_local_key():
    # "local" is reserved, neither a host nor a hostgroup may use it as id
    test_defaults = copy.deepcopy(defaults)
    test_defaults['automate']['hosts']['local'] = dict(host='local')
    test_defaults['automate']['hostgroups']['local'] = ('a', 'b')
    with AutomateTest(config_defaults=test_defaults) as app:
        with pytest.raises(TokeoAutomateError):
            app.automate.hosts
        with pytest.raises(TokeoAutomateError):
            app.automate.hostgroups


def test_connections_simple():
    # the connections are read and completed by their defaults
    with AutomateTest() as app:
        assert app.automate.connections == defaults_automate_connections(validate=True)


def test_tasks_simple():
    # the tasks are read and expanded; func and module are left out, the
    # imported functions cannot be compared and are run by the next test
    with AutomateTest() as app:
        tasks = app.automate.tasks
        for name in ('task1', 'task2', 'task3'):
            tasks[name].pop('func')
            tasks[name].pop('module')
        assert tasks == defaults_automate_tasks(validate=True)


def test_tasks_task1_runs_locally():
    # task1 runs uname by invoke on the local host and returns its result
    with AutomateTest() as app:
        task = app.automate.tasks['task1']
        connection = invoke.Context(config=invoke.Config(overrides={}))
        with use_disabled_stdin_capture():
            ran = task['func'](app, connection, **task['kwargs'])
        result = TokeoAutomateResult('task1', task['connection']['id'], 'local', result=ran)
        assert result.exited == 0
        assert 'darwin' in result.stdout.lower() or 'linux' in result.stdout.lower()
        # the result can be shown by inspect
        app.inspect(result, divider='*')


def test_tasks_local_default_resolves_without_local_host():
    # without a hosts entry "local" the default of a task is the bare local host
    test_defaults = init_defaults('automate')
    test_defaults['automate']['tasks'] = dict(
        _defaults=dict(module='tests.ext.automate.automate_tests_helper'),
        task1=dict(),
    )
    with AutomateTest(config_defaults=test_defaults) as app:
        assert app.automate.tasks['task1']['connection']['hosts'] == (dict(id='local', name='local', host='local'),)


def test_tasks_local_default_resolves_with_local_host():
    # a configured hosts entry "local" is taken for the default of a task
    test_defaults = init_defaults('automate')
    test_defaults['automate']['hosts'] = dict(local=dict(shell='/bin/sh'))
    test_defaults['automate']['tasks'] = dict(
        _defaults=dict(module='tests.ext.automate.automate_tests_helper'),
        task1=dict(),
    )
    with AutomateTest(config_defaults=test_defaults) as app:
        assert app.automate.tasks['task1']['connection']['hosts'] == (dict(id='local', name='local', host='local', shell='/bin/sh'),)


def test_automate_result():
    # the fields of a result are read like attributes, it is no dict
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
