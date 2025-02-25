from sys import argv
from os.path import basename
from tokeo.ext.argparse import Controller
from cement.core.meta import MetaMixin
from cement import ex
from cement.core.foundation import SIGNALS
from cement.core.exc import CaughtSignal
from apscheduler.schedulers.base import STATE_RUNNING
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.base import MaxInstancesReachedError
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.cron import CronTrigger
from argparse import ArgumentParser
import shlex
from prompt_toolkit import prompt
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import NestedCompleter
from datetime import datetime, timezone, timedelta


class TokeoCronAndFireTrigger(CronTrigger):

    def __init__(
        self,
        year=None,
        month=None,
        day=None,
        week=None,
        day_of_week=None,
        hour=None,
        minute=None,
        second=None,
        start_date=None,
        end_date=None,
        timezone=None,
        jitter=None,
        delay=None,
    ):
        super().__init__(year, month, day, week, day_of_week, hour, minute, second, start_date, end_date, timezone, jitter)
        self.delay = delay

    @classmethod
    def from_crontab(cls, expr, timezone=None, jitter=None, delay=None):
        values = expr.split()
        if len(values) != 5:
            raise ValueError('Wrong number of fields; got {}, expected 5'.format(len(values)))

        return cls(
            minute=values[0],
            hour=values[1],
            day=values[2],
            month=values[3],
            day_of_week=values[4],
            timezone=timezone,
            jitter=jitter,
            delay=delay,
        )

    def get_next_fire_time(self, previous_fire_time, now):
        # check cron based trigger
        next_fire_time = super().get_next_fire_time(previous_fire_time, now)
        # if there is an additional delay, put it on top
        if self.delay is None:
            return next_fire_time
        else:
            return next_fire_time + timedelta(seconds=self.delay)


class TokeoScheduler(MetaMixin):

    class Meta:
        """Extension meta-data."""

        #: Unique identifier for this handler
        label = 'tokeo.scheduler'

        #: Id for config
        config_section = 'scheduler'

        #: Dict with initial settings
        config_defaults = dict(
            max_concurrent_jobs=10,
            timezone=None,
            tasks={},
        )

    def __init__(self, app, *args, **kw):
        super(TokeoScheduler, self).__init__(*args, **kw)
        self.app = app
        self._command_parser = None
        self._scheduler = None
        self._interactive = True
        self._tasks = None
        self._taskid = 0

    def _setup(self, app):
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)

    def _config(self, key, **kwargs):
        """
        This is a simple wrapper, and is equivalent to:
            ``self.app.config.get(<section>, <key>)``.
        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    @property
    def scheduler(self):
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler() if self._interactive else BlockingScheduler()
            self._scheduler.add_executor(ThreadPoolExecutor(max_workers=self._config('max_concurrent_jobs', fallback=10)), 'default')
        return self._scheduler

    # from BaseScheduler _process_jobs
    def process_job(self, job):
        try:
            executor = self.scheduler._lookup_executor(job.executor)
        except BaseException:
            self.app.log.error(f'Executor lookup "{job.executor}" failed for job "{job}" -- removing it from the job store')
            job.remove()

        try:
            executor.submit_job(job, [datetime.now(timezone.utc)])
        except MaxInstancesReachedError:
            self.app.log.warning(f'Execution of job "{job}" skipped: maximum number of running instances reached ({job.max_instances})')
        except BaseException:
            self.app.log.error(f'Error submitting job "{job}" to executor "{job.executor}"')

    @property
    def tasks(self):
        if self._tasks is None:
            self._tasks = self._config('tasks', fallback={})
        return self._tasks

    def add_crontab_task(
        self,
        module,
        func,
        crontab,
        coalesce=True,
        misfire_grace_time=None,
        delay=None,
        max_jitter=None,
        max_running_jobs=None,
        kwargs={},
        title='',
    ):
        if title == '':
            title = func
        self._taskid += 1

        self.scheduler.add_job(
            f'{module}:{func}',
            kwargs=kwargs,
            trigger=TokeoCronAndFireTrigger.from_crontab(
                crontab, jitter=max_jitter, delay=delay, timezone=self._config('timezone', fallback=None)
            ),
            name=f'{self._taskid}:{title}',
            id=f'{self._taskid}',
            coalesce=coalesce,
            misfire_grace_time=misfire_grace_time,
            max_instances=1 if max_running_jobs is None else max_running_jobs,
        )

    def init_tasks(self):
        # get the entries from config
        for key in self.tasks:
            # get params for task
            task = self.tasks[key]
            # make crontab lways as list
            crontab = task['crontab'] if isinstance(task['crontab'], list) else [task['crontab']]
            # get coalesce from string
            coalesce = task['coalesce'] if 'coalesce' in task else 'latest'
            if coalesce == 'latest':
                coalesce = True
            elif coalesce == 'earliest':
                coalesce = True
            elif coalesce == 'all':
                coalesce = False
            else:
                raise ValueError(f'Unsupported value "{coalesce}" for coalesce setting')
            # iterate crontab
            for entry in crontab:
                self.add_crontab_task(
                    task['module'],
                    key,
                    entry,
                    kwargs=task['kwargs'] if 'kwargs' in task else {},
                    title=task['name'] if 'name' in task and task['name'] != '' else key,
                    coalesce=coalesce,
                    misfire_grace_time=task['misfire_grace_time'] if 'misfire_grace_time' in task else None,
                    delay=task['delay'] if 'delay' in task else None,
                    max_jitter=task['max_jitter'] if 'max_jitter' in task else None,
                    max_running_jobs=task['max_running_jobs'] if 'max_running_jobs' in task else None,
                )

    def startup(self, interactive=True, paused=False):
        self._interactive = interactive
        self.app.log.info('Adding all scheduler tasks from config')
        self.init_tasks()
        if paused:
            self.app.log.warning('Initialize scheduler in paused mode')
        else:
            self.app.log.info('Spinning up scheduler')
        self.scheduler.start(paused=paused)

    def shutdown(self, signum=None, frame=None):
        # only shutdown if initialized
        if self._scheduler is not None:
            self.app.log.info('Shutdown scheduler')
            self._scheduler.shutdown()
            self._scheduler.remove_all_jobs()

    def launch(self, interactive=True, paused=False):
        self.app.scheduler.startup(interactive=interactive, paused=paused)
        if interactive:
            self.shell()

    def shell_completion(self):
        return NestedCompleter.from_nested_dict(
            {
                'list': None,
                'pause': None,
                'resume': None,
                'reload': {
                    '--restart': None,
                },
                'restart': None,
                'wakeup': None,
                'tasks': {
                    'pause': None,
                    'resume': None,
                    'remove': None,
                    'fire': None,
                },
                'exit': None,
                'quit': None,
            },
        )

    def shell_history(self):
        return InMemoryHistory(
            [
                'exit',
            ]
        )

    def handle_command_list(self, args):
        self.app.log.debug(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %z (%Z)'))
        self._scheduler.print_jobs()

    def handle_command_pause(self, args):
        self._scheduler.pause()

    def handle_command_resume(self, args):
        self._scheduler.resume()

    def handle_command_reload(self, args):
        # save running state
        is_running = self._scheduler.state == STATE_RUNNING
        # pause if running to prevent events while updating tasks
        if is_running:
            self._scheduler.pause()
        # drop the job queue
        self._scheduler.remove_all_jobs()
        # fill in from config again
        # attention: config get's not reload
        self.init_tasks()
        # set scheduler to running if was running or forced
        if is_running or args.restart:
            self._scheduler.resume()

    def handle_command_restart(self, args):
        args.restart = True
        self.handle_command_reload(args)

    def handle_command_wakeup(self, args):
        self._scheduler.wakeup()

    def handle_command_task_commands(self, args):
        for task in args.task:
            try:
                job = self._scheduler.get_job(task)
                if job:
                    if args.cmd == 'remove':
                        self._scheduler.remove_job(task)
                    elif args.cmd == 'pause':
                        self._scheduler.pause_job(task)
                    elif args.cmd == 'resume':
                        self._scheduler.resume_job(task)
                    elif args.cmd == 'fire':
                        self.process_job(job)
                    # a short note
                    self.app.log.info(f'{args.cmd}d job {job.id} [{job.name}]')
                else:
                    raise ValueError(f'job {task} not found!')
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
                description='control the task scheduler',
                epilog='',
            )

            # prepare for sub-commands
            sub = self._command_parser.add_subparsers(metavar='')
            # tasks list command
            cmd = sub.add_parser('list', help='show active scheduler tasks')
            cmd.set_defaults(func=self.handle_command_list)
            # scheduler pause command
            cmd = sub.add_parser('pause', help='pause the scheduler')
            cmd.set_defaults(func=self.handle_command_pause)
            # scheduler pause command
            cmd = sub.add_parser('resume', help='start the scheduler')
            cmd.set_defaults(func=self.handle_command_resume)
            # scheduler reload command
            cmd = sub.add_parser('reload', help='reload the scheduling tasks from config')
            cmd.add_argument('--restart', action='store_true', help='start the scheduler after reload if paused')
            cmd.set_defaults(func=self.handle_command_reload)
            # scheduler restart command
            cmd = sub.add_parser('restart', help='reload and restart the scheduler from config')
            cmd.set_defaults(func=self.handle_command_restart)
            # scheduler wakeup command
            cmd = sub.add_parser('wakeup', help='notify scheduler to trigger _process_jobs')
            cmd.set_defaults(func=self.handle_command_wakeup)

            # nested tasks sub-commands
            nested = sub.add_parser('tasks', help='tasks manipulation')
            nested.set_defaults(func=self.handle_subcommand_help, print_help=nested.print_help)
            nested = nested.add_subparsers(metavar='')
            # tasks remove command
            cmd = nested.add_parser('remove', help='remove the task')
            cmd.add_argument('task', nargs='+', help='id of tasks to drop')
            cmd.set_defaults(func=self.handle_command_task_commands, cmd='remove')
            # tasks pause command
            cmd = nested.add_parser('pause', help='pause the task')
            cmd.add_argument('task', nargs='+', help='id of tasks to pause')
            cmd.set_defaults(func=self.handle_command_task_commands, cmd='pause')
            # tasks resume command
            cmd = nested.add_parser('resume', help='resume the task')
            cmd.add_argument('task', nargs='+', help='id of tasks to resume')
            cmd.set_defaults(func=self.handle_command_task_commands, cmd='resume')
            # tasks fire command
            cmd = nested.add_parser('fire', help='fire the task')
            cmd.add_argument('task', nargs='+', help='id of tasks to fire')
            cmd.set_defaults(func=self.handle_command_task_commands, cmd='fire')

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
        self.app.log.info('Welcome to scheduler interactive shell.')
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
                        'Scheduler> ' if self._scheduler.state == STATE_RUNNING else '(not running) Scheduler> ',
                        completer=self.shell_completion(),
                        history=history,
                        auto_suggest=AutoSuggestFromHistory(),
                        default=user_input,
                    )
                    if self.command(user_input):
                        # add input to history while a successful command
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
                    self.app.log.info('bye bye using scheduler...')
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


class TokeoSchedulerController(Controller):

    class Meta:
        label = 'scheduler'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'Start and manage timed tasks with tokeo scheduler'
        description = (
            'Start the tokeo scheduler to control and manage running '
            'repeating tasks. Utilize a range of scheduler commands '
            'and a shell for an interactive task handling.'
        )
        epilog = f'Example: {basename(argv[0])} scheduler launch --background'

    def _setup(self, app):
        super(TokeoSchedulerController, self)._setup(app)

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
        help='start the scheduler service',
        description='Spin up the scheduler.',
        arguments=[
            (
                ['--background'],
                dict(
                    action='store_true',
                    help='do not startup in interactive shell',
                ),
            ),
            (
                ['--paused'],
                dict(
                    action='store_true',
                    help='start the scheduler in paused mode',
                ),
            ),
            (
                ['--no-colors'],
                dict(
                    action='store_true',
                    help='do not use colored output',
                ),
            ),
        ],
    )
    def launch(self):
        # rewrite the output log handler for interactive
        # to run well with prompt toolkit
        if not self.app.pargs.background:
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
        # start the scheduler
        self.app.scheduler.launch(interactive=not self.app.pargs.background, paused=self.app.pargs.paused)


def tokeo_scheduler_extend_app(app):
    app.extend('scheduler', TokeoScheduler(app))
    app.scheduler._setup(app)


def tokeo_scheduler_shutdown(app):
    app.scheduler.shutdown()


def load(app):
    app.handler.register(TokeoSchedulerController)
    app.hook.register('post_setup', tokeo_scheduler_extend_app)
    app.hook.register('pre_close', tokeo_scheduler_shutdown)
