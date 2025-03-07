from sys import argv, executable as sys_executable
from os import execv as os_execv
from os.path import basename, dirname, isfile
from multiprocessing.util import _exit_function as mp_exit_function
from asyncio import sleep as asyncio_sleep
import importlib
from tokeo.ext.argparse import Controller
from tokeo.core.exc import TokeoError
from cement.core.meta import MetaMixin
from cement import ex
from nicegui import ui, app as fastapi_app
from nicegui.element import Element
from nicegui.elements.mixins.text_element import TextElement


class TokeoNiceguiError(TokeoError):
    """Tokeo automate errors."""

    pass


try:
    from watchdog.observers import Observer
    from watchdog.events import PatternMatchingEventHandler

    class TokeoNiceguiWatchdogEventHandler(PatternMatchingEventHandler):
        """Event handler for watchdog to monitor file changes."""

        def __init__(self, patterns=None, ignore_patterns=None, ignore_directories=False, case_sensitive=False, callback=None):
            super().__init__(
                patterns=patterns, ignore_patterns=ignore_patterns, ignore_directories=ignore_directories, case_sensitive=case_sensitive
            )
            self.callback = callback

        def on_any_event(self, event):
            if self.callback:
                self.callback()

except ImportError:

    class TokeoNiceguiWatchdogEventHandler:
        """Event null handler."""

        def __init__(self, patterns=None, ignore_patterns=None, ignore_directories=True, case_sensitive=False, callback=None):
            raise TokeoNiceguiError('Watchdog library is missing to observe file changes')

        def on_any_event(self, event):
            pass


class NiceguiElementHelper:

    def __getattr__(self, tag, *args, **kwargs):
        def wrapper(text=None, *args, **kwargs):
            if text is None:
                return Element(tag=tag)
            else:
                return TextElement(tag=tag, text=text)

        return wrapper


class TokeoNicegui(MetaMixin):

    class Meta:
        """Extension meta-data."""

        #: Unique identifier for this handler
        label = 'tokeo.nicegui'

        #: Id for config
        config_section = 'nicegui'

        #: Dict with initial settings
        config_defaults = dict(
            host='127.0.0.1',
            port='4123',
            pages=None,
            default=None,
            title='Tokeo NiceGUI',
            favicon=None,
            viewport='width=device-width, initial-scale=1',
            dark=None,
            tailwind=True,
            prod_js=True,
            welcome_message=None,
            endpoint_documentation=None,
            storage_secret=None,
            binding_refresh_interval=0.5,
            reconnect_timeout=5.0,
            hotload_dir=None,
            hotload_includes='*.py',
            hotload_excludes='.*, .py[cod], .sw.*, ~*',
            logging_level='warning',
        )

    def _setup(self, app):
        # save pointer to app
        self.app = app
        # prepare the config
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        # allow easy access to ui
        self.ui = ui
        # allow easy access to fastapi app
        self.fastapi_app = fastapi_app
        # add the ux helper
        self.ux = NiceguiElementHelper()
        # lazy import pages modul
        self._pages = self._config('pages')
        self._default = self._config('default')
        # test welcome message
        self._welcome_message = self._config('welcome_message')
        if self._welcome_message is None:
            self._welcome_message = f'{self._config('title')} ready to go on'
        # watchdog files components
        self._hotload_dir = None
        self._watchdog_hotload_requested = False
        self._watchdog_observer = None
        self._watchdog_handler = None

    def _config(self, key, **kwargs):
        """
        This is a simple wrapper, and is equivalent to:
            ``self.app.config.get(<section>, <key>)``.
        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def _setup_watchdog(self):
        """
        Setup watchdog observer to watch for file changes.
        """

        # Use configuration
        hotload_dir = self._hotload_dir
        includes = self._config('hotload_includes', fallback='*.py')
        excludes = self._config('hotload_excludes', fallback='')
        # Convert string patterns to lists
        include_patterns = [p.strip() for p in includes.split(',') if p.strip()]
        exclude_patterns = [p.strip() for p in excludes.split(',') if p.strip()]
        # Create event handler that will trigger hotload on file changes
        self._watchdog_handler = TokeoNiceguiWatchdogEventHandler(
            patterns=include_patterns,
            ignore_patterns=exclude_patterns,
            ignore_directories=False,
            case_sensitive=False,
            callback=lambda: setattr(self, '_watchdog_hotload_requested', True),
        )
        # Create observer
        self._watchdog_observer = Observer()
        # Create scheduler
        self._watchdog_observer.schedule(self._watchdog_handler, hotload_dir, recursive=True)
        # Start the observer
        self._watchdog_observer.start()

    async def _watchdog_file_changes(self):
        """
        Check for file changes every 2 seconds in configured directories.
        Handles app hotload if changes are detected.
        """

        while True:
            # Check if watchdog detected changes
            if self._watchdog_hotload_requested:
                fastapi_app.shutdown()
                break
            else:
                # Wait before checking again
                await asyncio_sleep(2)

    def startup(self, hotload_dir=None, hotload=False):
        # load the api and weppages module
        module = importlib.import_module(self._pages)
        # check default web handler
        if self._default and isinstance(self._default, str) and self._default != '':
            default_page = getattr(module, self._default, None)
            # verify
            if default_page is None:
                raise TokeoNiceguiError(f'Default page handler "{self._default}" could not be found in module "{self._pages}"')
            # initialize registered default page
            default_page()
        # check config for watchdog
        if hotload:
            self._hotload_dir = hotload_dir if hotload_dir else self._config('hotload_dir', fallback=None)
            # if no dir set use the module's path
            if self._hotload_dir is None:
                self._hotload_dir = module.__file__
            # check to point for a dir
            if isfile(self._hotload_dir):
                self._hotload_dir = dirname(self._hotload_dir)
            # activate watchdog
            self._setup_watchdog()
            # Create the file monitor task to run in the FastAPI's event loop
            fastapi_app.on_startup(self._watchdog_file_changes)
        # use custom welcome message
        fastapi_app.on_startup(lambda: self.app.log.info(f'{self._welcome_message} {", ".join([x for x in fastapi_app.urls])}'))
        # spin up service
        ui.run(
            # config
            host=self._config('host'),
            port=int(self._config('port')),
            title=self._config('title'),
            favicon=self._config('favicon'),
            viewport=self._config('viewport'),
            dark=self._config('dark'),
            tailwind=self._config('tailwind'),
            prod_js=self._config('prod_js'),
            storage_secret=self._config('storage_secret'),
            binding_refresh_interval=float(self._config('binding_refresh_interval')),
            reconnect_timeout=float(self._config('reconnect_timeout')),
            uvicorn_logging_level=self._config('logging_level'),
            endpoint_documentation=self._config('endpoint_documentation'),
            # config fixed
            show_welcome_message=False,
            show=False,
            native=False,
            reload=False,
            uvicorn_reload_dirs=None,
            uvicorn_reload_includes=None,
            uvicorn_reload_excludes=None,
            on_air=None,
        )

    def shutdown(self):
        """
        Clean up resources when application shuts down.
        """

        # Stop watchdog observer if running
        if self._watchdog_observer and self._watchdog_observer.is_alive():
            self._watchdog_observer.stop()
            self._watchdog_observer.join(timeout=1)

    def hotload(self):
        """
        If watch raises for file change, hotload the service.
        """

        if self._watchdog_hotload_requested:
            self.app.log.info('Hotload webservice ...')
            mp_exit_function()
            os_execv(sys_executable, ['python'] + argv)


class TokeoNiceguiController(Controller):

    class Meta:
        label = 'nicegui'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'start web frontend server'
        description = 'Start the tokeo nicegui webservice.'
        epilog = f'Example: {basename(argv[0])} nicegui serve'

    def _setup(self, app):
        super()._setup(app)

    @ex(
        help='serve the wbeserver service',
        description='Spin up the webserver.',
        arguments=[
            (
                ['--hotload'],
                dict(
                    action='store_true',
                    help='on file change hotload the service',
                ),
            ),
            (
                ['--hotload-dir'],
                dict(
                    type=str,
                    default=None,
                    help='root dir to check for filechanges for hotload',
                ),
            ),
        ],
    )
    def serve(self):
        self.app.nicegui.startup(
            hotload_dir=self.app.pargs.hotload_dir,
            hotload=self.app.pargs.hotload,
        )


def tokeo_nicegui_extend_app(app):
    app.extend('nicegui', TokeoNicegui(app))
    app.nicegui._setup(app)


def tokeo_nicegui_shutdown(app):
    app.nicegui.shutdown()


def tokeo_nicegui_hotload(app):
    app.nicegui.hotload()


def load(app):
    app.handler.register(TokeoNiceguiController)
    app.hook.register('post_setup', tokeo_nicegui_extend_app)
    app.hook.register('pre_close', tokeo_nicegui_shutdown)
    app.hook.register('post_close', tokeo_nicegui_hotload)
