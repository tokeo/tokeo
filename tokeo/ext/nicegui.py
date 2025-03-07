"""
Tokeo NiceGUI Extension Module.

This extension integrates NiceGUI with Tokeo applications, providing a modern
web interface capability. NiceGUI allows for creating responsive web UIs with
Python, and this extension makes it available within the Tokeo framework.

The extension provides file monitoring capabilities for hot reloading during
development, with both watchdog-based monitoring (preferred) and a fallback
polling mechanism.

Example:
    To use this extension in your application:

    .. code-block:: python

        from tokeo.app import TokeoApp

        with TokeoApp('myapp', extensions=['tokeo.ext.nicegui']) as app:
            # App now has access to NiceGUI through app.nicegui
            @app.nicegui.ui.page('/')
            def index():
                app.nicegui.ui.label('Hello from Tokeo NiceGUI!')

            # Start the web server
            app.run()
"""

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
    """
    Tokeo NiceGUI extension specific errors.

    This exception class is raised when the NiceGUI extension encounters
    errors during setup, configuration, or operation.
    """

    pass


try:
    from watchdog.observers import Observer
    from watchdog.events import PatternMatchingEventHandler

    class TokeoNiceguiWatchdogEventHandler(PatternMatchingEventHandler):
        """
        Event handler for watchdog to monitor file changes.

        This class extends PatternMatchingEventHandler to handle file system
        events detected by watchdog. When file changes are detected that match
        the configured patterns, it triggers the reload callback.

        Attributes:
            callback (callable): Function to call when file changes are detected.
        """

        def __init__(self, patterns=None, ignore_patterns=None, ignore_directories=False, case_sensitive=False, callback=None):
            """
            Initialize the watchdog event handler.

            Args:
                patterns (list): List of file patterns to watch for changes.
                ignore_patterns (list): List of file patterns to ignore.
                ignore_directories (bool): Whether to ignore directory events.
                case_sensitive (bool): Whether patterns are case sensitive.
                callback (callable): Function to call when changes are detected.
            """
            super().__init__(
                patterns=patterns, ignore_patterns=ignore_patterns, ignore_directories=ignore_directories, case_sensitive=case_sensitive
            )
            self.callback = callback

        def on_any_event(self, event):
            """
            Handle any file system event by calling the callback.

            Args:
                event: The file system event that was detected.
            """
            if self.callback:
                self.callback()

except ImportError:

    class TokeoNiceguiWatchdogEventHandler:
        """
        Fallback event handler when watchdog is not available.

        This class provides a placeholder implementation that raises an error
        when instantiated, indicating that the watchdog library is missing.
        """

        def __init__(self, patterns=None, ignore_patterns=None, ignore_directories=True, case_sensitive=False, callback=None):
            """
            Raise an error indicating watchdog is not available.

            Args:
                patterns (list): List of file patterns to watch for changes.
                ignore_patterns (list): List of file patterns to ignore.
                ignore_directories (bool): Whether to ignore directory events.
                case_sensitive (bool): Whether patterns are case sensitive.
                callback (callable): Function to call when changes are detected.

            Raises:
                TokeoNiceguiError: Always raised to indicate watchdog is missing.
            """
            raise TokeoNiceguiError('Watchdog library is missing to observe file changes')

        def on_any_event(self, event):
            """
            Placeholder method for handling file system events.

            Args:
                event: The file system event that was detected.
            """
            pass


class NiceguiElementHelper:
    """
    Helper class to facilitate creation of NiceGUI elements.

    This class provides a simple way to dynamically create NiceGUI elements
    using attribute access, supporting both standard elements and text elements.
    """

    def __getattr__(self, tag, *args, **kwargs):
        """
        Dynamically create element factory functions for NiceGUI.

        Args:
            tag (str): The HTML tag name for the element.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            callable: A function that creates NiceGUI elements.
        """

        def wrapper(text=None, *args, **kwargs):
            if text is None:
                return Element(tag=tag)
            else:
                return TextElement(tag=tag, text=text)

        return wrapper


class TokeoNicegui(MetaMixin):
    """
    Main NiceGUI extension class for Tokeo.

    This class provides the core functionality for integrating NiceGUI with
    Tokeo applications, including configuration, startup, shutdown, and
    hot-reloading capabilities.
    """

    class Meta:
        """
        Extension meta-data and configuration.

        Attributes:
            label (str): Unique identifier for this extension.
            config_section (str): Configuration section identifier.
            config_defaults (dict): Default configuration values.
        """

        # Unique identifier for this handler
        label = 'tokeo.nicegui'

        # Id for config
        config_section = 'nicegui'

        # Dict with initial settings
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
        """
        Setup the NiceGUI extension.

        Initializes configuration, UI access, and file monitoring components.

        Args:
            app: The application object.
        """
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
        Get configuration value from the extension's config section.

        This is a simple wrapper around the application's config.get method.

        Args:
            key (str): Configuration key to retrieve.
            **kwargs: Additional arguments passed to config.get().

        Returns:
            The configuration value for the specified key.
        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def _setup_watchdog(self):
        """
        Setup watchdog observer to watch for file changes.

        Configures and starts the watchdog observer to monitor specified
        directories for file changes that should trigger a hot reload.
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
        Asynchronously monitor for file changes and handle app hot reload.

        Periodically checks for file changes detected by watchdog and
        initiates application shutdown when changes are detected.
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
        """
        Start the NiceGUI web server with Tokeo integration.

        Loads configured web pages, sets up file monitoring if requested,
        and starts the NiceGUI server.

        Args:
            hotload_dir (str, optional): Directory to monitor for file changes.
            hotload (bool): Whether to enable hot reloading functionality.

        Raises:
            TokeoNiceguiError: If the default page handler cannot be found.
        """
        # load the api and weppages module
        module = importlib.import_module(self._pages)
        # check default web handler
        if self._default and isinstance(self._default, str) and self._default != '':
            default_page = getattr(module, self._default, None)
            # verify
            if default_page is None:
                raise TokeoNiceguiError(
                    # fmt: skip
                    f'Default page handler "{self._default}" could not be found in module "{self._pages}"'
                )
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
        fastapi_app.on_startup(
            # fmt: off
            lambda: self.app.log.info(
                f'{self._welcome_message} {", ".join([x for x in fastapi_app.urls])}'
            )
            # fmt: on
        )
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

        Stops the watchdog observer if it's running to prevent resource leaks.
        """
        # Stop watchdog observer if running
        if self._watchdog_observer and self._watchdog_observer.is_alive():
            self._watchdog_observer.stop()
            self._watchdog_observer.join(timeout=1)

    def hotload(self):
        """
        Restart the application when file changes are detected.

        If watchdog has detected file changes, this method will restart the
        application process to apply the changes.
        """
        if self._watchdog_hotload_requested:
            self.app.log.info('Hotload webservice ...')
            mp_exit_function()
            os_execv(sys_executable, ['python'] + argv)


class TokeoNiceguiController(Controller):
    """
    Command-line controller for NiceGUI functionality.

    Provides CLI commands for starting and managing the NiceGUI web server.
    """

    class Meta:
        """
        Controller meta-data configuration.

        Attributes:
            label (str): The identifier for this controller.
            stacked_type (str): How this controller is stacked.
            stacked_on (str): Which controller this is stacked on.
            subparser_options (dict): Options for the subparser.
            help (str): Help text for this controller.
            description (str): Detailed description for this controller.
            epilog (str): Epilog text displaying usage example.
        """

        label = 'nicegui'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'start web frontend server'
        description = 'Start the tokeo nicegui webservice.'
        epilog = f'Example: {basename(argv[0])} nicegui serve'

    def _setup(self, app):
        """
        Set up the controller.

        Args:
            app: The application object.
        """
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
        """
        Start the NiceGUI web server.

        This command initializes and starts the NiceGUI web server with
        the configured options, including optional hot reloading.
        """
        self.app.nicegui.startup(
            hotload_dir=self.app.pargs.hotload_dir,
            hotload=self.app.pargs.hotload,
        )


def tokeo_nicegui_extend_app(app):
    """
    Extend the application with NiceGUI functionality.

    This function adds the NiceGUI extension to the application and
    initializes it.

    Args:
        app: The application object.
    """
    app.extend('nicegui', TokeoNicegui(app))
    app.nicegui._setup(app)


def tokeo_nicegui_shutdown(app):
    """
    Handle application shutdown for NiceGUI.

    Properly cleans up NiceGUI resources when the application is shutting down.

    Args:
        app: The application object.
    """
    app.nicegui.shutdown()


def tokeo_nicegui_hotload(app):
    """
    Handle hot reloading for NiceGUI.

    Triggers application restart if file changes have been detected.

    Args:
        app: The application object.
    """
    app.nicegui.hotload()


def load(app):
    """
    Load the NiceGUI extension into a Tokeo application.

    Registers the controller and hooks needed for NiceGUI integration.

    Args:
        app: The application object.
    """
    app.handler.register(TokeoNiceguiController)
    app.hook.register('post_setup', tokeo_nicegui_extend_app)
    app.hook.register('pre_close', tokeo_nicegui_shutdown)
    app.hook.register('post_close', tokeo_nicegui_hotload)
