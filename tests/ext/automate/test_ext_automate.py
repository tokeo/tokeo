import pytest
import invoke
import copy
from cement.utils.misc import init_defaults
from tests.utils import use_disabled_stdin_capture
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


def test_hosts_simple(rando):
    'Test for reading a list of hosts'
    with AutomateTest() as app:

        hosts = app.automate.hosts
        assert hosts == defaults_automate_hosts(validate=True)


def test_hostgroups_simple(rando):
    """
    Test for reading and expanding hostgroups
    """
    with AutomateTest() as app:

        hostgroups = app.automate.hostgroups
        assert hostgroups == defaults_automate_hostgroups(validate=True)


def test_reserved_local_key(rando):
    """
    Test to prevent use of the word "local" as key/id for a host or a hostgroup
    """
    test_defaults = copy.deepcopy(defaults)
    test_defaults['automate']['hosts']['local'] = dict(host='local')
    test_defaults['automate']['hostgroups']['local'] = tuple(
        (
            'a',
            'b',
        )
    )

    with AutomateTest(config_defaults=test_defaults) as app:

        with pytest.raises(TokeoAutomateError):
            _ = app.automate.hosts
        with pytest.raises(TokeoAutomateError):
            _ = app.automate.hostgroups


def test_connections_simple(rando):
    """
    Test for reading connections
    """
    with AutomateTest() as app:

        connections = app.automate.connections
        assert connections == defaults_automate_connections(validate=True)


def test_tasks_simple(rando):
    """
    Test for reading and expanding the tasks
    """
    test_defaults = copy.deepcopy(defaults)

    with AutomateTest(config_defaults=test_defaults) as app:

        tasks = app.automate.tasks
        # Drop funcs and modules while they are already tested.
        # Otherwise there would be an exception for module or func not found
        tasks['task1'].pop('func')
        tasks['task1'].pop('module')
        tasks['task2'].pop('func')
        tasks['task2'].pop('module')
        tasks['task3'].pop('func')
        tasks['task3'].pop('module')
        assert tasks == defaults_automate_tasks(validate=True)


def test_tasks_task1(rando):
    """
    Test for reading and expanding the tasks
    """
    test_defaults = copy.deepcopy(defaults)

    with AutomateTest(config_defaults=test_defaults) as app:

        tasks = app.automate.tasks
        context = invoke.Context(config=invoke.Config(overrides={}))
        connection = context
        func = tasks['task1']['func']
        kwargs = tasks['task1']['kwargs']
        with use_disabled_stdin_capture():
            result = TokeoAutomateResult('task1', tasks['task1']['connection']['id'], 'local', result=func(app, connection, **kwargs))
        assert result.exited == 0
        assert 'darwin' in result.stdout.lower() or 'linux' in result.stdout.lower()

        app.inspect(result, divider='*')
        # app.inspect(result.ok, divider='*')
        # app.inspect(result.stdout, divider='*')


def test_tasks_local_default_resolves_without_local_host(rando):
    """
    Without a hosts "local" entry the task default resolves to the bare local host
    """
    test_defaults = init_defaults('automate')
    test_defaults['automate']['tasks'] = dict(
        _defaults=dict(module='tests.ext.automate.automate_tests_helper'),
        task1=dict(),
    )

    with AutomateTest(config_defaults=test_defaults) as app:

        hosts = app.automate.tasks['task1']['connection']['hosts']
        assert hosts == tuple((dict(id='local', name='local', host='local'),))


def test_tasks_local_default_resolves_with_local_host(rando):
    """
    With a configured hosts "local" entry the task default to local resolves
    """
    test_defaults = init_defaults('automate')
    test_defaults['automate']['hosts'] = dict(local=dict(shell='/bin/sh'))
    test_defaults['automate']['tasks'] = dict(
        _defaults=dict(module='tests.ext.automate.automate_tests_helper'),
        task1=dict(),
    )

    with AutomateTest(config_defaults=test_defaults) as app:

        hosts = app.automate.tasks['task1']['connection']['hosts']
        assert hosts == tuple((dict(id='local', name='local', host='local', shell='/bin/sh'),))


def test_automate_result(rando):

    with AutomateTest() as app:

        res = TokeoAutomateResult(
            'task1', 'con1', 'host1', result=dict(stdout='Out', stderr='Err', command='Cmd', exited=1, values=dict(a=1, b='2nd'))
        )
        assert 'Fallback' == getprop(res, 'unknown', fallback='Fallback')
        assert 'Out' == getprop(res, 'stdout', fallback='Fallback')
        assert 'Err' == getattr(res, 'stderr')
        assert hasprops(res, ('stdout', 'stderr')) is True
        assert anyprop(res, ('miss', 'stderr')) is True

        with pytest.raises(TypeError):
            app.inspect(res['stderr'])
        with pytest.raises(AttributeError):
            app.inspect(res.get('stderr'))
