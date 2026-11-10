"""
Schedules tasks for Tokeo applications. It integrates the APScheduler library
to enable cron-style scheduled tasks and provides an interactive shell for
managing the scheduler at runtime.

The extension orchestrates the execution of background tasks with precise
timing controls and offers both programmatic and interactive management
of the task scheduler.

The shell and its lists build on the extensions ```tokeo.ext.tui.input``` and
```tokeo.ext.tui.output```; enable them together with this one.

### Features

- Cron-style scheduling with optional jitter and delay
- Task coalescing (latest, earliest, or all)
- Interactive command shell for task management
- Background or blocking scheduler modes
- Configuration-driven task setup
- Runtime task manipulation (pause, resume, remove, fire)
- Task definition via application configuration
- Control over maximum concurrent job execution

"""

from sys import argv
from os.path import basename
from tokeo.ext.argparse import Controller
from cement.core.meta import MetaMixin
from cement import ex
from apscheduler.schedulers.base import STATE_RUNNING
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.base import MaxInstancesReachedError
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.cron import CronTrigger
from argparse import ArgumentParser
import shlex
from datetime import datetime, timezone, timedelta
from tokeo.core.exc import TokeoError
from tokeo.core.cement.extensions import check_required_extensions


class TokeoSchedulerError(TokeoError, ValueError):
    """
    Exception raised for a scheduler config that cannot be used.

    ### Notes

    : Inherits from TokeoError, so main reports it as a clean message

    : Is also a ```ValueError```, so an ```except ValueError``` still catches
        it

    """

    pass


class TokeoCronAndFireTrigger(CronTrigger):
    """
    Enhanced CronTrigger that can be fired manually and supports an
    additional delay after the trigger time.

    This trigger extends the standard APScheduler CronTrigger with the ability
    to add a configurable delay after the scheduled time. This can be useful
    for staggering task execution or preventing resource contention, and it
    can also be fired manually on demand.

    ### Args

    - **year** (str|int, optional): Year to run on (4-digit)
    - **month** (str|int, optional): Month to run on (1-12)
    - **day** (str|int, optional): Day of month to run on (1-31)
    - **week** (str|int, optional): Week of the year to run on (1-53)
    - **day_of_week** (str|int, optional): Weekday to run on
        (0-6 or mon,tue,wed,thu,fri,sat,sun)
    - **hour** (str|int, optional): Hour to run on (0-23)
    - **minute** (str|int, optional): Minute to run on (0-59)
    - **second** (str|int, optional): Second to run on (0-59)
    - **start_date** (datetime|str, optional): Earliest date/time to run on
    - **end_date** (datetime|str, optional): Latest date/time to run on
    - **timezone** (datetime.tzinfo|str, optional): Timezone to use for
        the date/time calculations
    - **jitter** (int, optional): Advance or delay the job execution
        by jitter seconds at most
    - **delay_seconds** (int, optional): Additional seconds to delay after
        the scheduled time

    ### Notes

    : The delay_seconds parameter adds a fixed delay after the cron-calculated
        trigger time, while jitter adds a random amount of time (up to the
        specified maximum). Used together, they provide flexible control over
        task execution timing.

    : The cron parameters (year, month, etc.) support various formats including:

        - Single values: '5', 5
        - Ranges: '2-9', '0-23'
        - Multiple values: '1,3,5'
        - Step values: '*/2' (every 2 units)
        - Names for day_of_week: 'mon,wed,fri'

    """

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
        delay_seconds=None,
    ):
        super().__init__(year, month, day, week, day_of_week, hour, minute, second, start_date, end_date, timezone, jitter)
        #: Seconds the fire time is moved after the crontab time
        self.delay_seconds = delay_seconds

    @classmethod
    def from_crontab(cls, expr, timezone=None, jitter=None, delay_seconds=None):
        """
        Create a trigger from a standard crontab expression.

        Parses a crontab-format string and creates a trigger based on the
        expression. The standard crontab format uses five space-separated
        fields for minute, hour, day of month, month, and day of week.

        ### Args

        - **expr** (str): A crontab expression (5 fields: minute, hour,
            day of month, month, day of week)
        - **timezone** (datetime.tzinfo|str, optional): Timezone to use for
            date/time calculations
        - **jitter** (int, optional): Random seconds to add or subtract
            from trigger time
        - **delay_seconds** (int, optional): Fixed seconds to add after
            calculated trigger time

        ### Returns

        - **TokeoCronAndFireTrigger**: A new trigger instance configured with
            the expression

        ### Raises

        - **ValueError**: If the expression has the wrong number of fields

        """
        values = expr.split()
        if len(values) != 5:
            raise ValueError(f'Wrong number of fields; got {len(values)}, expected 5')

        return cls(
            minute=values[0],
            hour=values[1],
            day=values[2],
            month=values[3],
            day_of_week=values[4],
            timezone=timezone,
            jitter=jitter,
            delay_seconds=delay_seconds,
        )

    def get_next_fire_time(self, previous_fire_time, now):
        """
        Calculate the next time this trigger should fire.

        Extends the parent class's calculation by adding the configured delay
        to the next fire time determined by the cron expression.

        ### Args

        - **previous_fire_time** (datetime, optional): Previous firing time
            of the job or None
        - **now** (datetime): Current time (UTC)

        ### Returns

        - **datetime**: The next fire time or None if no future fire times
            are available

        ### Notes

        : This method first determines the next fire time using the standard
            cron calculation, then adds the configured delay (if any) to the
            result; jitter is applied by the parent class and affects the time
            before the delay is added

        : A delay greater than or equal to the cron interval pushes the fire
            time past the next slot, so individual fires can be skipped; keep
            delay smaller than the schedule interval to fire on every slot

        """
        # check cron based trigger
        next_fire_time = super().get_next_fire_time(previous_fire_time, now)
        # if there is an additional delay, put it on top; without a next
        # fire time the task fires no more, so there is no delay either
        if self.delay_seconds is None or next_fire_time is None:
            return next_fire_time
        else:
            return next_fire_time + timedelta(seconds=self.delay_seconds)


class TokeoScheduler(MetaMixin):
    """
    Main scheduler class for Tokeo applications.

    This class provides comprehensive task scheduling functionality for Tokeo
    applications, allowing for cron-style scheduled tasks and interactive
    management. It wraps the APScheduler library and extends it with additional
    features specific to the Tokeo framework.

    ### Args

    - **app**: The Tokeo application instance
    - **args**: Variable length argument list passed to parent initializer

    ### Keyword Args

    - **kw**: Arbitrary keyword arguments passed to parent initializer

    ### Notes

    : The scheduler can operate in two modes: background (non-blocking) and
        interactive (with command shell). It provides fine-grained control over
        task execution, including support for jitter, delay, and concurrency
        limits. Tasks can be defined programmatically or via configuration.

    : This constructor only initializes basic attributes. The actual scheduler
        is created on-demand when needed, and tasks are loaded during
        the startup process rather than at initialization time.

    """

    class Meta:
        """Extension meta-data."""

        # Label of this extension
        label = 'tokeo.scheduler'

        # Name of the config section
        config_section = 'scheduler'

        # Defaults of the config section
        config_defaults = dict(
            # Maximum number of concurrent job executions
            max_concurrent_jobs=10,
            # Default timezone for cron expressions (None = use system timezone)
            timezone=None,
            # Task configurations defined in application config
            tasks={},
        )

    def __init__(self, app, *args, **kw):
        super().__init__(*args, **kw)
        #: The Cement application instance
        self.app = app
        self._command_parser = None
        self._scheduler = None
        self._with_shell = False
        self._tasks = None
        self._taskid = 0

    def _setup(self, app):
        """
        Set up the scheduler extension.

        Performs initial setup of the scheduler extension, merging default
        configuration values with any user-provided values.

        ### Args

        - **app**: The Tokeo application instance

        ### Notes

        : This method is called automatically by the Cement framework during
            the application's setup process. It ensures the scheduler's
            configuration section exists with proper default values.

        """
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a simple wrapper around the application's config.get method
        that automatically uses the correct configuration section.

        ### Args

        - **key** (str): Configuration key to retrieve

        ### Keyword Args

        - **kwargs**: Additional arguments passed to config.get(), such as
            default values

        ### Returns

        - **Any**: Configuration value for the specified key

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    @property
    def scheduler(self):
        """
        Get the APScheduler instance.

        Lazily creates and configures the appropriate scheduler instance
        (background or blocking) based on with_shell of startup.

        ### Returns

        - **APScheduler**: The scheduler instance (BackgroundScheduler or
            BlockingScheduler)

        ### Notes

        : This property creates the scheduler on first access, configuring it
            with the appropriate executor and thread pool size. With the shell
            it uses a BackgroundScheduler that doesn't block the main thread.
            Without it uses a BlockingScheduler that keeps the application
            running.

        """
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler() if self._with_shell else BlockingScheduler()
            self._scheduler.add_executor(ThreadPoolExecutor(max_workers=self._config('max_concurrent_jobs', fallback=10)), 'default')
        return self._scheduler

    def process_job(self, job):
        """
        Process a single job by submitting it to its executor.

        Executes a job immediately, regardless of its schedule, by submitting
        it directly to the appropriate executor.

        ### Args

        - **job**: The job to process

        ### Raises

        - **MaxInstancesReachedError**: If the job's maximum number of concurrent
            instances has been reached

        ### Notes

        : This method is primarily used by the manual fire command in the
            interactive shell to trigger a job on demand. It bypasses the normal
            scheduling mechanism but still respects concurrent execution limits.

        """
        try:
            executor = self.scheduler._lookup_executor(job.executor)
        except Exception:
            self.app.log.error(f'Executor lookup "{job.executor}" failed for job "{job}", removing it from the job store')
            job.remove()
            # bail out: without an executor the submit below would hit an
            # unbound name and the real error would be masked by the broad
            # except in the second try
            return

        try:
            executor.submit_job(job, [datetime.now(timezone.utc)])
        except MaxInstancesReachedError:
            self.app.log.warning(f'Execution of job "{job}" skipped: maximum number of running instances reached ({job.max_instances})')
        except Exception:
            self.app.log.error(f'Error submitting job "{job}" to executor "{job.executor}"')

    @property
    def tasks(self):
        """
        Dictionary of configured automation tasks.

        Lazily loads and caches the task configurations from the application
        configuration. Tasks include references to the Python functions
        to execute, the schedule information, and any additional parameters.

        ### Returns

        - **dict**: Dictionary mapping task IDs to task configuration dictionaries

        """
        if self._tasks is None:
            self._tasks = self._config('tasks', fallback={})
        return self._tasks

    def add_crontab_task(
        self,
        module,
        func_name,
        crontab,
        kwargs=None,
        name='',
        coalesce=True,
        misfire_grace_time=None,
        delay_seconds=None,
        max_jitter_seconds=None,
        max_running_jobs=None,
    ):
        """
        Add a new cron-style task to the scheduler.

        Registers a Python function to be executed on a schedule defined by a
        crontab expression. The function is identified by its module and name,
        and can receive parameters via the kwargs dictionary.

        ### Args

        - **module** (str): The module containing the function to run
        - **func_name** (str): The function name to run
        - **crontab** (str): Crontab expression for scheduling
            (format: "min hour day month day_of_week")
        - **kwargs** (dict, optional): Keyword arguments to pass to the
            function. Defaults to an empty dict.
        - **name** (str): Human-readable name for the task, the function
            name when empty
        - **coalesce** (bool): Whether to coalesce missed executions
            (True = run once, False = run all)
        - **misfire_grace_time** (int, optional): Seconds after the scheduled
            time that the job is
            still allowed to be run
        - **delay_seconds** (int, optional): Seconds to delay execution after
            scheduled time
        - **max_jitter_seconds** (int, optional): Maximum jitter in seconds to
            add to the schedule
        - **max_running_jobs** (int, optional): Maximum number of concurrently
            running instances of this job

        ### Notes

        : The function is identified by its fully qualified name in the format
            'module:func_name' and must be importable by the application when the
            task runs. The name is used for display purposes in logs and the
            interactive shell.

        """
        if name == '':
            name = func_name
        self._taskid += 1

        kwargs = {} if kwargs is None else kwargs
        self.scheduler.add_job(
            f'{module}:{func_name}',
            kwargs=kwargs,
            trigger=TokeoCronAndFireTrigger.from_crontab(
                crontab, jitter=max_jitter_seconds, delay_seconds=delay_seconds, timezone=self._config('timezone', fallback=None)
            ),
            name=f'{self._taskid}:{name}',
            id=f'{self._taskid}',
            coalesce=coalesce,
            misfire_grace_time=misfire_grace_time,
            max_instances=1 if max_running_jobs is None else max_running_jobs,
        )

    def init_tasks(self):
        """
        Initialize tasks from configuration.

        Loads task configurations from the application config and schedules
        them with the scheduler. Each task configuration must specify at minimum
        a module and crontab expression.

        ### Raises

        - **TokeoSchedulerError**: If an unsupported coalesce option is used
            or a crontab entry of a task is not valid, naming the task

        ### Notes

        : Each task in the configuration can specify:

            - module: The Python module containing the function to run (required)
            - crontab: Cron expression or list of expressions (required)
            - name: Human-readable name for the task
            - kwargs: Dictionary of keyword arguments to pass to the function
            - coalesce: Missed-execution policy. 'latest' keeps the most
                recent pending run, 'earliest' the oldest, 'all' runs every
                missed run. Note: the current APScheduler exposes only a
                boolean coalesce flag, so 'latest' and 'earliest' behave
                identically (collapse to a single run) for now; they stay
                distinct in anticipation of finer control in future releases.
            - misfire_grace_time: Seconds after scheduled time to still run
            - delay_seconds: Fixed seconds to delay execution
            - max_jitter_seconds: Random seconds to add to timing
            - max_running_jobs: Maximum concurrent instances

          The task ID in the configuration becomes the function name to call.

        """
        # get the entries from config
        for key in self.tasks:
            # get params for task
            task = self.tasks[key]
            # make crontab always as list
            crontab = task['crontab'] if isinstance(task['crontab'], list) else [task['crontab']]
            # get coalesce from string
            coalesce = task['coalesce'] if 'coalesce' in task else 'latest'
            # latest and earliest are kept as distinct config options on
            # purpose, but APScheduler currently only offers a boolean
            # coalesce flag, so both collapse to True for now; the plan is
            # to honour the difference once a future APScheduler supports it
            if coalesce == 'latest':
                coalesce = True
            elif coalesce == 'earliest':
                coalesce = True
            elif coalesce == 'all':
                coalesce = False
            else:
                raise TokeoSchedulerError(f'Unsupported value "{coalesce}" for coalesce option')
            # iterate crontab
            for entry in crontab:
                # a wrong crontab entry raises a ValueError of the trigger
                try:
                    self.add_crontab_task(
                        task['module'],
                        key,
                        entry,
                        kwargs=task['kwargs'] if 'kwargs' in task else {},
                        name=task['name'] if 'name' in task and task['name'] != '' else key,
                        coalesce=coalesce,
                        misfire_grace_time=task['misfire_grace_time'] if 'misfire_grace_time' in task else None,
                        delay_seconds=task['delay_seconds'] if 'delay_seconds' in task else None,
                        max_jitter_seconds=task['max_jitter_seconds'] if 'max_jitter_seconds' in task else None,
                        max_running_jobs=task['max_running_jobs'] if 'max_running_jobs' in task else None,
                    )
                except ValueError as err:
                    raise TokeoSchedulerError(f'Task "{key}" with crontab "{entry}": {err}') from err

    def startup(self, with_shell=False, paused=False):
        """
        Start the scheduler.

        Initializes the scheduler, loads tasks from configuration, and starts
        the scheduler in either running or paused state. This is the primary
        method for starting the scheduler without the interactive shell.

        ### Args

        - **with_shell** (bool): Whether to run in the background for the
            interactive shell
        - **paused** (bool): Whether to start in paused state

        ### Notes

        : With the shell, the scheduler runs in the background and doesn't
            block the application's main thread. Without it uses a blocking
            scheduler that keeps the application running until shutdown.

        : Starting with paused=True allows you to load all tasks but defer their
            execution until explicitly resumed.

        """
        self._with_shell = with_shell
        self.app.log.info('Adding all scheduler tasks from config')
        self.init_tasks()
        if paused:
            self.app.log.warning('Initialize scheduler in paused mode')
        else:
            self.app.log.info('Spinning up scheduler')
        self.scheduler.start(paused=paused)

    def shutdown(self, signum=None, frame=None):
        """
        Shutdown the scheduler.

        Stops the scheduler and removes all jobs. This should be called
        when the application is shutting down to ensure proper cleanup. A
        scheduler that is not running, e.g. after ```init_tasks``` failed, is
        left as it is.

        ### Args

        - **signum** (int, optional): Signal number (used for signal handlers)
        - **frame** (frame, optional): Current stack frame (used for signal
            handlers)

        """
        # only shutdown if started, e.g. not after init_tasks failed, the
        # scheduler of apscheduler raises on a shutdown when not running
        if self._scheduler is not None and self._scheduler.running:
            self.app.log.info('Shutdown scheduler')
            self._scheduler.shutdown()
            self._scheduler.remove_all_jobs()

    def launch(self, with_shell=False, paused=False):
        """
        Launch the scheduler and optionally start the interactive shell.

        Convenience method that starts the scheduler and then optionally
        launches the interactive shell for managing tasks.

        ### Args

        - **with_shell** (bool): Whether to run the interactive shell
        - **paused** (bool): Whether to start in paused state

        ### Notes

        : Both modes block: with the shell until the shell ends, without
            until the scheduler is stopped.

        """
        self.app.scheduler.startup(with_shell=with_shell, paused=paused)
        if with_shell:
            self.run_shell()

    def shell_completion(self):
        """
        Get the completion of the shell commands.

        ### Returns

        - **dict**: The commands and their subcommands as nested dict

        """
        return {
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
        }

    def shell_history(self):
        """
        Get the lines the shell history starts with.

        ### Returns

        - **list**: The lines, ```exit``` to find it by arrow up

        """
        return ['exit']

    def handle_command_list(self, args):
        """
        Handle the 'list' command to show the scheduled tasks.

        Shows the time and the state of the scheduler, then a table of the
        tasks with id, name, trigger, next run and state.

        ### Args

        - **args**: Command arguments from the parser

        """
        output = self.app.tui.output
        state = 'running' if self._scheduler.state == STATE_RUNNING else 'not running'
        header = f'{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %z (%Z)")}  scheduler {state}'
        rows = [
            [
                job.id,
                job.name,
                str(job.trigger),
                job.next_run_time.strftime('%Y-%m-%d %H:%M:%S %z') if job.next_run_time else '',
                'active' if job.next_run_time else 'paused',
            ]
            for job in self._scheduler.get_jobs()
        ]
        columns = [('id', 'left', 'full'), 'name', 'trigger', 'next run', 'state']
        output.print(header, output.build_table(columns, rows, title='tasks'))

    def handle_command_pause(self, args):
        """
        Handle the 'pause' command to pause the scheduler.

        Pauses the scheduler, preventing all scheduled tasks from running
        until the scheduler is resumed.

        ### Args

        - **args**: Command arguments from the parser

        ### Notes

        : When paused, the scheduler will not execute any jobs, but will keep
            track of the scheduled times. When resumed, the scheduler will decide
            which jobs to execute based on the misfire policy.

        """
        self._scheduler.pause()

    def handle_command_resume(self, args):
        """
        Handle the 'resume' command to resume the scheduler.

        Resumes the scheduler after it has been paused, allowing scheduled
        tasks to execute again.

        ### Args

        - **args**: Command arguments from the parser

        ### Notes

        : When the scheduler is resumed, it will determine which jobs should
            be executed based on their scheduled times and misfire policies.
            Depending on the configuration, it may immediately execute jobs that
            were scheduled to run while the scheduler was paused.

        """
        self._scheduler.resume()

    def handle_command_reload(self, args):
        """
        Handle the 'reload' command to reload tasks from configuration.

        Removes all existing tasks and reloads them from the application
        configuration. This is useful when the configuration has changed
        and you want to update the scheduler without restarting the application.

        ### Args

        - **args**: Command arguments from the parser with optional restart flag

        ### Notes

        : This command first pauses the scheduler (if it's running), then removes
            all jobs, reloads the task configuration, and finally resumes the
            scheduler if it was previously running or if the restart flag is set.

        : The configuration itself is not reloaded from disk; only the tasks
            defined in the current application configuration are reloaded.

        """
        # save running state
        running = self._scheduler.state == STATE_RUNNING
        # pause if running to prevent events while updating tasks
        if running:
            self._scheduler.pause()
        # drop the job queue
        self._scheduler.remove_all_jobs()
        # the tasks keep their ids, pause 1 still means the first task
        self._taskid = 0
        # fill in from config again (the in-memory config dict; the file
        # on disk is not reread - the docstring documents this)
        self.init_tasks()
        # set scheduler to running if was running or forced
        if running or args.restart:
            self._scheduler.resume()

    def handle_command_restart(self, args):
        """
        Handle the 'restart' command to restart the scheduler.

        Convenience command that reloads tasks from configuration and
        ensures the scheduler is running after the reload.

        ### Args

        - **args**: Command arguments from the parser

        ### Notes

        : This is equivalent to calling ```reload --restart``` and is provided
            as a convenience command for the common case of wanting to reload
            tasks and ensure the scheduler is running afterward.

        """
        args.restart = True
        self.handle_command_reload(args)

    def handle_command_wakeup(self, args):
        """
        Handle the 'wakeup' command to wake up the scheduler.

        Forces the scheduler to wake up and check for due jobs immediately,
        rather than waiting for the next scheduled wakeup time.

        ### Args

        - **args**: Command arguments from the parser

        ### Notes

        : This command is useful when you've added jobs while the scheduler
            is running and want to ensure they're checked immediately, rather than
            waiting for the scheduler's next normal wakeup interval.

        """
        self._scheduler.wakeup()

    def handle_task_subcommands(self, args):
        """
        Handle task-specific commands (pause, resume, remove, fire).

        Executes commands that operate on specific tasks, identified by their IDs.
        Multiple task IDs can be specified to apply the same command to multiple
        tasks.

        ### Args

        - **args**: Command arguments from the parser with subcommand and task
            attributes

        ### Raises

        - **ValueError**: If a specified task ID is not found

        ### Notes

        : This method handles several task-specific commands:

            - **pause**: Pauses specific tasks without affecting the scheduler
                as a whole
            - **resume**: Resumes specific tasks that have been paused
            - **remove**: Permanently removes tasks from the scheduler
            - **fire**: Executes tasks immediately, regardless of their schedule

        : Each command operates on one or more tasks specified by their IDs.

        """
        # the action of the subcommand, e.g. pause for tasks.pause
        action = args.subcommand.removeprefix('tasks.')
        for task in args.task:
            try:
                job = self._scheduler.get_job(task)
                if job:
                    if args.subcommand == 'tasks.remove':
                        self._scheduler.remove_job(task)
                    elif args.subcommand == 'tasks.pause':
                        self._scheduler.pause_job(task)
                    elif args.subcommand == 'tasks.resume':
                        self._scheduler.resume_job(task)
                    elif args.subcommand == 'tasks.fire':
                        self.process_job(job)
                    # a short note
                    self.app.log.info(f'{action}d job {job.id} [{job.name}]')
                else:
                    raise ValueError(f'job {task} not found!')
            except Exception as err:
                self.app.log.error(err)

    def handle_subcommand_help(self, args):
        """
        Handle help requests for subcommands.

        Displays help information for subcommands when invoked with no
        specific action.

        ### Args

        - **args**: Command arguments with print_help method

        ### Notes

        : This method is called when a subcommand is used without specifying
            an action (e.g., 'tasks' without 'pause', 'resume', etc.). It displays
            help information showing the available actions for that subcommand.

        """
        args.print_help()

    @property
    def command_parser(self):
        """
        Command parser for the interactive shell.

        Creates and returns an argument parser for the interactive shell commands.
        The parser is built once and cached for future use.

        ### Returns

        - **ArgumentParser**: Command parser instance for the interactive shell

        ### Notes

        : The parser defines all available commands and their arguments for the
            interactive shell. It supports commands for managing the scheduler as
            a whole and commands for operating on individual tasks.

        : Available commands include:

            - ```list```: Show active scheduler tasks
            - ```pause```/```resume```: Control overall scheduler state
            - ```reload```: Reload tasks from configuration
            - ```restart```: Reload and restart the scheduler
            - ```wakeup```: Force the scheduler to check for due jobs
            - ```tasks```: Commands for managing individual tasks:
                - ```pause```/```resume```: Control specific task state
                - ```remove```: Delete a task
                - ```fire```: Execute a task immediately

        """
        if self._command_parser is None:
            # if not created, generate the nested command parser
            self._command_parser = ArgumentParser(
                prog='',
                description='control the task scheduler',
                epilog='',
            )

            # prepare for sub-commands
            sub = self._command_parser.add_subparsers(metavar='')
            # scheduler list command
            command = sub.add_parser('list', help='show active scheduler tasks')
            command.set_defaults(func=self.handle_command_list)
            # scheduler pause command
            command = sub.add_parser('pause', help='pause the scheduler')
            command.set_defaults(func=self.handle_command_pause)
            # scheduler resume command
            command = sub.add_parser('resume', help='resume the scheduler')
            command.set_defaults(func=self.handle_command_resume)
            # scheduler reload command
            command = sub.add_parser('reload', help='reload the scheduling tasks from config')
            command.add_argument('--restart', action='store_true', help='start the scheduler after reload if paused')
            command.set_defaults(func=self.handle_command_reload)
            # scheduler restart command
            command = sub.add_parser('restart', help='reload and restart the scheduler from config')
            command.set_defaults(func=self.handle_command_restart)
            # scheduler wakeup command
            command = sub.add_parser('wakeup', help='notify scheduler to trigger _process_jobs')
            command.set_defaults(func=self.handle_command_wakeup)

            # tasks with its remove, pause, resume and fire commands
            group_parser = sub.add_parser('tasks', help='tasks manipulation')
            group_parser.set_defaults(func=self.handle_subcommand_help, print_help=group_parser.print_help)
            group_sub = group_parser.add_subparsers(metavar='')
            for name, verb in (('remove', 'drop'), ('pause', 'pause'), ('resume', 'resume'), ('fire', 'fire')):
                command = group_sub.add_parser(name, help=f'{name} the task')
                command.add_argument('task', nargs='+', help=f'id of tasks to {verb}')
                command.set_defaults(func=self.handle_task_subcommands, subcommand=f'tasks.{name}')

        # return initialized parser
        return self._command_parser

    def run_command(self, cmd=''):
        """
        Process a command string from the interactive shell.

        Parses and executes a command entered by the user in the interactive
        shell, an empty line follows each command.

        ### Args

        - **cmd** (str): Command string to process

        ### Returns

        - **bool**: True if the command was successful or showed help,
          False otherwise

        ### Raises

        - **EOFError**: If the exit or quit command is entered
        - **Exception**: The error of a failing command

        ### Notes

        : This method is the central command processor for the interactive shell.
            It parses the command string, executes the appropriate handler for the
            command, and returns a success indicator. The return value is used to
            determine whether to add the command to the history and whether to clear
            the input field for the next command.

        : Special commands:

            - exit, quit: Raise EOFError to terminate the shell
            - Empty commands or commands that show help: Return True
            - Commands that fail to parse: Return False
            - Commands that fail to execute: Raise their error

        """
        # an empty line between two commands
        try:
            # signal bye bye to interactive shell
            if cmd in ['exit', 'quit']:
                raise EOFError('Command exit entered.')

            # check command
            cmd_args = shlex.split(cmd)
            try:
                args = self.command_parser.parse_args(args=cmd_args)
            except SystemExit as err:
                # if parse was ok but only help, err == 0, else err != 0
                return err.code == 0

            # a failing command raises, the shell shows the error and keeps the line
            if 'func' in args:
                print('')
                args.func(args)
                return True

            # unusual empty but valid command, can be added to history
            return True
        finally:
            print('')

    def run_shell(self, **session_options):
        """
        Start the interactive shell for scheduler management.

        The prompt shows whether the scheduler is running. ```exit```,
        ```quit``` or Ctrl-D end the shell.

        ### Args

        - **session_options**: More options for the shell of ```app.tui.input```

        ### Example

        ```
        Scheduler> list
        [shows all scheduled tasks]

        Scheduler> tasks pause 1
        [pauses task with ID 1]

        Scheduler> reload --restart
        [reloads tasks and ensures scheduler is running]

        Scheduler> exit
        [exits the shell]
        ```

        """

        def get_prompt():
            return 'Scheduler> ' if self._scheduler.state == STATE_RUNNING else '(not running) Scheduler> '

        self.app.log.info('Welcome to scheduler interactive shell.')
        self.app.tui.input.run_shell(
            self.run_command,
            prompt=get_prompt,
            completer=self.shell_completion(),
            history=self.shell_history(),
            **session_options,
        )
        self.app.log.info('bye bye using scheduler...')


class TokeoSchedulerController(Controller):
    """
    Command-line controller for scheduler functionality.

    This controller provides CLI commands for starting and managing
    the scheduler. It integrates with the Cement framework's command-line
    interface to expose scheduler commands to the application's CLI.

    ### Example

    ```bash
    # Start the scheduler, it runs until stopped
    myapp scheduler launch

    # Start the scheduler with the interactive shell
    myapp scheduler launch --shell

    # Start in paused mode
    myapp scheduler launch --shell --paused
    ```

    ### Notes

    : The controller is registered with the Cement framework and adds a
        'scheduler' command to the application's command-line interface.
        It handles command-line arguments for controlling the scheduler
        behavior and appearance.

    : This controller works in conjunction with the TokeoScheduler class,
        providing a CLI wrapper around its functionality.

    """

    class Meta:
        """Controller meta-data."""

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
        epilog = f'Example: {basename(argv[0])} scheduler launch --shell'

    def _setup(self, app):
        """
        Set up the controller.

        Initializes the controller and prepares it for use with the
        Cement framework.

        ### Args

        - **app**: The Tokeo application instance

        ### Notes

        : This method is called automatically by the Cement framework during
            controller registration. It performs any necessary initialization
            for the controller.

        """
        super()._setup(app)

    @ex(
        help='start the scheduler service',
        description='Spin up the scheduler.',
        arguments=[
            (
                ['--shell'],
                dict(
                    action='store_true',
                    help='start with the interactive shell',
                ),
            ),
            (
                ['--paused'],
                dict(
                    action='store_true',
                    help='start the scheduler in paused mode',
                ),
            ),
        ],
    )
    def launch(self):
        """
        Start the scheduler service.

        Without --shell the scheduler runs in the background until it is
        stopped, the default; with --shell the interactive shell runs in the
        foreground.

        """
        self.app.scheduler.launch(with_shell=self.app.pargs.shell, paused=self.app.pargs.paused)


def tokeo_scheduler_extend_app(app):
    """
    Extend the application with scheduler functionality.

    This function adds the scheduler extension to the application and
    initializes it, making it available as app.scheduler throughout
    the application.

    ### Args

    - **app**: The Tokeo application instance

    ### Notes

    : This function is called automatically during the application's post_setup
        phase. It creates an instance of TokeoScheduler, configures it, and
        attaches it to the application as the 'scheduler' attribute.

    """
    check_required_extensions(app, 'tokeo.ext.scheduler', 'tokeo.ext.tui.input', 'tokeo.ext.tui.output')
    app.extend('scheduler', TokeoScheduler(app))
    app.scheduler._setup(app)


def tokeo_scheduler_shutdown(app):
    """
    Handle application shutdown for scheduler.

    Properly cleans up scheduler resources when the application is shutting down.
    This function is registered as a pre_close hook to ensure proper cleanup.

    ### Args

    - **app**: The Tokeo application instance

    ### Notes

    : This function is called automatically during the application's pre_close
        phase. It ensures the scheduler is properly shut down, stopping all
        running jobs and releasing resources before the application exits.

    """
    app.scheduler.shutdown()


def load(app):
    """
    Load the scheduler extension into a Tokeo application.

    This function registers the controller and hooks needed for scheduler
    integration with the Tokeo application framework. It's called automatically
    when the extension is loaded.

    ### Args

    - **app**: The Tokeo application instance

    ### Example

    ```python
    # In your application configuration:
    class MyApp(App):
        class Meta:
            extensions = [
                'tokeo.ext.tui.input',
                'tokeo.ext.tui.output',
                'tokeo.ext.scheduler',
                # other extensions...
            ]
    ```

    ### Notes

    : This function performs three key actions:

        - Registers the TokeoSchedulerController for CLI integration
        - Registers a post_setup hook to initialize the scheduler
        - Registers a pre_close hook for proper cleanup

    : After loading this extension, the scheduler is available as
        app.scheduler and the 'scheduler' CLI command is added to the
        application.

    """
    app.handler.register(TokeoSchedulerController)
    app.hook.register('post_setup', tokeo_scheduler_extend_app)
    app.hook.register('pre_close', tokeo_scheduler_shutdown)
