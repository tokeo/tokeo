"""
Tokeo Scheduler Extension Module.

This extension provides task scheduling capabilities for Tokeo applications.
It integrates the APScheduler library to enable cron-style scheduled tasks
and provides an interactive shell for managing the scheduler at runtime.

The extension orchestrates the execution of background tasks with precise
timing controls and offers both programmatic and interactive management
of the task scheduler.

### Features:

1. Cron-style scheduling with optional jitter and delay
1. Task coalescing (latest, earliest, or all)
1. Interactive command shell for task management
1. Background or blocking scheduler modes
1. Configuration-driven task setup
1. Runtime task manipulation (pause, resume, remove, fire)
1. Declarative task definition via application configuration
1. Control over maximum concurrent job execution

"""

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
    """
    Enhanced CronTrigger that allows manually to fire a timer.
    supports an additional delay after trigger time.

    This trigger extends the standard APScheduler CronTrigger with the ability
    to add a configurable delay after the scheduled time. This can be useful
    for staggering task execution or preventing resource contention and allows
    to manually fire a timer.

    ### Notes:

    : The delay parameter adds a fixed delay after the cron-calculated trigger time,
        while jitter adds a random amount of time (up to the specified maximum).
        Used together, they provide flexible control over task execution timing.

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
        delay=None,
    ):
        """
        Initialize the trigger with cron parameters and optional delay.

        Creates a new trigger with the specified cron parameters and optional
        delay configuration.

        ### Args:

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
        - **delay** (int, optional): Additional seconds to delay after
            the scheduled time

        ### Notes:

        : The cron parameters (year, month, etc.) support various formats including:

            1. Single values: '5', 5
            1. Ranges: '2-9', '0-23'
            1. Multiple values: '1,3,5'
            1. Step values: '*/2' (every 2 units)
            1. Names for day_of_week: 'mon,wed,fri'

        """
        super().__init__(year, month, day, week, day_of_week, hour, minute, second, start_date, end_date, timezone, jitter)
        self.delay = delay

    @classmethod
    def from_crontab(cls, expr, timezone=None, jitter=None, delay=None):
        """
        Create a trigger from a standard crontab expression.

        Parses a crontab-format string and creates a trigger based on the
        expression. The standard crontab format uses five space-separated
        fields for minute, hour, day of month, month, and day of week.

        ### Args:

        - **expr** (str): A crontab expression (5 fields: minute, hour,
            day of month, month, day of week)
        - **timezone** (datetime.tzinfo|str, optional): Timezone to use for
            date/time calculations
        - **jitter** (int, optional): Random seconds to add or subtract
            from trigger time
        - **delay** (int, optional): Fixed seconds to add after calculated
            trigger time

        ### Returns:

        - **TokeoCronAndFireTrigger**: A new trigger instance configured with
            the expression

        ### Raises:

        - **ValueError**: If the expression has the wrong number of fields

        """
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
        """
        Calculate the next time this trigger should fire.

        Extends the parent class's calculation by adding the configured delay
        to the next fire time determined by the cron expression.

        ### Args:

        - **previous_fire_time** (datetime, optional): Previous firing time
            of the job or None
        - **now** (datetime): Current time (UTC)

        ### Returns:

        - **datetime**: The next fire time or None if no future fire times
            are available

        ### Notes:

        : This method first determines the next fire time using the standard cron
            calculation, then adds the configured delay (if any) to the result.
            The jitter is applied by the parent class and affects the time before
            the delay is added.

        """
        # check cron based trigger
        next_fire_time = super().get_next_fire_time(previous_fire_time, now)
        # if there is an additional delay, put it on top
        if self.delay is None:
            return next_fire_time
        else:
            return next_fire_time + timedelta(seconds=self.delay)


class TokeoScheduler(MetaMixin):
    """
    Main scheduler class for Tokeo applications.

    This class provides comprehensive task scheduling functionality for Tokeo
    applications, allowing for cron-style scheduled tasks and interactive
    management. It wraps the APScheduler library and extends it with additional
    features specific to the Tokeo framework.

    ### Methods:

    - **startup**: Initialize and start the scheduler
    - **shutdown**: Stop the scheduler and clean up resources
    - **launch**: Start the scheduler and optionally the interactive shell
    - **shell**: Launch the interactive command shell
    - **add_crontab_task**: Add a new cron-style task to the scheduler
    - **init_tasks**: Initialize tasks from configuration

    ### Notes:

    : The scheduler can operate in two modes: background (non-blocking) and
        interactive (with command shell). It provides fine-grained control over
        task execution, including support for jitter, delay, and concurrency
        limits. Tasks can be defined programmatically or via configuration.

    """

    class Meta:
        """
        Extension meta-data and configuration.

        ### Notes:

        : This class defines the configuration section, default values, and
            unique identifier required by the Cement framework for proper handler
            registration and operation.

        """

        # Unique identifier for this handler
        label = 'tokeo.scheduler'

        # Configuration section in the application config
        config_section = 'scheduler'

        # Default configuration settings
        config_defaults = dict(
            # Maximum number of concurrent job executions
            max_concurrent_jobs=10,
            # Default timezone for cron expressions (None = use system timezone)
            timezone=None,
            # Task configurations defined in application config
            tasks={},
        )

    def __init__(self, app, *args, **kw):
        """
        Initialize the scheduler.

        Creates a new scheduler instance with the specified application context.
        The actual scheduler initialization is deferred until startup is called.

        ### Args:

        - **app**: The Tokeo application instance
        - ***args**: Variable length argument list passed to parent initializer
        - ****kw**: Arbitrary keyword arguments passed to parent initializer

        ### Notes:

        : This constructor only initializes basic attributes. The actual scheduler
            is created on-demand when needed, and tasks are loaded during the startup
            process rather than at initialization time.

        """
        super(TokeoScheduler, self).__init__(*args, **kw)
        self.app = app
        self._command_parser = None
        self._shell_completion = None
        self._scheduler = None
        self._interactive = True
        self._tasks = None
        self._taskid = 0

    def _setup(self, app):
        """
        Set up the scheduler extension.

        Performs initial setup of the scheduler extension, merging default
        configuration values with any user-provided values.

        ### Args:

        - **app**: The Tokeo application instance

        ### Notes:

        : This method is called automatically by the Cement framework during
            the application's setup process. It ensures the scheduler's configuration
            section exists with proper default values.

        """
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a simple wrapper around the application's config.get method
        that automatically uses the correct configuration section.

        ### Args:

        - **key** (str): Configuration key to retrieve
        - **kwargs**: Additional arguments passed to config.get(), such as
            default values

        ### Returns:

        - Configuration value for the specified key

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    @property
    def scheduler(self):
        """
        Get the APScheduler instance.

        Lazily creates and configures the appropriate scheduler instance
        (background or blocking) based on the interactive mode setting.

        ### Returns:

        - **APScheduler**: The scheduler instance (BackgroundScheduler or
            BlockingScheduler)

        ### Notes:

        : This property creates the scheduler on first access, configuring it
            with the appropriate executor and thread pool size. In interactive
            mode, it uses a BackgroundScheduler that doesn't block the main
            thread. In non-interactive mode, it uses a BlockingScheduler that
            keeps the application running.

        """
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler() if self._interactive else BlockingScheduler()
            self._scheduler.add_executor(ThreadPoolExecutor(max_workers=self._config('max_concurrent_jobs', fallback=10)), 'default')
        return self._scheduler

    def process_job(self, job):
        """
        Process a single job by submitting it to its executor.

        Executes a job immediately, regardless of its schedule, by submitting
        it directly to the appropriate executor.

        ### Args:

        - **job**: The job to process

        ### Notes:

        : This method is primarily used by the manual fire command in the
            interactive shell to trigger a job on demand. It bypasses the normal
            scheduling mechanism but still respects concurrent execution limits.

        ### Raises:

        - **MaxInstancesReachedError**: If the job's maximum number of concurrent
            instances has been reached

        """
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
        """
        Dictionary of configured automation tasks.

        Lazily loads and caches the task configurations from the application
        configuration. Tasks include references to the Python functions
        to execute, the schedule information, and any additional parameters.

        ### Returns:

        - **dict**: Dictionary mapping task IDs to task configuration dictionaries

        """
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
        """
        Add a new cron-style task to the scheduler.

        Registers a Python function to be executed on a schedule defined by a
        crontab expression. The function is identified by its module and name,
        and can receive parameters via the kwargs dictionary.

        ### Args:

        - **module** (str): The module containing the function to run
        - **func** (str): The function name to run
        - **crontab** (str): Crontab expression for scheduling
            (format: "min hour day month day_of_week")
        - **coalesce** (bool): Whether to coalesce missed executions
            (True = run once, False = run all)
        - **misfire_grace_time** (int, optional): Seconds after the scheduled
            time that the job is
            still allowed to be run
        - **delay** (int, optional): Seconds to delay execution after
            scheduled time
        - **max_jitter** (int, optional): Maximum jitter in seconds to add to
            the schedule
        - **max_running_jobs** (int, optional): Maximum number of concurrently
            running instances of this job
        - **kwargs** (dict): Keyword arguments to pass to the function
        - **title** (str): Human-readable title for the task

        ### Notes:

        : The function is identified by its fully qualified name in the format
            'module:func' and must be importable by the application when the task
            runs. The title is used for display purposes in logs and the interactive
            shell.

        """
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
        """
        Initialize tasks from configuration.

        Loads task configurations from the application config and schedules
        them with the scheduler. Each task configuration must specify at minimum
        a module and crontab expression.

        ### Raises:

        - **ValueError**: If an unsupported coalesce setting is used

        ### Notes:

        : Each task in the configuration can specify:

            - module: The Python module containing the function to run (required)
            - crontab: Cron expression or list of expressions (required)
            - name: Human-readable name for the task
            - kwargs: Dictionary of keyword arguments to pass to the function
            - coalesce: How to handle missed executions ('latest', 'earliest',
                'all')
            - misfire_grace_time: Seconds after scheduled time to still run
            - delay: Fixed seconds to delay execution
            - max_jitter: Random seconds to add to timing
            - max_running_jobs: Maximum concurrent instances

          The task ID in the configuration becomes the function name to call.

        """
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
        """
        Start the scheduler.

        Initializes the scheduler, loads tasks from configuration, and starts
        the scheduler in either running or paused state. This is the primary
        method for starting the scheduler without the interactive shell.

        ### Args:

        - **interactive** (bool): Whether to run in interactive mode
        - **paused** (bool): Whether to start in paused state

        ### Notes:

        : In interactive mode, the scheduler runs in the background and doesn't
            block the application's main thread. In non-interactive mode, it uses
            a blocking scheduler that keeps the application running until shutdown.

        : Starting with paused=True allows you to load all tasks but defer their
            execution until explicitly resumed.

        """
        self._interactive = interactive
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
        when the application is shutting down to ensure proper cleanup.

        ### Args:

        - **signum** (int, optional): Signal number (used for signal handlers)
        - **frame** (frame, optional): Current stack frame (used for signal
            handlers)

        """
        # only shutdown if initialized
        if self._scheduler is not None:
            self.app.log.info('Shutdown scheduler')
            self._scheduler.shutdown()
            self._scheduler.remove_all_jobs()

    def launch(self, interactive=True, paused=False):
        """
        Launch the scheduler and optionally start the interactive shell.

        Convenience method that starts the scheduler and then optionally
        launches the interactive shell for managing tasks.

        ### Args:

        - **interactive** (bool): Whether to run in interactive mode with
            command shell
        - **paused** (bool): Whether to start in paused state

        ### Notes:

        : This method combines scheduler startup and interactive shell launch.
            When interactive is True, it will block until the shell is exited.
            When interactive is False, it will return immediately after starting
            the scheduler.

        """
        self.app.scheduler.startup(interactive=interactive, paused=paused)
        if interactive:
            self.shell()

    def shell_completion(self):
        """
        Get shell command completions for the interactive shell.

        Creates and returns command completion definitions for the prompt_toolkit
        interactive shell. This enables tab-completion of commands and subcommands.

        ### Returns:

        - **NestedCompleter**: Command completer instance for prompt_toolkit

        ### Notes:

        : The completer is built once and cached for future use. It defines
            completions for all available shell commands and their subcommands.

        """
        if self._shell_completion is None:
            self._shell_completion = NestedCompleter.from_nested_dict(
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
        # return the completion set
        return self._shell_completion

    def shell_history(self):
        """
        Get command history for the interactive shell.

        Creates an in-memory command history for the interactive shell with
        some predefined common commands.

        ### Returns:

        - **InMemoryHistory**: Command history instance for prompt_toolkit

        ### Notes:

        : The history is initialized with common commands like 'exit' to
            provide convenient history recall from the start. As the user
            enters more commands, they are added to this history.

        """
        return InMemoryHistory(
            [
                'exit',
            ]
        )

    def handle_command_list(self, args):
        """
        Handle the 'list' command to show scheduled tasks.

        Displays all currently scheduled tasks including their next run time,
        trigger information, and status.

        ### Args:

        - **args**: Command arguments from the parser

        ### Notes:

        : This command prints the current time followed by a detailed list of all
            scheduled jobs with their IDs, names, next run times, and status.

        """
        self.app.log.debug(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %z (%Z)'))
        self._scheduler.print_jobs()

    def handle_command_pause(self, args):
        """
        Handle the 'pause' command to pause the scheduler.

        Pauses the scheduler, preventing all scheduled tasks from running
        until the scheduler is resumed.

        ### Args:

        - **args**: Command arguments from the parser

        ### Notes:

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

        ### Args:

        - **args**: Command arguments from the parser

        ### Notes:

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

        ### Args:

        - **args**: Command arguments from the parser with optional restart flag

        ### Notes:

        : This command first pauses the scheduler (if it's running), then removes
            all jobs, reloads the task configuration, and finally resumes the
            scheduler if it was previously running or if the restart flag is set.

        : The configuration itself is not reloaded from disk; only the tasks
            defined in the current application configuration are reloaded.

        """
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
        """
        Handle the 'restart' command to restart the scheduler.

        Convenience command that reloads tasks from configuration and
        ensures the scheduler is running after the reload.

        ### Args:

        - **args**: Command arguments from the parser

        ### Notes:

        : This is equivalent to calling `reload --restart` and is provided
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

        ### Args:

        - **args**: Command arguments from the parser

        ### Notes:

        : This command is useful when you've added jobs while the scheduler
            is running and want to ensure they're checked immediately, rather than
            waiting for the scheduler's next normal wakeup interval.

        """
        self._scheduler.wakeup()

    def handle_command_task_commands(self, args):
        """
        Handle task-specific commands (pause, resume, remove, fire).

        Executes commands that operate on specific tasks, identified by their IDs.
        Multiple task IDs can be specified to apply the same command to multiple
        tasks.

        ### Args:

        - **args**: Command arguments from the parser with cmd and task attributes

        ### Notes:

        : This method handles several task-specific commands:

            1. **pause**: Pauses specific tasks without affecting the scheduler
                as a whole
            1. **resume**: Resumes specific tasks that have been paused
            1. **remove**: Permanently removes tasks from the scheduler
            1. **fire**: Executes tasks immediately, regardless of their schedule

        : Each command operates on one or more tasks specified by their IDs.

        ### Raises:

        - **ValueError**: If a specified task ID is not found

        """
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
        """
        Handle help requests for subcommands.

        Displays help information for subcommands when invoked with no
        specific action.

        ### Args:

        - **args**: Command arguments with print_help method

        ### Notes:

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

        ### Returns:

        - **ArgumentParser**: Command parser instance for the interactive shell

        ### Notes:

        : The parser defines all available commands and their arguments for the
            interactive shell. It supports commands for managing the scheduler as
            a whole and commands for operating on individual tasks.

        : Available commands include:

            1. **list**: Show active scheduler tasks
            1. **pause**/**resume**: Control overall scheduler state
            1. **reload**: Reload tasks from configuration
            1. **restart**: Reload and restart the scheduler
            1. **wakeup**: Force the scheduler to check for due jobs
            1. **tasks**: Commands for managing individual tasks:
                1. **pause**/**resume**: Control specific task state
                1. **remove**: Delete a task
                1. **fire**: Execute a task immediately

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
        """
        Process a command string from the interactive shell.

        Parses and executes a command entered by the user in the interactive shell.

        ### Args:

        - **cmd** (str): Command string to process

        ### Returns:

        - **bool**: True if the command was successful or showed help,
          False otherwise

        ### Raises:

        - **EOFError**: If the exit or quit command is entered

        ### Notes:

        : This method is the central command processor for the interactive shell.
            It parses the command string, executes the appropriate handler for the
            command, and returns a success indicator. The return value is used to
            determine whether to add the command to the history and whether to clear
            the input field for the next command.

        : Special commands:

            1. exit, quit: Raise EOFError to terminate the shell
            1. Empty commands or commands that show help: Return True
            1. Commands that fail to parse or execute: Return False

        """
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
        """
        Start the interactive shell for scheduler management.

        Provides an interactive command prompt for managing the scheduler and its
        tasks. The shell supports command history, tab completion, and showing the
        scheduler's current state in the prompt.

        ### Notes:

        : The interactive shell provides a command-line interface for managing
            the scheduler at runtime. It displays the scheduler's current state
            (running or paused) in the prompt and supports various commands for
            listing, pausing, resuming, and manipulating tasks.

        : The shell captures Ctrl+D to exit cleanly and handles various error
            conditions gracefully. Command history is maintained between commands
            and can be navigated with up/down arrows.

        : This method blocks until the shell is exited.

        ### Example:

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
    """
    Command-line controller for scheduler functionality.

    This controller provides CLI commands for starting and managing
    the scheduler. It integrates with the Cement framework's command-line
    interface to expose scheduler commands to the application's CLI.

    ### Methods:

    - **launch**: Start the scheduler service with optional interactive shell
    - Several log_* methods for output formatting

    ### Notes:

    : The controller is registered with the Cement framework and adds a
        'scheduler' command to the application's command-line interface.
        It handles command-line arguments for controlling the scheduler
        behavior and appearance.

    : This controller works in conjunction with the TokeoScheduler class,
        providing a CLI wrapper around its functionality.

    ### Example:

    ```bash
    # Start the scheduler with interactive shell
    myapp scheduler launch

    # Start the scheduler in the background
    myapp scheduler launch --background

    # Start in paused mode
    myapp scheduler launch --paused

    # Start with plain text output (no colors)
    myapp scheduler launch --no-colors
    ```

    """

    class Meta:
        """
        Controller meta-data configuration.

        ### Notes:

        : This class defines the metadata required by the Cement framework for
            proper controller registration and CLI integration. It specifies how
            the controller is displayed in help text and how it relates to other
            controllers in the application.

        """

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
        """
        Set up the controller.

        Initializes the controller and prepares it for use with the
        Cement framework.

        ### Args:

        - **app**: The Tokeo application instance

        ### Notes:

        : This method is called automatically by the Cement framework during
            controller registration. It performs any necessary initialization
            for the controller.

        """
        super(TokeoSchedulerController, self)._setup(app)

    def log_info_bw(self, *args):
        """
        Log info message in black and white.

        ### Args:

        - ***args**: Message parts to log

        ### Notes:

        : This is a plain text alternative to the colored logging methods.
            It's used when the --no-colors flag is specified.

        """
        print('INFO:', *args)

    def log_warning_bw(self, *args):
        """
        Log warning message in black and white.

        ### Args:

        - ***args**: Message parts to log

        ### Notes:

        : This is a plain text alternative to the colored logging methods.
            It's used when the --no-colors flag is specified.

        """
        print('WARN:', *args)

    def log_error_bw(self, *args):
        """
        Log error message in black and white.

        ### Args:

        - ***args**: Message parts to log

        ### Notes:

        : This is a plain text alternative to the colored logging methods.
            It's used when the --no-colors flag is specified.

        """
        print('ERR:', *args)

    def log_debug_bw(self, *args):
        """
        Log debug message in black and white.

        ### Args:

        - ***args**: Message parts to log

        ### Notes:

        : This is a plain text alternative to the colored logging methods.
            It's used when the --no-colors flag is specified.

        """
        print('DEBUG:', *args)

    def log_info(self, *args):
        """
        Log info message in color (green).

        ### Args:

        - ***args**: Message parts to log

        ### Notes:

        : This method uses ANSI color codes to display info messages in green.
            It's the default logging method unless --no-colors is specified.

        """
        print('\033[32mINFO:', *args, '\033[39m')

    def log_warning(self, *args):
        """
        Log warning message in color (yellow).

        ### Args:

        - ***args**: Message parts to log

        ### Notes:

        : This method uses ANSI color codes to display warning messages in yellow.
            It's the default logging method unless --no-colors is specified.

        """
        print('\033[33mWARN:', *args, '\033[39m')

    def log_error(self, *args):
        """
        Log error message in color (red).

        ### Args:

        - ***args**: Message parts to log

        ### Notes:

        : This method uses ANSI color codes to display error messages in red.
            It's the default logging method unless --no-colors is specified.

        """
        print('\033[31mERR:', *args, '\033[39m')

    def log_debug(self, *args):
        """
        Log debug message in color (magenta).

        ### Args:

        - ***args**: Message parts to log

        ### Notes:

        : This method uses ANSI color codes to display debug messages in magenta.
            It's the default logging method unless --no-colors is specified.

        """
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
        """
        Start the scheduler service.

        This command initializes and starts the scheduler with the
        configured options, including interactive shell if requested.

        ### Notes:

        : This method is exposed as a CLI command and handles the command-line
            arguments for starting the scheduler. It can run the scheduler in
            interactive mode with a command shell or in background mode.

        : When running in interactive mode, it replaces the default log methods
            with console-friendly versions (with or without colors) to ensure
            proper display in the interactive shell.

        : Command-line options:

            1. --background: Start without interactive shell
            1. --paused: Start in paused state
            1. --no-colors: Use plain text output without ANSI colors

        """
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
    """
    Extend the application with scheduler functionality.

    This function adds the scheduler extension to the application and
    initializes it, making it available as app.scheduler throughout
    the application.

    ### Args:

    - **app**: The Tokeo application instance

    ### Notes:

    : This function is called automatically during the application's post_setup
        phase. It creates an instance of TokeoScheduler, configures it, and
        attaches it to the application as the 'scheduler' attribute.

    """
    app.extend('scheduler', TokeoScheduler(app))
    app.scheduler._setup(app)


def tokeo_scheduler_shutdown(app):
    """
    Handle application shutdown for scheduler.

    Properly cleans up scheduler resources when the application is shutting down.
    This function is registered as a pre_close hook to ensure proper cleanup.

    ### Args:

    - **app**: The Tokeo application instance

    ### Notes:

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

    ### Args:

    - **app**: The Tokeo application instance

    ### Example:

    ```python
    # In your application configuration:
    class MyApp(App):
        class Meta:
            extensions = [
                'tokeo.ext.scheduler',
                # other extensions...
            ]
    ```

    ### Notes:

    : This function performs three key actions:

        1. Registers the TokeoSchedulerController for CLI integration
        1. Registers a post_setup hook to initialize the scheduler
        1. Registers a pre_close hook for proper cleanup

    : After loading this extension, the scheduler is available as
        app.scheduler and the 'scheduler' CLI command is added to the
        application.

    """
    app.handler.register(TokeoSchedulerController)
    app.hook.register('post_setup', tokeo_scheduler_extend_app)
    app.hook.register('pre_close', tokeo_scheduler_shutdown)
