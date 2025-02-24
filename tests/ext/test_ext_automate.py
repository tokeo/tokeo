import pytest
import copy
from tests.utils import use_disabled_stdin_capture
from tokeo.main import TestApp
from cement.utils.misc import init_defaults
from tokeo.ext.automate import TokeoAutomateError, TokeoAutomateResult
from tokeo.core.utils.base import hasprops, anyprop, getprop
import invoke


def task1(app, connection, url=''):
    app.log.info('Task1 started')
    result = connection.run('uname -a', hide=True, warn=False)
    return result


def task2(app, connection, url=''):
    app.log.info('Task2 start with url: ' + url)


def task3(app, connection, url=''):
    app.log.info('Task3 start with url: ' + url)


def defaults_automate_hosts(validate=False):
    # define config defaults
    d = dict(
        host1=dict(
            host='ip_address1',
            port=22,
            user='user1',
            password='password1',
            sudo='sudo1',
            identity='identity1',
            host_key='host_key1',
        ),
        host2=dict(
            name='Server host2',
            port=22,
            host='ip_address2',
            user='admin2',
            password='password2',
        ),
        host3=dict(
            host='ip_address3',
        ),
    )
    # fullfill the dict in case for validation purpose
    if validate:
        d['host1']['id'] = 'host1'
        d['host1']['name'] = 'host1'
        d['host2']['id'] = 'host2'
        d['host3']['id'] = 'host3'
        d['host3']['name'] = 'host3'
    # return the dict
    return d


def defaults_automate_hostgroups(validate=False):
    if not validate:
        # define config defaults
        d = dict(
            group1=tuple(
                (
                    'host1',
                    'host2',
                )
            ),
            group2=tuple(('host3',)),
            group3=tuple(
                (
                    'group1',
                    'host3',
                )
            ),
            group4=tuple(
                (
                    'group1',
                    'group2',
                )
            ),
            group5=tuple(
                (
                    '192.168.0.1',
                    '192.168.0.2',
                )
            ),
        )
    else:
        # fullfill the dict in case for validation purpose
        hosts = defaults_automate_hosts(validate=True)
        d = dict(
            group1=tuple(
                (
                    hosts['host1'],
                    hosts['host2'],
                )
            ),
            group2=tuple((hosts['host3'],)),
            group3=tuple(
                (
                    hosts['host1'],
                    hosts['host2'],
                    hosts['host3'],
                )
            ),
            group4=tuple(
                (
                    hosts['host1'],
                    hosts['host2'],
                    hosts['host3'],
                )
            ),
            group5=tuple(
                (
                    dict(
                        id='192.168.0.1',
                        name='192.168.0.1',
                        host='192.168.0.1',
                    ),
                    dict(
                        id='192.168.0.2',
                        name='192.168.0.2',
                        host='192.168.0.2',
                    ),
                )
            ),
        )
    # return the dict
    return d


def defaults_automate_connections(validate=False):
    # define base
    b = dict(
        port=22,
        user='user_connect_base',
        password='password_connect_base',
        sudo='sudo_connect_base',
        identity=None,
        connect_timeout=30,
        lookup_keys=None,
        allow_agent=False,
        forward_agent=False,
        forward_local=None,
        forward_remote=None,
        known_hosts=None,
    )
    # define connections
    c = dict(
        # additional defined connections
        con1=dict(
            name='A sample connection',
            hosts=tuple(
                (
                    'local',
                    'host1',
                    'host2',
                    'host3',
                    'group3',
                    '192.168.101.1',
                )
            ),
            user='user_con1',
            password='password_con1',
            sudo='sudo_con1',
            identity='identity',
            known_hosts='known_hosts_con1',
            connect_timeout=30,
            forward_agent=False,
            forward_local=None,
            forward_remote=None,
        ),
        con2=dict(
            name='A short connection',
            hosts='192.168.101.1',
            user='user_con2',
            identity='identity',
        ),
    )
    # define config defaults
    if not validate:
        d = dict(
            **b,
            connections=c,
        )
    else:
        # add same fullfiller
        b['id'] = '_default'
        b['name'] = '_default'
        c['con1']['id'] = 'con1'
        c['con2']['id'] = 'con2'
        # return as expanded dict
        d = dict(
            _default=b,
            connections=c,
        )
    # return the dict
    return d


def defaults_automate_tasks(validate=False):
    if not validate:
        # define config defaults
        d = dict(
            module='tests.ext.test_ext_automate',
            task1=dict(
                module='tests.ext.test_ext_automate',
            ),
            task2=dict(
                name='Ping our hosts',
                module='tests.ext.test_ext_automate',
                timeout=None,
                kwargs=dict(
                    url='https://github.com',
                ),
                connection=dict(
                    use='con1',
                    user='user_task1',
                    password='password_task1',
                    sudo='sudo_task1',
                    identity='identity_task1',
                    connect_timeout=30,
                    forward_agent=False,
                    forward_local=None,
                    forward_remote=None,
                    known_hosts='known_hosts_task1',
                ),
            ),
            task3=dict(
                hosts=['192.168.101.1'],
            ),
        )
    else:
        # define expanded validate dict
        d = dict(
            task1=dict(
                id='task1',
                name='task1',
                timeout=None,
                kwargs={},
                connection=dict(
                    id='_default',
                    name='_default',
                    hosts=tuple(
                        (
                            dict(
                                id='local',
                                name='local',
                                host='local',
                            ),
                        )
                    ),
                    port=22,
                    user='user_connect_base',
                    password='password_connect_base',
                    sudo='sudo_connect_base',
                    identity=None,
                    connect_timeout=30,
                    lookup_keys=None,
                    allow_agent=False,
                    forward_agent=False,
                    forward_local=None,
                    forward_remote=None,
                    known_hosts=None,
                ),
            ),
            task2=dict(
                id='task2',
                name='Ping our hosts',
                timeout=None,
                kwargs=dict(
                    url='https://github.com',
                ),
                connection=dict(
                    id='con1',
                    name='A sample connection',
                    hosts=tuple(
                        (
                            dict(
                                id='local',
                                name='local',
                                host='local',
                            ),
                            dict(
                                id='host1',
                                name='host1',
                                host='ip_address1',
                                port=22,
                                user='user1',
                                password='password1',
                                sudo='sudo1',
                                identity='identity1',
                                host_key='host_key1',
                            ),
                            dict(
                                id='host2',
                                name='Server host2',
                                host='ip_address2',
                                port=22,
                                user='admin2',
                                password='password2',
                            ),
                            dict(
                                id='host3',
                                name='host3',
                                host='ip_address3',
                            ),
                            dict(
                                id='192.168.101.1',
                                name='192.168.101.1',
                                host='192.168.101.1',
                            ),
                        )
                    ),
                    port=22,
                    user='user_task1',
                    password='password_task1',
                    sudo='sudo_task1',
                    identity='identity_task1',
                    connect_timeout=30,
                    lookup_keys=None,
                    allow_agent=False,
                    forward_agent=False,
                    forward_local=None,
                    forward_remote=None,
                    known_hosts='known_hosts_task1',
                ),
            ),
            task3=dict(
                id='task3',
                name='task3',
                timeout=None,
                kwargs={},
                connection=dict(
                    id='_default',
                    name='_default',
                    hosts=tuple(
                        (
                            dict(
                                id='192.168.101.1',
                                name='192.168.101.1',
                                host='192.168.101.1',
                            ),
                        )
                    ),
                    port=22,
                    user='user_connect_base',
                    password='password_connect_base',
                    sudo='sudo_connect_base',
                    identity=None,
                    connect_timeout=30,
                    lookup_keys=None,
                    allow_agent=False,
                    forward_agent=False,
                    forward_local=None,
                    forward_remote=None,
                    known_hosts=None,
                ),
            ),
        )
    # return the dict
    return d


# setup a default config
defaults = init_defaults('automate')
defaults['automate']['hosts'] = defaults_automate_hosts()
defaults['automate']['hostgroups'] = defaults_automate_hostgroups()
defaults['automate']['connections'] = defaults_automate_connections()
defaults['automate']['tasks'] = defaults_automate_tasks()


class AutomateTestApp(TestApp):

    class Meta:
        extensions = ['tokeo.ext.print', 'tokeo.ext.automate']


def test_hosts_simple(rando):
    'Test for reading a list of hosts'
    with AutomateTestApp(config_defaults=defaults) as app:
        app.run()

        hosts = app.automate.hosts
        assert hosts == defaults_automate_hosts(validate=True)


def test_hostgroups_simple(rando):
    """
    Test for reading and expanding hostgroups
    """
    with AutomateTestApp(config_defaults=defaults) as app:
        app.run()

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

    with AutomateTestApp(config_defaults=test_defaults) as app:
        app.run()

        with pytest.raises(TokeoAutomateError):
            _ = app.automate.hosts
        with pytest.raises(TokeoAutomateError):
            _ = app.automate.hostgroups


def test_connections_simple(rando):
    """
    Test for reading connections
    """
    with AutomateTestApp(config_defaults=defaults) as app:
        app.run()

        connections = app.automate.connections
        assert connections == defaults_automate_connections(validate=True)


def test_tasks_simple(rando):
    """
    Test for reading and expanding the tasks
    """
    test_defaults = copy.deepcopy(defaults)

    with AutomateTestApp(config_defaults=test_defaults) as app:
        app.run()

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

    with AutomateTestApp(config_defaults=test_defaults) as app:
        app.run()

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


def test_automate_result(rando):

    with AutomateTestApp(config_defaults=defaults) as app:
        app.run()

        res = TokeoAutomateResult(
                  'task1',
                  'con1',
                  'host1',
                  result=dict(stdout='Out', stderr='Err', command='Cmd', exited=1, values=dict(a=1, b='2nd'))
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
