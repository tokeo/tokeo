"""
Shared parts of the tests of tokeo.ext.automate: the tasks, the default
hosts, hostgroups, connections and tasks, and the test app.
"""

from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest


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
        # the extension normalizes a scalar sudo to its full dict form
        d['host1']['sudo'] = dict(
            password='sudo1',
            mode='eq',
            match='password:',
            tail=100,
        )
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
        # the extension reads base values from the _defaults key of the section
        d = dict(_defaults=b, **c)
    else:
        # add _defaults
        b['id'] = '_defaults'
        b['name'] = '_defaults'
        # meta-only fields the user section does not set
        b['hosts'] = None
        b['connections'] = {}
        # the extension normalizes a scalar sudo to its full dict form
        b['sudo'] = dict(
            password='sudo_connect_base',
            mode='eq',
            match='password:',
            tail=100,
        )
        c['_defaults'] = b
        # add same fullfiller
        c['con1']['id'] = 'con1'
        c['con2']['id'] = 'con2'
        c['con1']['sudo'] = dict(
            password='sudo_con1',
            mode='eq',
            match='password:',
            tail=100,
        )
        # the connections property returns the composed mapping itself
        d = c
    # return the dict
    return d


def defaults_automate_tasks(validate=False):
    if not validate:
        # define config defaults
        d = dict(
            _defaults=dict(
                module='tests.ext.automate.automate_tests_helper',
            ),
            task1=dict(
                module='tests.ext.automate.automate_tests_helper',
            ),
            task2=dict(
                name='Ping our hosts',
                module='tests.ext.automate.automate_tests_helper',
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
                kwprotected=dict(
                    env={},
                    protect='strict',
                    pty=False,
                    sanitize=True,
                ),
                kwargs={},
                connection=dict(
                    id='_defaults',
                    name='_defaults',
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
                    sudo=dict(
                        password='sudo_connect_base',
                        mode='eq',
                        match='password:',
                        tail=100,
                    ),
                    identity=None,
                    connect_timeout=30,
                    lookup_keys=None,
                    allow_agent=False,
                    forward_agent=False,
                    known_hosts=None,
                ),
            ),
            task2=dict(
                id='task2',
                name='Ping our hosts',
                kwprotected=dict(
                    env={},
                    protect='strict',
                    pty=False,
                    sanitize=True,
                ),
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
                                sudo=dict(
                                    password='sudo1',
                                    mode='eq',
                                    match='password:',
                                    tail=100,
                                ),
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
                    sudo=dict(
                        password='sudo_task1',
                        mode='eq',
                        match='password:',
                        tail=100,
                    ),
                    identity='identity_task1',
                    connect_timeout=30,
                    lookup_keys=None,
                    allow_agent=False,
                    forward_agent=False,
                    known_hosts='known_hosts_task1',
                ),
            ),
            task3=dict(
                id='task3',
                name='task3',
                kwprotected=dict(
                    env={},
                    protect='strict',
                    pty=False,
                    sanitize=True,
                ),
                kwargs={},
                connection=dict(
                    id='_defaults',
                    name='_defaults',
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
                    sudo=dict(
                        password='sudo_connect_base',
                        mode='eq',
                        match='password:',
                        tail=100,
                    ),
                    identity=None,
                    connect_timeout=30,
                    lookup_keys=None,
                    allow_agent=False,
                    forward_agent=False,
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


class AutomateTest(TokeoTest):
    """A test app with automate, by default with the config of defaults."""

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.tui.input',
            'tokeo.ext.tui.output',
            'tokeo.ext.automate',
        ]

    def __init__(self, **kw):
        kw.setdefault('config_defaults', defaults)
        super().__init__(**kw)
