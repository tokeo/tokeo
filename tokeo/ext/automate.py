from sys import argv
from os.path import basename
from datetime import datetime, timezone
from tokeo.core.utils.json import jsonDump, jsonTokeoEncoder
from tokeo.ext.argparse import Controller
from cement.core.meta import MetaMixin
from cement import ex
from cement.core.foundation import SIGNALS
from cement.core.exc import CaughtSignal
from argparse import ArgumentParser
import shlex
from prompt_toolkit import prompt
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import NestedCompleter, WordCompleter
from threading import Thread
from concurrent import futures as concurrent_futures
import importlib
import invoke
import fabric
import paramiko
from paramiko.hostkeys import HostKeys, HostKeyEntry


def jsonTokeoAutomateEncoder(obj):
    # test for automate result
    if isinstance(obj, TokeoAutomateResult):
        return obj.__dict__
    # continue with tokeo encoder
    return jsonTokeoEncoder(obj)


class TokeoAutomateError(Exception):
    """Tokeo automate errors."""

    pass


class TokeoAutomateResult:

    def __init__(self, task_id, connection_id, host_id, result):
        self.task_id = task_id
        self.connection_id = connection_id
        self.host_id = host_id
        # get content from give result (by function return or set on create)
        # already type of inkoke.runners.Result
        if isinstance(result, invoke.runners.Result):
            # setup the values
            self.stdout = result.stdout
            self.stderr = result.stderr
            self.command = result.command
            self.exited = result.exited
            # return the dict as values if the dict does not is a runners result
            # otherwise any computing results have to be added as values
            try:
                self.values = result.values
            except Exception:
                self.values = None
        else:
            # test result type
            try:
                has_invoke_result = 'stdout' in result or 'stderr' in result or 'command' in result or 'exited' in result
                like_invoke_result = 'stdout' in result and 'stderr' in result and 'command' in result and 'exited' in result
            except Exception:
                # not iterable or other
                has_invoke_result = False
                like_invoke_result = False
            # check for iterable result
            if has_invoke_result:
                # setup the values
                self.stdout = result['stdout'] if 'stdout' in result else None
                self.stderr = result['stderr'] if 'stderr' in result else None
                self.command = result['command'] if 'command' in result else None
                self.exited = result['exited'] if 'exited' in result else 0
                # return the dict as values if the dict is not a runners result
                # otherwise any computing results have to be added as values
                self.values = result if not like_invoke_result else result['values'] if 'values' in result else None
            else:
                # this is func specific return or none
                self.stdout = None
                self.stderr = None
                self.command = None
                self.exited = 0
                self.values = result

    @property
    def __dict__(self):
        return dict(
            task_id=self.task_id,
            connection_id=self.connection_id,
            host_id=self.host_id,
            stdout=self.stdout,
            stderr=self.stderr,
            command=self.command,
            exited=self.exited,
            values=self.values,
        )

    def __repr__(self):
        return self.__dict__.__repr__()


class TokeoAutomate(MetaMixin):

    class Meta:
        """Extension meta-data."""

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
        self._tasks = None
        self._modules = {}
        self._hosts = None
        self._hostgroups = None
        self._connections = None

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
        # Create a dict with all given and allowed fields
        # any field set hereby will never be overwritten
        # by any merge. If some want to set the password
        # by connection e.g., the password field should
        # not exist here.
        if not isinstance(entry, dict) or 'host' not in entry:
            raise TokeoAutomateError('To define a host entry there must be at least a dict with a "host" field')
        # setup the dict
        d = dict(id=key, name=entry['name'] if 'name' in entry else key, host=entry['host'])
        for field in ('port', 'user', 'password', 'sudo', 'identity', 'host_key'):
            if field in entry:
                d[field] = entry[field]
        # return the record
        return d

    # make a defined dict from a config entry
    def _get_connection_dict(self, key, entry):
        # Create a dict with all given and allowed fields
        # any field set hereby will never be overwritten
        # by any merge. If some want to set the password
        # by connection e.g., the password field should
        # not exist here.
        d = dict(
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
        ):
            if field in entry:
                d[field] = entry[field]
        # return the record
        return d

    @property
    def hosts(self):
        """
        This setup a list of defined host dicts
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
                # only sudo property may be set by config
                # all other props will be dropped
                if 'sudo' in _config_host:
                    _config_host = dict(host=key, sudo=_config_host['sudo'])
                else:
                    _config_host = dict(host=key)
            # build entry
            host = self._get_host_dict(key, _config_host)
            # add the filled entry
            self._hosts[key] = host
        # return property
        return self._hosts

    @property
    def hostgroups(self):
        """
        This defines a group of hosts by host, hostgroup and single entry.
        The rule is: first lookup from hosts for id, second lookup from
        hostgroups for id, at least add a single (new) entry.
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
            for h in _config_hostgroup:
                # check if found as host
                if h in self.hosts:
                    hosts_list.append(self.hosts[h])
                elif h in self._hostgroups:
                    hosts_list.extend(self._hostgroups[h])
                else:
                    hosts_list.append(self._get_host_dict(h, dict(host=h)))
            # add the filled entry
            self._hostgroups[key] = tuple(hosts_list)
        # return property
        return self._hostgroups

    @property
    def connections(self):
        """
        This defines a list of connections, useable to access hosts.
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
        if 'connections' in _config_connections:
            _config_connections = _config_connections['connections']
        else:
            _config_connections = {}
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
        # initialize tasks
        self._tasks = {}
        # save list reference
        _config_tasks = self._config('tasks', fallback={})
        # loop and fullfill
        for key in _config_tasks:
            # get params for task
            _config_task = _config_tasks[key]
            # check for minimal configs
            if 'module' not in _config_task or _config_task['module'] is None or str.strip(_config_task['module']) == '':
                raise TokeoAutomateError(f'The task "{key}" for automate must have a module to exist')
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
                raise TokeoAutomateError(f'A function "{key}" does not exist in module "{_config_task['module']}"')
            except Exception:
                raise
            # fullfill task
            task = dict(
                func=func,
                module=module,
                id=key,
                name=_config_task['name'] if 'name' in _config_task and _config_task['name'] != '' else key,
                timeout=_config_task['timeout'] if 'timeout' in _config_task else None,
                kwargs=_config_task['kwargs'] if 'kwargs' in _config_task else {},
            )
            # Check for connection settings. The rule follows
            # first if 'connections' exist, second a `use`
            # relation, third a 'hosts' section, last fallback
            # is local command
            if 'connection' in _config_task:
                task['connection'] = _config_task['connection']
            else:
                if 'use' in _config_task:
                    task['connection'] = dict(use=_config_task['use'])
                elif 'hosts' in _config_task:
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
            # fullfill the connection
            connection = task['connection']
            # merge with use if defined
            connection = (self.connections['connections'][connection['use']] if 'use' in connection else {}) | connection
            # drop use if defined
            connection.pop('use', None)
            # merge with default but drop sub structs
            connection = self.Meta.config_defaults['connections'] | self.connections['_default'] | connection
            connection.pop('connections', None)
            # expand hosts to host_dicts
            hosts_list = []
            # check for list of hosts
            connection_hosts = connection['hosts']
            if isinstance(connection_hosts, str):
                connection_hosts = tuple((connection_hosts,))
            # loop the hosts
            for h in connection_hosts:
                if h in self.hosts:
                    hosts_list.append(self.hosts[h])
                elif h in self.hostgroups:
                    hosts_list.extend(self.hostgroups[h])
                else:
                    hosts_list.append(self._get_host_dict(h, dict(host=h)))
            # make dict list of hosts unique
            unique_hosts_list = {}
            for h in hosts_list:
                if not h['id'] in unique_hosts_list:
                    unique_hosts_list[h['id']] = h
            # replace the fullfilled hosts
            connection['hosts'] = tuple(unique_hosts_list.values())
            # replace with fullfilled connection
            task['connection'] = connection
            # add the task to the list
            self._tasks[key] = task

    @property
    def tasks(self):
        if self._tasks is None:
            self._get_tasks()
        return self._tasks

    def _run_connections(self, task):
        run_connections = []
        connection = task['connection']
        for h in connection['hosts']:
            # setup dynamic arguments for connection and connect_kwargs
            connect_args = {}
            connect_kwargs = {}
            connect_config = {}
            # add sudo only if password is given
            if 'sudo' in h and h['sudo'] or 'sudo' in connection and connection['sudo']:
                connect_config['sudo'] = dict(password=h['sudo'] if 'sudo' in h and h['sudo'] else connection['sudo'])
            # create local invoke or ssh client
            if h['host'] == 'local':
                run_connections.append([connection['id'], h['id'], invoke.Context(config=invoke.Config(overrides={**connect_config}))])
            else:
                # define some settings per default
                connect_kwargs['look_for_keys'] = False
                connect_kwargs['allow_agent'] = False
                connect_args['forward_agent'] = False
                # host and port are mandatory
                connect_args['host'] = h['host']
                connect_args['port'] = h['port'] if 'port' in h and h['port'] else connection['port']
                # add user only if user is given
                if 'user' in h and h['user'] or 'user' in connection:
                    connect_args['user'] = h['user'] if 'user' in h and h['user'] else connection['user']
                # add password only if password is given
                if 'password' in h and h['password'] or 'password' in connection:
                    connect_kwargs['password'] = h['password'] if 'password' in h and h['password'] else connection['password']
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
                if 'host_key' in h and h['host_key'] or 'known_hosts' in connection and connection['known_hosts']:
                    # get the list of host keys from known_hosts like strings
                    known_hosts = f'{h["host"]} {h["host_key"]}' if 'host_key' in h and h['host_key'] else connection['known_hosts']
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
                run_connections.append([connection['id'], h['id'], fabric_conn])

        return tuple((run_connections))

    def run(self, task, host_ids=(), verbose=False):
        # list results for all connections
        results = []
        run_connections = self._run_connections(task)
        for rc in run_connections:
            if len(host_ids) == 0 or rc[1] in host_ids:
                results.append(
                    TokeoAutomateResult(task['id'], rc[0], rc[1], task['func'](self.app, rc[2], verbose=verbose, **task['kwargs']))
                )
        # return that content
        return tuple(results)

    def run_thread(self, task, verbose=False):
        t = Thread(target=self.run, arg=(task, verbose))
        t.start()
        return t

    def run_sequential(self, task_ids, continue_on_error=False, verbose=False, return_results=False, return_outputs=True):
        # test tasks_ids
        if isinstance(task_ids, str):
            _ = tuple((task_ids,))
        if not isinstance(task_ids, list) and not isinstance(task_ids, tuple):
            raise TokeoAutomateError('runMany must be called with one or many task ids')
        # flag for getting all exit codes from run commands
        sum_exit_codes = 0
        # list for all result details
        results = []
        # run all given tasks from command line
        for t in task_ids:
            if sum_exit_codes == 0 or continue_on_error:
                try:
                    # split into separated task and host filter
                    a = t.split(':') + [None]
                    t = a[0]
                    h = tuple((a[1],)) if a[1] else ()
                    # check before start
                    if t not in self.tasks:
                        raise TokeoAutomateError(f'Task "{t}" is not configured yet')
                    # get the task object
                    task = self.tasks[t]
                    # use and run one by one
                    res = self.app.automate.run(task, host_ids=h, verbose=verbose)
                    # prepare empty list for this run results
                    r_list = []
                    # loop results
                    for r in res:
                        # check for any error and set flag
                        if r.exited != 0:
                            sum_exit_codes = 1
                        # check if results need to be stored for output
                        if return_results:
                            # append the results details to list
                            r_list.append(r)
                    # append to overall list
                    if len(r_list) > 0:
                        results.append(tuple(r_list))

                except Exception as e:
                    # handle all other errors
                    self.app.log.error(e)
                    # save flag only if no other error was encountered
                    if sum_exit_codes == 0:
                        sum_exit_codes = -1
                    # prepare output
                    if return_results:
                        results.append(
                            TokeoAutomateResult(t, None, None, dict(stdout='', stderr=f'{e}', command=f'automate run {t}', exited=-1))
                        )

        # return the results as dict
        return dict(sum_exit_codes=sum_exit_codes, results=results)

    def run_threaded(self, max_workers, task_ids, verbose=False, return_results=False, return_outputs=True):
        # test tasks_ids
        if isinstance(task_ids, str):
            _ = tuple((task_ids,))
        if not isinstance(task_ids, list) and not isinstance(task_ids, tuple):
            raise TokeoAutomateError('runMany must be called with one or many task ids')
        # flag for getting all exit codes from run commands
        sum_exit_codes = 0
        # list for all result details
        results = []
        # create the thread pool
        with concurrent_futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # list of initiated threads
            futures = []
            # run all given tasks from command line
            for t in task_ids:
                try:
                    # split into separated task and host filter
                    a = t.split(':') + [None]
                    t = a[0]
                    h = tuple((a[1],)) if a[1] else ()
                    # check before start
                    if t not in self.tasks:
                        raise TokeoAutomateError(f'Task "{t}" is not configured yet')
                    # get the task object
                    task = self.tasks[t]
                    # put task on pool
                    futures.append(executor.submit(self.run, task, h, verbose))

                # exception while create
                except Exception as e:
                    # handle all other errors
                    self.app.log.error(e)
                    # save flag only if no other error was encountered
                    if sum_exit_codes == 0:
                        sum_exit_codes = -1
                    # prepare output
                    if return_results:
                        results.append(
                            TokeoAutomateResult(t, None, None, dict(stdout='', stderr=f'{e}', command=f'automate run {t}', exited=-1))
                        )

            # check processing of all threads and result
            for future in concurrent_futures.as_completed(futures):
                try:
                    # use and run one by one
                    res = future.result()
                    # prepare empty list for this run results
                    r_list = []
                    # loop results
                    for r in res:
                        # check for any error and set flag
                        if r.exited != 0:
                            sum_exit_codes = 1
                        # check if results need to be stored for output
                        if return_results:
                            # append the results details to list
                            r_list.append(r)
                    # append to overall list
                    if len(r_list) > 0:
                        results.append(tuple(r_list))

                # handle running exceptions
                except Exception as e:
                    # handle all other errors
                    self.app.log.error(e)
                    # save flag only if no other error was encountered
                    if sum_exit_codes == 0:
                        sum_exit_codes = -1
                    # prepare output
                    if return_results:
                        results.append(
                            TokeoAutomateResult(t, None, None, dict(stdout='', stderr=f'{e}', command=f'automate run {t}', exited=-1))
                        )

        # return the results as dict
        return dict(sum_exit_codes=sum_exit_codes, results=results)


class TokeoAutomateShell:

    def __init__(self, app):
        self.app = app
        self._command_parser = None

    def startup(self):
        self.app.automate.tasks

    def shutdown(self, signum=None, frame=None):
        # only shutdown if initialized
        pass

    def launch(self):
        self.startup()
        self.shell()

    def shell_completion(self):
        # create a completion set for tasks and tasks by hosts
        t_completion = []
        t_host_completion = []
        for tid in self.app.automate.tasks:
            t_completion.append(f'{tid}')
            for hid in self.app.automate.tasks[tid]['connection']['hosts']:
                t_host_completion.append(f'{tid}:{hid["id"]}')
        wt = WordCompleter(t_completion)
        wh = WordCompleter(t_completion + t_host_completion)
        # return the completion set
        return NestedCompleter.from_nested_dict(
            {
                'list': None,
                'show': wt,
                'run': wh,
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

    def shell_history(self):
        return InMemoryHistory(
            [
                'exit',
                'list',
            ]
        )

    def handle_command_list(self, args):
        self.app.log.debug(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %z (%Z)'))
        for tid in self.app.automate.tasks:
            t = self.app.automate.tasks[tid]
            print(f'{t["id"]}' + f' - {t['name']}' if t['id'] != t['name'] else '')

    def handle_command_show(self, args):
        for tid in args.task:
            try:
                t = self.app.automate.tasks[tid]
                print(f'{t}')
            except Exception as err:
                self.app.log.error(err)

    def handle_command_run(self, args):
        if args.threads >= 1:
            self.app.automate.run_threaded(args.threads, args.task, verbose=args.verbose)
        else:
            self.app.automate.run_sequential(args.task, verbose=args.verbose)

    def handle_subcommand_commands(self, args):
        try:
            if args.cmd == 'hosts.list':
                for h in self.app.automate.hosts:
                    print(f'{h}: {self.app.automate.hosts[h]}')
            elif args.cmd == 'groups.list':
                for g in self.app.automate.hostgroups:
                    print(f'{g}: {self.app.automate.hostgroups[g]}')
            elif args.cmd == 'conns.list':
                c = '_default'
                print(f'{c}: {self.app.automate.connections[c]}')
                for c in self.app.automate.connections['connections']:
                    print(f'{c}: {self.app.automate.connections['connections'][c]}')
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
        n = shlex.split(cmd)
        try:
            args = self.command_parser.parse_args(args=n)
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
                    # we don't support Ctrl-C
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

    class Meta:
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
            (['task'], dict(nargs='+', help='task(s)[:host] to run')),
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
        self.app.exit_code = res['sum_exit_codes']

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
    app.extend('automate', TokeoAutomate(app))
    app.automate._setup(app)


def tokeo_automate_shutdown(app):
    pass


def load(app):
    app.handler.register(TokeoAutomateController)
    app.hook.register('post_setup', tokeo_automate_extend_app)
    app.hook.register('pre_close', tokeo_automate_shutdown)
