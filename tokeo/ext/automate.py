"""
Task automation and remote execution for Tokeo applications.

This module provides an automation framework for running local and remote tasks,
managing hosts, and creating interactive shells for task execution. It supports
both sequential and parallel execution, SSH-based remote operations, and an
interactive command-line interface.

Example:
    ```python
    # Define a task in your configuration
    # tokeo.yml
    automate:
      tasks:
        check_uptime:
          module: "myapp.tasks"
          hosts: "webserver1"

      hosts:
        webserver1:
          host: "192.168.1.10"
          user: "admin"
          identity: "/path/to/ssh_key"

    # Execute via CLI
    # tokeo automate run check_uptime

    # define your method
    def check_uptime(app, connection, verbose=False):
        return connection.run('uptime', hide=not verbose, warn=False)
    ```
"""

from sys import argv
from os.path import basename
from datetime import datetime, timezone
import yaml
from threading import Thread
from concurrent import futures as concurrent_futures
import importlib
import invoke
import fabric
import paramiko
from paramiko.hostkeys import HostKeys, HostKeyEntry
from argparse import ArgumentParser
import shlex
from prompt_toolkit import prompt
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import NestedCompleter, WordCompleter
from cement.core.meta import MetaMixin
from cement import ex
from cement.core.foundation import SIGNALS
from cement.core.exc import CaughtSignal
from tokeo.core.exc import TokeoError
from tokeo.core.utils.base import hasprops, getprop, default_when_blank
from tokeo.core.utils.json import jsonDump, jsonTokeoEncoder
from tokeo.ext.argparse import Controller


def jsonTokeoAutomateEncoder(obj):
    """
    Internal custom JSON encoder for TokeoAutomateResult objects.

    Converts TokeoAutomateResult objects to dictionaries for JSON serialization.

    Args:
        obj: The object to encode.

    Returns:
        A serializable representation of the object.
    """
    # test for automate result
    if isinstance(obj, TokeoAutomateResult):
        return vars(obj)
    # continue with tokeo encoder
    return jsonTokeoEncoder(obj)


class TokeoAutomateError(TokeoError):
    """
    Exception class for automation-related errors.

    Used for errors specific to the automation system, such as
    configuration problems, connection issues, or task execution failures.
    """

    pass


class TokeoAutomateResult:
    """
    Represents the result of an automated task execution.

    Stores the output and status of a task execution, including stdout,
    stderr, exit code, and any additional values returned by the task.

    Attributes:
        task_id: Identifier of the executed task.
        connection_id: Identifier of the connection used.
        host_id: Identifier of the host where the task was executed.
        stdout: Standard output from the task execution.
        stderr: Standard error output from the task execution.
        command: The command that was executed.
        exited: Exit code of the command (0 for success).
        values: Additional values returned by the task.
    """

    def __init__(self, task_id, connection_id, host_id, result):
        """
        Initialize a task execution result.

        Args:
            task_id: Identifier of the executed task.
            connection_id: Identifier of the connection used.
            host_id: Identifier of the host where the task was executed.
            result: Raw result object from the execution.
        """
        self.task_id = task_id
        self.connection_id = connection_id
        self.host_id = host_id
        self.stdout = getprop(result, 'stdout', fallback=None)
        self.stderr = getprop(result, 'stderr', fallback=None)
        self.command = getprop(result, 'command', fallback=None)
        self.exited = getprop(result, 'exited', fallback=0)
        # in case that result looks like an invoke.runners.Result
        # try to get values from an additional values attribute
        # otherwise save the result as values for latter processing
        if hasprops(result, ('stdout', 'stderr', 'command', 'exited')):
            self.values = getprop(result, 'values', fallback=None)
        else:
            self.values = result

    def __repr__(self):
        """
        String representation of the result.

        Returns:
            A string representation of all result properties.
        """
        return vars(self).__repr__()


class TokeoAutomate(MetaMixin):
    """
    Main automation engine for executing tasks locally and remotely.

    Provides a framework for defining, configuring, and executing tasks across
    multiple hosts using SSH or local execution. Supports host definitions,
    host groups, connections, and task configuration.

    Attributes:
        app: The Cement application instance.
        _tasks: Cached dictionary of configured tasks.
        _modules: Cache of imported task modules.
        _hosts: Cached dictionary of configured hosts.
        _hostgroups: Cached dictionary of configured host groups.
        _connections: Cached dictionary of configured connections.
    """

    class Meta:
        """Extension meta-data and configuration defaults."""

        #: Unique identifier for this handler
        label = 'tokeo.automate'

        #: Id for config
        config_section = 'automate'

        #: Dict with initial settings
        config_defaults = dict(
            hosts={},
            passwords={},
            hostgroups={},
            connections=dict(
                id=None,
                name=None,
                connect_timeout=60,
                hosts=None,
                port=22,
                user=None,
                password=None,
                sudo=None,
                identity=None,
                lookup_keys=False,
                allow_agent=False,
                forward_agent=False,
                forward_local=None,
                forward_remote=None,
                known_hosts=None,
                connections={},
            ),
        )

    def __init__(self, app, *args, **kw):
        super(TokeoAutomate, self).__init__(*args, **kw)
        self.app = app
        # copy from list of tasks with expanded entries
        self._tasks = None
        # refernces for imported modules
        self._modules = {}
        # configured hosts list with settings per host
        self._hosts = None
        # counter of created host entries, also used as unique id
        self._hosts_cnt = 0
        # configured groups with hosts references
        self._hostgroups = None
        # configured named connections and access settings
        self._connections = None
        # counter of created connections, also used as unique id
        self._connections_cnt = 0

    def _setup(self, app):
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)

    def _config(self, key, **kwargs):
        """
        This is a simple wrapper, and is equivalent to:
            ``self.app.config.get(<section>, <key>)``.
        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def _get_host_dict(self, key, entry):
        """
        This will create a host dict for different defined hosts like:
        host, host:port, user@host, user:passwort@host:port
        """
        if not isinstance(entry, dict):
            raise TokeoAutomateError('To define a host entry there must be at least a dict')
        # use the counter also as running id
        self._hosts_cnt += 1
        # test key and make sure that has a value
        key = entry['id'] if 'id' in entry else key if key is not None else f'_host{self._hosts_cnt}'
        # check for reference
        entry = (self.hosts.get(entry['use'], {}) if 'use' in entry else {}) | entry
        # drop 'use' attribute if was defined
        entry.pop('use', None)
        # Create a dict with all given and allowed fields
        # any field set hereby will never be overwritten
        # by any merge. If some want to set the password
        # by connection e.g., the password field should
        # not exist here.
        if 'host' not in entry:
            raise TokeoAutomateError('To define a host entry there must be at least a "host" field')
        # setup the dict
        _host = dict(id=key, name=entry['name'] if 'name' in entry else key, host=entry['host'])
        for field in ('port', 'user', 'password', 'sudo', 'identity', 'host_key', 'shell'):
            if field in entry:
                _host[field] = entry[field]
        # return the record
        return _host

    def _get_host_dict_from_str(self, key, host_str):
        """
        This will get a string from schema
        user:password@host.domain:port
        and split into parts and create a host_dict
        """
        if not isinstance(host_str, str) or str.strip(host_str) == '':
            raise TokeoAutomateError('At least a host must be specified to get host_dict from string')
        # use the counter also as running id
        self._hosts_cnt += 1
        # get parts from string
        user_host_parts = str.strip(host_str).split('@') if '@' in host_str else ['', str.strip(host_str)]
        user_password_parts = user_host_parts[0].split(':') if ':' in user_host_parts[0] else [user_host_parts[0], '']
        host_port_parts = user_host_parts[1].split(':') if ':' in user_host_parts[1] else [user_host_parts[1], '']
        # build and return as dict
        if key is None or str(key) == '':
            key = host_port_parts[0]
        _host = dict(id=key, name=key, host=host_port_parts[0])
        if host_port_parts[1] and host_port_parts[1] != '':
            _host['port'] = host_port_parts[1]
        if user_password_parts[0] and user_password_parts[0] != '':
            _host['user'] = user_password_parts[0]
        if user_password_parts[1] and user_password_parts[1] != '':
            _host['password'] = user_password_parts[1]
        # return the record
        return _host

    def _overrule_host_dict(self, base, overrule):
        """
        This will get two dicts and overrule allowed
        keys from 2nd dict into 1st dict
        """
        # use the counter also as running id
        self._hosts_cnt += 1
        # make a copy from base dict
        _host = base.copy()
        # test for overrule content
        if overrule is None or not isinstance(overrule, dict):
            return _host
        # check for reference
        overrule = (self.hosts.get(overrule['use'], {}) if 'use' in overrule else {}) | overrule
        # drop 'use' attribute if was defined
        overrule.pop('use', None)
        # overrulable fields from host dict
        for field in ('id', 'name', 'host', 'port', 'user', 'password', 'sudo', 'identity', 'host_key', 'shell'):
            if field in overrule:
                _host[field] = overrule[field]
        # return the record
        return _host

    # make a defined dict from a config entry
    def _get_connection_dict(self, key, entry):
        """
        Create a dict with all given and allowed fields
        any field set hereby will never be overwritten
        by any merge. If some want to set the password
        by connection e.g., the password field should
        not exist here.
        """
        # use the counter also as running id
        self._connections_cnt += 1
        # test key and make sure that has a value
        key = key if key else entry['id'] if 'id' in entry else f'_conn{self._connections_cnt}'
        # create the base dict
        _connection = dict(
            id=key,
            name=entry['name'] if 'name' in entry and key != '_default' else key,
        )
        for field in (
            'hosts',
            'port',
            'user',
            'password',
            'sudo',
            'identity',
            'connect_timeout',
            'lookup_keys',
            'allow_agent',
            'forward_agent',
            'forward_local',
            'forward_remote',
            'known_hosts',
            'shell',
        ):
            if field in entry:
                _connection[field] = entry[field]
        # return the record
        return _connection

    def _setup_connection(self, connection):
        """
        Transform a connection configuration into a full, expanded connection.

        This method processes connection configurations from various sources and
        merges them according to well-defined precedence rules. It builds a complete
        connection configuration by:

        1. Connection Reference Resolution:
           - If 'use' parameter is specified, merges with the referenced connection
           - Applies connection-specific settings on top of the reference

        2. Default Settings Application:
           - Applies default connection settings from Meta.config_defaults
           - Put user-defined default connection settings into a ['_default'] dict
           - Ensures connection-specific settings override defaults

        3. Host Resolution Process:
           - Supports various host specification formats (string, dict, reference)
           - Resolves host references in multiple formats:
             * Direct host IDs from the hosts dictionary
             * Host group IDs from the hostgroups dictionary
             * Inline host definitions as dictionaries
             * Connection strings in "user:password@host:port" format
           - Properly merges host-specific overrides with referenced hosts

        4. Parameter Precedence Handling:
           - Host-specific parameters take highest precedence
           - Connection parameters (if 'use' is defined) come next
           - Task-specific parameters have lowest precedence

        5. Host List Normalization:
           - Ensures each host appears only once in the final list
           - Converts host configurations to a standardized format
           - Creates a tuple of fully-resolved host configurations

        Args:
            connection: A connection configuration dictionary which may be partial
                and reference other connections via 'use'.

        Returns:
            A fully resolved connection dictionary with all settings merged
            according to precedence rules and all hosts fully resolved.

        Raises:
            TokeoAutomateError: If host configurations are invalid.
        """
        # merge with use if defined
        connection = (self.connections['connections'][connection['use']] if 'use' in connection else {}) | connection
        # drop 'use' attribute if was defined
        connection.pop('use', None)
        # merge with default but drop sub 'connections' structs
        connection = self.Meta.config_defaults['connections'] | self.connections['_default'] | connection
        connection.pop('connections', None)
        # expand list of hosts to list of host_dicts
        hosts_list = []
        # check for list of hosts
        connection_hosts = connection['hosts']
        if isinstance(connection_hosts, str):
            connection_hosts = tuple((connection_hosts,))
        # loop the hosts
        for host in connection_hosts:
            # check types of entries to identify the config
            if isinstance(connection_hosts, dict):
                # host is key, maybe more fields
                entry = connection_hosts[host]
            elif isinstance(host, dict):
                # list of dicts
                if len(host) > 1:
                    raise TokeoAutomateError('A host dict must contains just 1 host dict structure')
                _host = next(iter(host))
                entry = host[_host]
                host = _host
            else:
                entry = None
            if isinstance(entry, str):
                entry = self._get_host_dict_from_str(None, entry)
            # check if entry needs fullfill while ref
            if entry and 'use' in entry:
                entry = self._get_host_dict(host, entry)

            if host in self.hosts:
                hosts_list.append(self._overrule_host_dict(self.hosts[host], entry))
            elif host in self.hostgroups:
                hosts_list.extend(self.hostgroups[host])
            else:
                if entry:
                    hosts_list.append(entry)
                else:
                    hosts_list.append(self._get_host_dict_from_str(None, host))
        # make dict list of hosts unique
        unique_hosts_list = {}
        for host in hosts_list:
            if not host['id'] in unique_hosts_list:
                unique_hosts_list[host['id']] = host
        # replace the fullfilled hosts
        connection['hosts'] = tuple(unique_hosts_list.values())
        # return with fullfilled connection
        return connection

    @property
    def hosts(self):
        """
        Dictionary of configured hosts.

        Retrieves and caches the configured host definitions from the
        application configuration. Each host entry includes connection details
        like hostname, port, username, and authentication information.

        Returns:
            Dictionary mapping host IDs to host configuration dictionaries.
        """
        # if set return
        if self._hosts is not None:
            return self._hosts
        # initialize hosts
        self._hosts = {}
        # read config sections
        _config_hosts = self._config('hosts', fallback={})
        # loop and fullfill
        for key in _config_hosts:
            # get params for host
            _config_host = _config_hosts[key]
            # key "local" is reserved as name
            if key == 'local':
                # local is only allowed when used to set sudo property
                if 'host' in _config_host:
                    raise TokeoAutomateError('The id "local" is reserved and not allowed as host')
                # create a host dict for local
                _config_local = dict(host=key)
                # only sudo and shell properties may set by config
                # all other props will be dropped
                if 'sudo' in _config_host:
                    _config_local['sudo'] = _config_host['sudo']
                if 'shell' in _config_host:
                    _config_local['shell'] = _config_host['shell']
                # set local for host dict
                _config_host = _config_local
            # build entry
            host = self._get_host_dict(key, _config_host)
            # add the filled entry
            self._hosts[key] = host
        # return property
        return self._hosts

    @property
    def hostgroups(self):
        """
        Dictionary of configured host groups.

        Retrieves and caches the configured host group definitions.
        Host groups allow executing tasks on multiple hosts at once.
        The resolution rules are:
        1. First lookup the ID in the hosts dictionary
        2. Second lookup the ID in already defined hostgroups
        3. As a fallback, create a new host entry from the string

        Returns:
            Dictionary mapping group names to lists of
            host configuration dictionaries.
        """
        # if set return
        if self._hostgroups is not None:
            return self._hostgroups
        # initialize hosts
        self._hostgroups = {}
        # read config sections
        _config_hostgroups = self._config('hostgroups', fallback={})
        # loop and fullfill
        for key in _config_hostgroups:
            # key "local" is reserved as name
            if key == 'local':
                raise TokeoAutomateError('The id "local" is reserved and not allowed as hostgroup')
            # get params for host
            _config_hostgroup = _config_hostgroups[key]
            # check split for host and user
            if not isinstance(_config_hostgroup, list) and not isinstance(_config_hostgroup, tuple):
                raise TokeoAutomateError('To create the hostgroup "{key}" there must be a list of hosts')
            # expand hosts to host_dicts
            hosts_list = []
            for host in _config_hostgroup:
                # check if found as host
                if host in self.hosts:
                    hosts_list.append(self.hosts[host].copy())
                elif host in self._hostgroups:
                    hosts_list.extend(self._hostgroups[host])
                else:
                    hosts_list.append(self._get_host_dict_from_str(None, host))
            # add the filled entry
            self._hostgroups[key] = tuple(hosts_list)
        # return property
        return self._hostgroups

    @property
    def connections(self):
        """
        Dictionary of configured connections for accessing hosts.

        Connections define how to access hosts, including authentication details,
        SSH configuration, timeout settings, and other connection parameters.
        The connection config includes a default connection and can be extended
        with additional named connections.

        Returns:
            Dictionary containing the default connection and a nested dictionary
            of named connections.
        """
        # if set return
        if self._connections is not None:
            return self._connections
        # initialize hosts
        self._connections = {}
        # read config sections
        _config_connections = self._config('connections', fallback={})
        # take the base fields as _default connection
        connection = self._get_connection_dict('_default', _config_connections)
        self._connections['_default'] = connection
        # add a dictionary for additional connections
        self._connections['connections'] = {}
        # take additional connections configuration or leave empty if none
        _config_connections = _config_connections.get('connections', {})
        # loop and add
        for key in _config_connections:
            # get params for host
            _config_connection = _config_connections[key]
            # build entry
            connection = self._get_connection_dict(key, _config_connection)
            # add the composed entry
            self._connections['connections'][key] = connection
        # return property
        return self._connections

    def _get_tasks(self):
        """
        Process and build the task configuration dictionary from settings.

        This method performs the complex process of analyzing task configurations
        from the config settings and building a fully-resolved task dictionary.
        It handles:

        1. Dynamic task module loading:
           - Imports Python modules containing task implementation functions
           - Supports a default module for all tasks via the 'module' setting
           - Caches imported modules for performance

        2. Task function resolution:
           - Looks up the function in the module matching the task name
           - Creates fallback error-raising functions for missing implementations
           - Proper exception handling for import and lookup errors

        3. Connection configuration with priority rules:
           - Explicit 'connection' setting takes precedence
           - Reference via 'use' parameter is second priority
           - Direct 'hosts' configuration is third priority
           - Default to 'local' host if no connection is specified
           - Parameters stored in a single host dict proceeds connection paramters
           - Rule for parameter taken: Host, Connection (if used), task specific

        4. Standardizes task objects with:
           - Function reference
           - Module reference
           - ID and name
           - Timeout settings
           - Additional keyword arguments
           - Fully-resolved connection information

        The connection configuration follows a cascading resolution process
        to determine where tasks should be executed, with proper fallback to
        local execution if no remote hosts are specified.

        Raises:
            TokeoAutomateError: If task module is missing, invalid, or cannot
                be imported, or for other configuration errors.
        """

        # a simple wrapper to raise not exist function error on runtime
        def __unknown_function(module, key):
            def wrapper(app, connection, **kwargs):
                raise TokeoAutomateError(f'A function named "{key}" does not exist in module "{_config_task['module']}"')

            return wrapper

        # initialize tasks
        self._tasks = {}
        # save list reference
        _config_tasks = self._config('tasks', fallback={})
        # test for a default module
        _default_module = _config_tasks.pop('module', None)
        if _default_module is not None and (not isinstance(_default_module, str) or str.strip(_default_module) == ''):
            raise TokeoAutomateError('A default module for tasks must be defined by a string')
        # loop and fullfill
        for key in _config_tasks:
            # get params for task
            _config_task = _config_tasks[key]
            # check for minimal configs
            if 'module' not in _config_task or _config_task['module'] is None or str.strip(_config_task['module']) == '':
                if _default_module is None:
                    raise TokeoAutomateError(f'The task "{key}" for automate must have a module defined to exist')
                else:
                    _config_task['module'] = _default_module
            # cache import module
            if _config_task['module'] not in self._modules:
                try:
                    self._modules[_config_task['module']] = importlib.import_module(_config_task['module'])
                except ModuleNotFoundError:
                    raise TokeoAutomateError(f'A module "{_config_task['module']}" could not be imported')
                except Exception:
                    raise
            # get the function
            module = self._modules[_config_task['module']]
            try:
                func = getattr(module, key)
            except AttributeError:
                func = __unknown_function(module, key)
            except Exception:
                raise
            # fullfill task
            task = dict(
                func=func,
                module=module,
                id=key,
                name=default_when_blank(_config_task.get('name'), key),
                timeout=_config_task.get('timeout'),
                kwargs=_config_task.get('kwargs', {}),
            )
            # Check for connection settings. The rule follows
            # first if 'connections' exist, second a `use`
            # relation, third a 'hosts' section, last fallback
            # is local command
            if 'connection' in _config_task:
                task['connection'] = _config_task['connection']
                if 'hosts' not in task['connection'] and 'use' not in task['connection']:
                    task['connection']['hosts'] = tuple(('local',))
            else:
                if 'use' in _config_task:
                    task['connection'] = dict(use=_config_task['use'])
                else:
                    if 'hosts' in _config_task:
                        # move hosts into inside struct
                        hosts = _config_task['hosts']
                        if isinstance(hosts, str):
                            hosts = tuple((hosts,))
                        if isinstance(hosts, list):
                            hosts = tuple(hosts)
                        # append hosts to dict
                        task['connection'] = dict(hosts=hosts)
                    else:
                        # without any hosts it's a local command
                        task['connection'] = dict(
                            hosts=tuple(('local',)),
                        )
                    # check if shell is defined by task
                    if 'shell' in _config_task:
                        task['connection']['shell'] = _config_task['shell']

            # replace with fullfilled connection
            task['connection'] = self._setup_connection(task['connection'].copy())
            # add the task to the list
            self._tasks[key] = task

    @property
    def tasks(self):
        """
        Dictionary of configured automation tasks.

        Lazily loads and caches the task configurations from the application
        configuration. Tasks include references to the Python functions
        to execute, the hosts or connections to use, and any additional
        parameters.

        Returns:
            Dictionary mapping task IDs to task configuration dictionaries.
        """
        if self._tasks is None:
            self._get_tasks()
        return self._tasks

    def _run_connections(self, task):
        run_connections = []
        connection = task.get('connection', {})
        for _host in connection['hosts']:
            # setup dynamic arguments for connection and connect_kwargs
            connect_args = {}
            connect_kwargs = {}
            connect_config = {}
            # add sudo only if sudo password is given
            if 'sudo' in _host and _host['sudo'] or 'sudo' in connection and connection['sudo']:
                connect_config['sudo'] = dict(password=_host['sudo'] if 'sudo' in _host and _host['sudo'] else connection['sudo'])
            if 'shell' in _host and _host['shell'] or 'shell' in connection and connection['shell']:
                connect_config['run'] = dict(shell=_host['shell'] if 'shell' in _host and _host['shell'] else connection['shell'])
            # create local invoke or ssh client
            if _host['host'] == 'local':
                run_connections.append(
                    dict(
                        connection_id=connection['id'],
                        host_id=_host['id'],
                        context=invoke.Context(config=invoke.Config(overrides={**connect_config})),
                    )
                )
            else:
                # define some settings per default
                connect_kwargs['look_for_keys'] = False
                connect_kwargs['allow_agent'] = False
                connect_args['forward_agent'] = False
                # host and port are mandatory
                connect_args['host'] = _host['host']
                connect_args['port'] = _host['port'] if 'port' in _host and _host['port'] else connection['port']
                # add user only if user is given
                if 'user' in _host and _host['user'] or 'user' in connection:
                    connect_args['user'] = _host['user'] if 'user' in _host and _host['user'] else connection['user']
                # add password only if password is given
                if 'password' in _host and _host['password'] or 'password' in connection:
                    connect_kwargs['password'] = _host['password'] if 'password' in _host and _host['password'] else connection['password']
                # setup and attach the connection
                if 'lookup_keys' in connection and isinstance(connection['lookup_keys'], bool) and connection['lookup_keys']:
                    connect_kwargs['look_for_keys'] = True
                if 'allow_agent' in connection and isinstance(connection['allow_agent'], bool) and connection['allow_agent']:
                    connect_kwargs['allow_agent'] = True
                    if 'forward_agent' in connection and isinstance(connection['forward_agent'], bool) and connection['forward_agent']:
                        connect_args['forward_agent'] = True
                if 'connect_timeout' in connection and (
                    isinstance(connection['connect_timeout'], int) or isinstance(connection['connect_timeout'], str)
                ):
                    connect_kwargs['timeout'] = int(connection['connect_timeout'])
                if 'identity' in connection and connection['identity'] is not None:
                    connect_kwargs['key_filename'] = connection['identity']
                # create the connection
                fabric_conn = fabric.Connection(
                    **connect_args, config=fabric.Config(overrides={**connect_config}), connect_kwargs={**connect_kwargs}
                )
                # modify connection
                if 'host_key' in _host and _host['host_key'] or 'known_hosts' in connection and connection['known_hosts']:
                    # get the list of host keys from known_hosts like strings
                    if 'host_key' in _host and _host['host_key']:
                        known_hosts = f'{_host["host"]} {_host["host_key"]}'
                    else:
                        known_hosts = connection['known_hosts']
                    # if just string rebuild to list
                    if isinstance(known_hosts, str):
                        known_hosts = tuple((known_hosts,))
                    # create a memory host_keys
                    host_keys = HostKeys()
                    # append keys
                    for k in known_hosts:
                        entry = HostKeyEntry.from_line(k)
                        host_keys.add(entry.hostnames[0], entry.key.get_name(), entry.key)
                    # disable connection to unknown hosts
                    fabric_conn.client.set_missing_host_key_policy(paramiko.RejectPolicy())
                    # append the valid host_keys
                    fabric_conn.client.get_host_keys().update(host_keys)
                else:
                    fabric_conn.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                # append to stack
                run_connections.append(
                    dict(
                        connection_id=connection['id'],
                        host_id=_host['id'],
                        context=fabric_conn,
                    )
                )

        return tuple((run_connections))

    def run(self, task, filter_host_ids=(), verbose=False):
        """
        Execute a task on the configured hosts.

        Runs the specified task on all hosts defined in the task's connection,
        optionally filtering to specific hosts. Collects the results from each
        execution.

        Args:
            task: Task configuration dictionary.
            filter_host_ids: Optional tuple of host IDs to restrict execution.
            verbose: Whether to enable verbose execution output.

        Returns:
            Tuple of TokeoAutomateResult objects, one for each host execution.
        """
        # list results for all connections
        results = []
        run_connections = self._run_connections(task)
        for run_connection in run_connections:
            if len(filter_host_ids) == 0 or run_connection['host_id'] in filter_host_ids:
                results.append(
                    TokeoAutomateResult(
                        task['id'],
                        run_connection['connection_id'],
                        run_connection['host_id'],
                        task['func'](self.app, run_connection['context'], verbose=verbose, **task['kwargs']),
                    )
                )
        # return that content
        return tuple(results)

    def run_thread(self, task, verbose=False):
        t = Thread(target=self.run, arg=(task, verbose))
        t.start()
        return t

    def run_sequential(
        self,
        task_ids,
        with_hosts=None,
        with_connection=None,
        continue_on_error=False,
        verbose=False,
        return_results=False,
        return_outputs=True,
    ):
        """
        Execute multiple tasks sequentially.

        Runs a sequence of tasks one after another, optionally continuing
        even if some tasks fail. Tasks can be overridden to use specific
         hosts or connections.

        Args:
            task_ids: String or sequence of task IDs to execute.
            with_hosts: Optional host or hosts to override task configuration.
            with_connection: Optional connection to override task configuration.
            continue_on_error: Whether to continue executing tasks after errors.
            verbose: Whether to enable verbose execution output.
            return_results: Whether to include full result details in results.
            return_outputs: Whether to include stdout/stderr in results.

        Returns:
            Dictionary with keys:
                sum_exited_code: 0 for success, non-zero for errors
                results: List of task results if return_results is True

        Raises:
            TokeoAutomateError: If a task ID does not exist.
        """
        # test tasks_ids
        if isinstance(task_ids, str):
            task_ids = tuple((task_ids,))
        if not isinstance(task_ids, (list, tuple)):
            raise TokeoAutomateError('run_sequential must be called with one or many task ids')

        # check overrulers for hosts
        if isinstance(with_hosts, str):
            if with_hosts in self.hosts:
                _with_hosts = tuple((self.hosts[with_hosts],))
            else:
                _with_hosts = tuple((self._get_host_dict_from_str(with_hosts),))
        elif isinstance(with_hosts, (list, tuple, dict)):
            _with_hosts = with_hosts
        else:
            _with_hosts = None

        # check overrulers for connection
        if isinstance(with_connection, str):
            _with_connection = self.connections['connections'][with_connection]
        elif isinstance(with_connection, dict):
            _with_connection = self._get_connection_dict(None, with_connection)
            if 'use' in with_connection:
                _with_connection['use'] = with_connection['use']
        else:
            _with_connection = None

        # flag for getting all exit codes from run commands
        sum_exited_code = 0
        # list for all result details
        results = []
        # run all given tasks from command line
        for task_host_id in task_ids:
            if sum_exited_code == 0 or continue_on_error:
                try:
                    # split into separated task and host filter
                    split_task_host_id = task_host_id.split(':') + [None]
                    task_id = split_task_host_id[0]
                    host_id = tuple((split_task_host_id[1],)) if split_task_host_id[1] else ()
                    # check before start
                    if task_id not in self.tasks:
                        raise TokeoAutomateError(f'Task "{task_id}" is not defined yet')
                    # get the task object
                    task = self.tasks[task_id].copy()
                    # test overules
                    if _with_connection:
                        task['connection'] = self._setup_connection(_with_connection)
                    if _with_hosts:
                        task['connection']['hosts'] = _with_hosts
                        task['connection'] = self._setup_connection(task['connection'])
                    # use and run one by one
                    run_results = self.app.automate.run(task, filter_host_ids=host_id, verbose=verbose)
                    # prepare empty list for this run results
                    run_results_return = []
                    # loop results
                    for run_result in run_results:
                        # check for any error and set flag
                        if run_result.exited != 0:
                            sum_exited_code = 1
                        # check if results need to be stored for output
                        if return_results:
                            # drop outputs
                            if not return_outputs:
                                run_result.stdout = None
                                run_result.stderr = None
                            # append the results details to list
                            run_results_return.append(run_result)
                    # append to overall list
                    if len(run_results_return) > 0:
                        results.append(tuple(run_results_return))

                except Exception as e:
                    # handle all other errors
                    self.app.log.error(e)
                    # save flag only if no other error was encountered
                    if sum_exited_code == 0:
                        sum_exited_code = -1
                    # prepare output
                    if return_results:
                        results.append(
                            TokeoAutomateResult(
                                # fmt: skip
                                task_id,
                                None,
                                None,
                                dict(stdout='', stderr=f'{e}', command=f'automate run {task_id}', exited=-1),
                            )
                        )

        # return the results as dict
        return dict(sum_exited_code=sum_exited_code, results=results)

    def run_threaded(
        self,
        max_workers,
        task_ids,
        with_hosts=None,
        with_connection=None,
        verbose=False,
        return_results=False,
        return_outputs=True,
    ):
        # test tasks_ids
        if isinstance(task_ids, str):
            task_ids = tuple((task_ids,))
        if not isinstance(task_ids, (list, tuple)):
            raise TokeoAutomateError('run_threaded must be called with one or many task ids')

        # check overrulers for hosts
        if isinstance(with_hosts, str):
            if with_hosts in self.hosts:
                _with_hosts = tuple((self.hosts[with_hosts],))
            else:
                _with_hosts = tuple((self._get_host_dict_from_str(with_hosts),))
        elif isinstance(with_hosts, (list, tuple, dict)):
            _with_hosts = with_hosts
        else:
            _with_hosts = None

        # check overrulers for connection
        if isinstance(with_connection, str):
            _with_connection = self.connections['connections'][with_connection]
        elif isinstance(with_connection, dict):
            _with_connection = self._get_connection_dict(None, with_connection)
            if 'use' in with_connection:
                _with_connection['use'] = with_connection['use']
        else:
            _with_connection = None

        # flag for getting all exit codes from run commands
        sum_exited_code = 0
        # list for all result details
        results = []
        # create the thread pool
        with concurrent_futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # list of initiated threads
            futures = []
            # run all given tasks from command line
            for task_host_id in task_ids:
                try:
                    # split into separated task and host filter
                    split_task_host_id = task_host_id.split(':') + [None]
                    task_id = split_task_host_id[0]
                    host_id = tuple((split_task_host_id[1],)) if split_task_host_id[1] else ()
                    # check before start
                    if task_id not in self.tasks:
                        raise TokeoAutomateError(f'Task "{task_id}" is not defined yet')
                    # get the task object
                    task = self.tasks[task_id].copy()
                    # test overules
                    if _with_connection:
                        task['connection'] = self._setup_connection(_with_connection)
                    if _with_hosts:
                        task['connection']['hosts'] = _with_hosts
                        task['connection'] = self._setup_connection(task['connection'])
                    # put task on pool
                    futures.append(executor.submit(self.run, task, host_id, verbose))

                # exception while create
                except Exception as e:
                    # handle all other errors
                    self.app.log.error(e)
                    # save flag only if no other error was encountered
                    if sum_exited_code == 0:
                        sum_exited_code = -1
                    # prepare output
                    if return_results:
                        results.append(
                            TokeoAutomateResult(
                                # fmt: skip
                                task_id,
                                None,
                                None,
                                dict(stdout='', stderr=f'{e}', command=f'automate run {task_id}', exited=-1),
                            )
                        )

            # check processing of all threads and result
            for future in concurrent_futures.as_completed(futures):
                try:
                    # use and run one by one
                    run_results = future.result()
                    # prepare empty list for this run results
                    run_results_return = []
                    # loop results
                    for run_result in run_results:
                        # check for any error and set flag
                        if run_result.exited != 0:
                            sum_exited_code = 1
                        # check if results need to be stored for output
                        if return_results:
                            # drop outputs
                            if not return_outputs:
                                run_result.stdout = None
                                run_result.stderr = None
                            # append the results details to list
                            run_results_return.append(run_result)
                    # append to overall list
                    if len(run_results_return) > 0:
                        results.append(tuple(run_results_return))

                # handle running exceptions
                except Exception as e:
                    # handle all other errors
                    self.app.log.error(e)
                    # save flag only if no other error was encountered
                    if sum_exited_code == 0:
                        sum_exited_code = -1
                    # prepare output
                    if return_results:
                        results.append(
                            TokeoAutomateResult(
                                # fmt: skip
                                task_id,
                                None,
                                None,
                                dict(stdout='', stderr=f'{e}', command=f'automate run {task_id}', exited=-1),
                            )
                        )

        # return the results as dict
        return dict(sum_exited_code=sum_exited_code, results=results)


class TokeoAutomateShell:

    def __init__(self, app):
        self.app = app
        self._command_parser = None
        self._shell_completion = None

    def startup(self):
        self.app.automate.tasks

    def shutdown(self, signum=None, frame=None):
        # only shutdown if initialized
        pass

    def launch(self):
        self.startup()
        self.shell()

    def shell_completion(self):
        if self._shell_completion is None:
            # create a completion set for tasks and tasks by hosts
            tasks_completion = []
            tasks_host_completion = []
            for task_id in self.app.automate.tasks:
                tasks_completion.append(f'{task_id}')
                for host_id in self.app.automate.tasks[task_id]['connection']['hosts']:
                    tasks_host_completion.append(f'{task_id}:{host_id["id"]}')
            # create completer
            wordlist_show = WordCompleter(
                # fmt: skip
                sorted(set(tasks_completion))
            )
            wordlist_run = WordCompleter(
                sorted(set(tasks_completion + tasks_host_completion)) + ['--verbose', '--as-json', '--without-output', '--threads']
            )
            self._shell_completion = NestedCompleter.from_nested_dict(
                {
                    'list': None,
                    'show': wordlist_show,
                    'run': wordlist_run,
                    'hosts': {
                        'list': None,
                    },
                    'hostgroups': {
                        'list': None,
                    },
                    'connections': {
                        'list': None,
                    },
                    'exit': None,
                    'quit': None,
                },
            )
        # return the completion set
        return self._shell_completion

    def shell_history(self):
        return InMemoryHistory(
            [
                'exit',
                'list',
            ]
        )

    def handle_command_list(self, args):
        self.app.log.debug(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %z (%Z)'))
        for task_id in self.app.automate.tasks:
            task = self.app.automate.tasks[task_id]
            if task['id'] == task['name']:
                print(f'{task["id"]}')
            else:
                print(f'{task["id"]} - {task["name"]}')

    def handle_command_show(self, args):
        for task_id in args.task:
            try:
                task = self.app.automate.tasks[task_id]
                print(f'{task}')
            except Exception as err:
                self.app.log.error(err)

    def handle_command_run(self, args):
        # setup run kwargs from args
        run_args = {}
        run_args['verbose'] = args.verbose
        run_args['return_results'] = args.as_json
        run_args['return_outputs'] = not args.without_output
        # run command line wit args
        if args.threads >= 1:
            res = self.app.automate.run_threaded(args.threads, args.task, **run_args)
        else:
            res = self.app.automate.run_sequential(args.task, **run_args)
        if args.as_json:
            self.app.print(
                jsonDump(
                    res['results'],
                    default=jsonTokeoAutomateEncoder,
                    encoding=None,
                )
            )

    def handle_subcommand_commands(self, args):
        try:
            if args.cmd == 'hosts.list':
                for host in self.app.automate.hosts:
                    print(f'{host}: {self.app.automate.hosts[host]}')
            elif args.cmd == 'groups.list':
                for hostgroup in self.app.automate.hostgroups:
                    print(f'{hostgroup}: {self.app.automate.hostgroups[hostgroup]}')
            elif args.cmd == 'conns.list':
                conn = '_default'
                print(f'{conn}: {self.app.automate.connections[conn]}')
                for conn in self.app.automate.connections['connections']:
                    print(f'{conn}: {self.app.automate.connections['connections'][conn]}')
        except Exception as err:
            self.app.log.error(err)

    def handle_subcommand_help(self, args):
        args.print_help()

    @property
    def command_parser(self):
        if self._command_parser is None:
            # if not created, generate the nested command parser
            self._command_parser = ArgumentParser(
                prog='',
                description='control the automate shell',
                epilog='',
            )

            # prepare for sub-commands
            sub = self._command_parser.add_subparsers(metavar='')
            # tasks list command
            cmd = sub.add_parser('list', help='show active scheduler tasks')
            cmd.set_defaults(func=self.handle_command_list)
            # scheduler pause command
            cmd = sub.add_parser('show', help='pause the scheduler')
            cmd.add_argument('task', nargs='+', help='task_id(s) to show')
            cmd.set_defaults(func=self.handle_command_show)
            # scheduler pause command
            cmd = sub.add_parser('run', help='start the scheduler')
            cmd.add_argument('task', nargs='+', help='task_id(s)[:host] to run')
            cmd.add_argument('--verbose', action='store_true', help='show output from command execution')
            cmd.add_argument('--as-json', action='store_true', help='show results as json')
            cmd.add_argument('--without-output', action='store_true', help='hide outputs from stdout and stderr')
            cmd.add_argument('--threads', type=int, default=0, help='run by number of threads')
            cmd.set_defaults(func=self.handle_command_run)

            # nested tasks sub-commands
            nested = sub.add_parser('hosts', help='about hosts')
            nested.set_defaults(func=self.handle_subcommand_help, print_help=nested.print_help)
            nested = nested.add_subparsers(metavar='')
            # tasks remove command
            cmd = nested.add_parser('list', help='show the configured hosts')
            cmd.set_defaults(func=self.handle_subcommand_commands, cmd='hosts.list')
            # nested tasks sub-commands
            nested = sub.add_parser('hostgroups', help='about hostgroups')
            nested.set_defaults(func=self.handle_subcommand_help, print_help=nested.print_help)
            nested = nested.add_subparsers(metavar='')
            # tasks remove command
            cmd = nested.add_parser('list', help='show the configured hostgroups')
            cmd.set_defaults(func=self.handle_subcommand_commands, cmd='groups.list')
            # nested tasks sub-commands
            nested = sub.add_parser('connections', help='about connections')
            nested.set_defaults(func=self.handle_subcommand_help, print_help=nested.print_help)
            nested = nested.add_subparsers(metavar='')
            # tasks remove command
            cmd = nested.add_parser('list', help='show the configured connections')
            cmd.set_defaults(func=self.handle_subcommand_commands, cmd='conns.list')

        # return initialized parser
        return self._command_parser

    def command(self, cmd=''):
        # signal bye bye to interactive shell
        if cmd in ['exit', 'quit']:
            raise EOFError('Command exit entered.')

        # check command
        splitted_cmd = shlex.split(cmd)
        try:
            args = self.command_parser.parse_args(args=splitted_cmd)
        except SystemExit as err:
            # if parse was ok but only help, err == 0, else err != 0
            return err.code == 0

        # execute command
        if 'func' in args:
            try:
                print('')
                args.func(args)
                return True
            except Exception as err:
                self.app.log.debug(f'{type(err)}: {err}')
                return False

        # unusual empty but valid command, can be added to history
        return True

    def shell(self):
        self.app.log.info('Welcome to automate interactive shell.')
        # build in-memory history for interactive shell
        history = self.shell_history()
        # initilize the user_input
        user_input = ''
        # get std.output and prevent ruining interface
        with patch_stdout(raw=True):
            # loop interactove shell
            while True:
                # catch exceptions
                try:
                    user_input = prompt(
                        'Automate> ',
                        completer=self.shell_completion(),
                        history=history,
                        auto_suggest=AutoSuggestFromHistory(),
                        default=user_input,
                    )
                    if self.command(user_input):
                        # add input to history when a successful command
                        # but do not repeat as input
                        history.store_string(user_input)
                        user_input = ''
                    else:
                        # repeat the error input to edit and correct
                        pass

                except KeyboardInterrupt:
                    # we don't support Ctrl-C but reset input
                    user_input = ''
                    continue
                except EOFError:
                    # we do support Ctrl-D
                    self.app.log.info('bye bye using automate shell...')
                    break
                except CaughtSignal as err:
                    # check for catched signals and allow shutdown by signals
                    if err.signum in SIGNALS:
                        break
                except Exception as err:
                    # print out the exception and continue
                    self.app.log.debug(f'Logged unknown exception: {err}')

                # make one line space
                print('')


class TokeoAutomateController(Controller):
    """
    Controller for the automate CLI commands.

    Provides command-line interface commands for running tasks and launching
    the interactive automation shell.
    """

    class Meta:
        """Controller configuration."""

        label = 'automate'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'Start and manage recurring local and remote tasks with tokeo automate'
        description = (
            'Start the tokeo automate to control and manage running recurring tasks. '
            'Utilize a range of predefined actions and a shell for an interactive task handling.'
        )
        epilog = f'Example: {basename(argv[0])} automate shell'

    def _setup(self, app):
        super(TokeoAutomateController, self)._setup(app)

    def log_info_bw(self, *args):
        print('INFO:', *args)

    def log_warning_bw(self, *args):
        print('WARN:', *args)

    def log_error_bw(self, *args):
        print('ERR:', *args)

    def log_debug_bw(self, *args):
        print('DEBUG:', *args)

    def log_info(self, *args):
        print('\033[32mINFO:', *args, '\033[39m')

    def log_warning(self, *args):
        print('\033[33mWARN:', *args, '\033[39m')

    def log_error(self, *args):
        print('\033[31mERR:', *args, '\033[39m')

    def log_debug(self, *args):
        print('\033[35mDEBUG:', *args, '\033[39m')

    @ex(
        help='run task(s)',
        description='Run one or many configured tasks.',
        arguments=[
            (
                ['task'],
                dict(
                    nargs='+',
                    help='task(s)[:host] to run',
                ),
            ),
            (
                ['--with-hosts'],
                dict(
                    type=str,
                    default=None,
                    help='run tasks but set the hosts by parameter from string or by yaml',
                ),
            ),
            (
                ['--with-connection'],
                dict(
                    type=str,
                    default=None,
                    help='run tasks but set the connection by parameter as id or by yaml',
                ),
            ),
            (
                ['--threads'],
                dict(
                    type=int,
                    default=0,
                    help='run number of task[:host] by number of threads (default=0)',
                ),
            ),
            (
                ['--verbose'],
                dict(
                    action='store_true',
                    help='show output from command execution',
                ),
            ),
            (
                ['--continue'],
                dict(
                    dest='continue_run',
                    action='store_true',
                    help='continue with next task(s) also having errors',
                ),
            ),
            (
                ['--as-json'],
                dict(
                    action='store_true',
                    help='return result(s) as json',
                ),
            ),
            (
                ['--encode-utf8'],
                dict(
                    action='store_true',
                    help='encode the json [--as-json] result(s) as utf-8',
                ),
            ),
            (
                ['--without-output'],
                dict(
                    action='store_true',
                    help='no outputs from stdout and stderr in json [--as-json] result(s)',
                ),
            ),
        ],
    )
    def run(self):
        # setup kwargs
        kwargs = dict(
            verbose=self.app.pargs.verbose, return_results=self.app.pargs.as_json, return_outputs=not self.app.pargs.without_output
        )
        # get hosts by --with-hosts
        if self.app.pargs.with_hosts:
            try:
                kwargs['with_hosts'] = yaml.safe_load(self.app.pargs.with_hosts)
            except (yaml.error.MarkedYAMLError, yaml.error.YAMLError):
                kwargs['with_hosts'] = self.app.pargs.with_hosts
        # get connection by --with-connection
        if self.app.pargs.with_connection:
            try:
                kwargs['with_connection'] = yaml.safe_load(self.app.pargs.with_connection)
            except (yaml.error.MarkedYAMLError, yaml.error.YAMLError):
                kwargs['with_connection'] = self.app.pargs.with_connection

        # use the internal processings
        if self.app.pargs.threads > 0:
            res = self.app.automate.run_threaded(self.app.pargs.threads, self.app.pargs.task, **kwargs)
        else:
            res = self.app.automate.run_sequential(self.app.pargs.task, continue_on_error=self.app.pargs.continue_run, **kwargs)

        # check for json and encoding
        if self.app.pargs.as_json:
            self.app.print(
                jsonDump(
                    res['results'],
                    default=jsonTokeoAutomateEncoder,
                    encoding='utf-8' if self.app.pargs.encode_utf8 else None,
                )
            )

        # return the loggeg exit codes
        self.app.exit_code = res['sum_exited_code']

    @ex(
        help='start the automate command shell',
        description='Spin up the interactive automation shell.',
        arguments=[
            (
                ['--no-colors'],
                dict(
                    action='store_true',
                    help='do not use colored output',
                ),
            ),
        ],
    )
    def shell(self):
        # use colored output?
        if self.app.pargs.no_colors:
            self.app.log.info = self.log_info_bw
            self.app.log.warning = self.log_warning_bw
            self.app.log.error = self.log_error_bw
            self.app.log.debug = self.log_debug_bw
        else:
            self.app.log.info = self.log_info
            self.app.log.warning = self.log_warning
            self.app.log.error = self.log_error
            self.app.log.debug = self.log_debug

        # start the shell
        shell = TokeoAutomateShell(self.app)
        shell.launch()


def tokeo_automate_extend_app(app):
    """
    Initialize and register the automate extension with the application.

    Args:
        app: The Cement application instance.
    """
    app.extend('automate', TokeoAutomate(app))
    app.automate._setup(app)


def tokeo_automate_shutdown(app):
    """
    Perform any cleanup needed when shutting down the automate extension.

    Args:
        app: The Cement application instance.
    """
    pass


def load(app):
    """
    Load the automate extension into the application.

    This function is called by Cement when loading extensions.

    Args:
        app: The Cement application instance.
    """
    app.handler.register(TokeoAutomateController)
    app.hook.register('post_setup', tokeo_automate_extend_app)
    app.hook.register('pre_close', tokeo_automate_shutdown)
