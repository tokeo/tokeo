"""
Tokeo NiceGUI Extension Module.

This extension integrates NiceGUI with Tokeo applications, providing a modern
web interface capability. NiceGUI allows for creating responsive web UIs with
Python, and this extension makes it available within the Tokeo framework.

The extension provides file monitoring capabilities for hot reloading during
development, with both watchdog-based monitoring (preferred) and a fallback
polling mechanism.

### Features:

- **Direct UI components** via app.nicegui.ui for building web interfaces
- **FastAPI integration** for advanced web functionality and REST endpoints
- **File watching** with hot-reloading support during development
- **Element helper** for custom HTML elements not directly exposed by NiceGUI
- **Extensive configuration** with sensible defaults for most use cases
- **Complete lifecycle management** integrated with Tokeo application hooks
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

    ### Notes:

    1. Used for NiceGUI-specific error conditions like configuration issues,
      file watching problems, or missing dependencies

    1. Inherits from the base TokeoError class to maintain error handling
      consistency across the framework

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

        ### Notes:

        - Used for hot-reloading functionality during development
        - Monitors file changes based on configured patterns
        - Calls the provided callback function when changes are detected
        - Only available when the watchdog package is installed

        """

        def __init__(self, patterns=None, ignore_patterns=None, ignore_directories=False, case_sensitive=False, callback=None):
            """
            Initialize the watchdog event handler.

            ### Args:

            - **patterns** (list): List of file patterns to watch for changes
            - **ignore_patterns** (list): List of file patterns to ignore
            - **ignore_directories** (bool): Whether to ignore directory events
            - **case_sensitive** (bool): Whether patterns are case sensitive
            - **callback** (callable): Function to call when changes are detected

            ### Notes:

            - The patterns use glob syntax (e.g., *.py for all Python files)

            - The callback will be invoked without arguments whenever a matching
              file system event is detected

            """
            super().__init__(
                patterns=patterns, ignore_patterns=ignore_patterns, ignore_directories=ignore_directories, case_sensitive=case_sensitive
            )
            self.callback = callback

        def on_any_event(self, event):
            """
            Handle any file system event by calling the callback.

            ### Args:

            - **event** (FileSystemEvent): The file system event that was detected

            ### Notes:

            - Called automatically by watchdog when a matching file system event occurs

            - Only invokes the callback if one was provided during initialization

            """
            if self.callback:
                self.callback()

except ImportError:

    class TokeoNiceguiWatchdogEventHandler:
        """
        Fallback event handler when watchdog is not available.

        This class provides a placeholder implementation that raises an error
        when instantiated, indicating that the watchdog library is missing.

        ### Notes:

        - Used when the watchdog package is not installed

        - Raises an error to indicate watchdog is required for file monitoring
        """

        def __init__(self, patterns=None, ignore_patterns=None, ignore_directories=True, case_sensitive=False, callback=None):
            """
            Raise an error indicating watchdog is not available.

            ### Args:

            - **patterns** (list): List of file patterns to watch for changes
            - **ignore_patterns** (list): List of file patterns to ignore
            - **ignore_directories** (bool): Whether to ignore directory events
            - **case_sensitive** (bool): Whether patterns are case sensitive
            - **callback** (callable): Function to call when changes are detected

            ### Raises:

            - **TokeoNiceguiError**: Always raised to indicate watchdog is missing

            """
            raise TokeoNiceguiError('Watchdog library is missing to observe file changes')

        def on_any_event(self, event):
            """
            Placeholder method for handling file system events.

            ### Args:

            - **event** (FileSystemEvent): The file system event that was detected

            ### Notes:

            : This method is never called as initialization always raises an error

            """
            pass


class NiceguiElementHelper:
    """
    Helper class to facilitate creation of NiceGUI elements.

    This class provides a simple way to dynamically create NiceGUI elements
    using attribute access, supporting both standard elements and text elements.
    It expands NiceGUI's capabilities by allowing access to HTML elements that
    aren't directly exposed by the NiceGUI API.

    ### Notes:

    - Accessible via app.nicegui.ux in Tokeo applications
    - Creates HTML elements dynamically based on attribute access
    - Handles both empty elements and text-containing elements
    - Allows creating **ANY** HTML element even if not directly supported by NiceGUI

    """

    def __getattr__(self, tag, *args, **kwargs):
        """
        Dynamically create element factory functions for NiceGUI.

        This method is called when an attribute is accessed that doesn't exist,
        and returns a function that creates NiceGUI elements with the given tag.

        ### Args:

        - **tag** (str): The HTML tag name for the element
        - ***args**: Positional arguments (unused)
        - ****kwargs**: Keyword arguments (unused)

        ### Returns:

        - **callable**: A function that creates NiceGUI elements

        ### Notes:

        1. The returned function creates either an Element or a TextElement
          depending on whether text content is provided

        1. Usage example: app.nicegui.ux.article("My content") creates a <article> element

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
    hot-reloading capabilities. It serves as the primary interface between
    the Tokeo application and the NiceGUI library.

    ### Notes:

    - Provides direct access to NiceGUI components via ui and fastapi_app attributes
    - Manages file monitoring for hot-reloading during development
    - Handles application configuration and startup/shutdown lifecycle
    - Exposes a custom element helper for creating arbitrary HTML elements

    """

    class Meta:
        """
        Extension meta-data and configuration defaults.

        """

        # Unique identifier for this handler
        label = 'tokeo.nicegui'

        # Id for config
        config_section = 'nicegui'

        # Dict with initial settings
        config_defaults = dict(
            host='127.0.0.1',
            port='4123',
            apis=None,
            routes=None,
            default_route=None,
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
        Set up the NiceGUI extension.

        Initializes configuration, UI access, and file monitoring components.
        This method is called during application startup to prepare the NiceGUI
        extension for use.

        ### Args:

        - **app** (Application): The Cement application instance

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
        # lazy import apis and routes modul
        self._apis = self._config('apis')
        self._routes = self._config('routes')
        self._default_route = self._config('default_route')
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

        This is a convenient wrapper around the application's config.get method,
        accessing values from the extension's config section.

        ### Args:

        - **key** (str): Configuration key to retrieve
        - **kwargs**: Additional arguments passed to config.get()

        ### Returns:

        - **Any**: The configuration value for the specified key

        ### Notes:

        - Uses the handler's config_section as defined in Meta

        - Passes through any additional arguments to config.get()

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def _setup_watchdog(self):
        """
        Set up watchdog observer to watch for file changes.

        Configures and starts the watchdog observer to monitor specified
        directories for file changes that should trigger a hot reload.

        ### Notes:

        - Uses configuration settings to determine which files to watch
        - Sets up a callback that will trigger a hotload when changes are detected
        - Starts the observer in a separate thread to monitor for changes

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

        ### Notes:

        - Runs as an asynchronous task in the FastAPI event loop
        - Checks the _watchdog_hotload_requested flag every 2 seconds
        - Triggers FastAPI shutdown when changes are detected
        - The shutdown triggers the application restart through the hooks

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

        Loads configured APIs and routes, sets up file monitoring if requested,
        and starts the NiceGUI server. This is the main entry point for running
        the NiceGUI web application.

        ### Args:

        - **hotload_dir** (str, optional): Directory to monitor for file changes
        - **hotload** (bool): Whether to enable hot reloading functionality

        ### Raises:

        - **TokeoNiceguiError**: If the default route handler cannot be found

        ### Notes:

        - Dynamically imports API and route modules based on configuration
        - Sets up the default route handler if specified
        - Configures file monitoring if hot-reloading is enabled
        - Starts the NiceGUI server with configuration from the application

        """
        # load the api and routes module
        apis_module = importlib.import_module(self._apis)
        routes_module = importlib.import_module(self._routes)
        # check default web handler
        if self._default_route and isinstance(self._default_route, str) and self._default_route != '':
            default_route = getattr(routes_module, self._default_route, None)
            # verify
            if default_route is None:
                raise TokeoNiceguiError(
                    # fmt: skip
                    f'Default route handler "{self._default_route}" could not be found in module "{self._routes}"'
                )
            # initialize registered default route
            default_route()
        # check config for watchdog
        if hotload:
            self._hotload_dir = hotload_dir if hotload_dir else self._config('hotload_dir', fallback=None)
            # if no dir set use the module's path
            if self._hotload_dir is None:
                self._hotload_dir = getattr(routes_module, '__file__', getattr(apis_module, '__file__', None))
            # check to point for a dir
            if self._hotload_dir and isfile(self._hotload_dir):
                self._hotload_dir = dirname(self._hotload_dir)
            else:
                self._hotload_dir = self.app._meta.main_dir
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
        This method is called during application shutdown to ensure proper
        cleanup of NiceGUI resources.

        ### Notes:

        - Only stops the watchdog observer if it's running
        - Uses a timeout to prevent hanging during shutdown
        - Called automatically by the pre_close application hook

        """
        # Stop watchdog observer if running
        if self._watchdog_observer and self._watchdog_observer.is_alive():
            self._watchdog_observer.stop()
            self._watchdog_observer.join(timeout=1)

    def hotload(self):
        """
        Restart the application when file changes are detected.

        If watchdog has detected file changes, this method will restart the
        application process to apply the changes. This enables the hot-reloading
        functionality during development.

        ### Notes:

        - Only restarts if the _watchdog_hotload_requested flag is set
        - Performs a full process restart to ensure clean reloading
        - Called automatically by the post_close application hook

        ### Output:

        : Logs a message indicating that hotload is in progress

        """
        if self._watchdog_hotload_requested:
            self.app.log.info('Hotload webservice ...')
            mp_exit_function()
            os_execv(sys_executable, ['python'] + argv)


class TokeoNiceguiController(Controller):
    """
    Command-line controller for NiceGUI functionality.

    This controller provides CLI commands for starting and managing the NiceGUI
    web server. It integrates with the Tokeo command-line interface to allow
    users to start and manage the web application from the command line.

    """

    class Meta:
        """
        Controller meta-data for command-line integration.

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
        Set up the controller with the application.

        ### Args:

        - **app** (Application): The Cement application instance
        """
        super()._setup(app)

    @ex(
        help='serve the webserver service',
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

        ### Args:

        - **--hotload** (flag): Enable hot-reloading for development
        - **--hotload-dir** (str): Directory to monitor for file changes

        ### Notes:

        - Delegates to the app.nicegui.startup() method
        - Passes command-line arguments to control hot-reloading behavior
        - The server runs until interrupted (Ctrl+C) or a file change triggers restart

        """
        self.app.nicegui.startup(
            hotload_dir=self.app.pargs.hotload_dir,
            hotload=self.app.pargs.hotload,
        )


def tokeo_nicegui_pdoc_render_decorator(app, func, decorator, args, kwargs):
    """
    Handle docstrings for nicegui decorators in pdoc.

    This function provides custom handling for NiceGUI decorators during
    documentation generation with pdoc. It extracts path parameters from
    decorators and loads appropriate docstrings.

    ### Args:

    - **app** (Application): The Cement application instance
    - **func** (function): The function being decorated
    - **decorator** (str): The decorator string
    - **args** (list): Positional arguments to the decorator
    - **kwargs** (dict): Keyword arguments to the decorator

    ### Returns:

    - **dict|None**: Dictionary with decorator information or None if not handled

    ### Notes:

    1. Handles FastAPI route decorators (@app.nicegui.fastapi_app.get, @app.nicegui.fastapi_app.post)
    1. Handles NiceGUI page decorators (@app.nicegui.ui.page, @ui.page)
    1. Extracts parameters values for better documentation

    """
    if decorator == '@app.nicegui.fastapi_app.get':
        params = None
        if args is not None:
            try:
                value = args[0].value
                params = f'"{value}"' if isinstance(value, str) else f'{value}'
            except Exception:
                params = '...path...'
        return dict(
            decorator=decorator,
            params=params,
            docstring=app.pdoc.docstrings('decorator', 'fastapi.get'),
        )
    elif decorator == '@app.nicegui.fastapi_app.post':
        params = None
        if args is not None:
            try:
                value = args[0].value
                params = f'"{value}"' if isinstance(value, str) else f'{value}'
            except Exception:
                params = '...path...'
        return dict(
            decorator=decorator,
            params=params,
            docstring=app.pdoc.docstrings('decorator', 'fastapi.post'),
        )
    elif decorator == '@app.nicegui.ui.page' or decorator == '@ui.page':
        params = None
        if args is not None:
            try:
                value = args[0].value
                params = f'"{value}"' if isinstance(value, str) else f'{value}'
            except Exception:
                params = '...path...'
        return dict(
            decorator=decorator,
            params=params,
            docstring=app.pdoc.docstrings('decorator', 'nicegui.page'),
        )


def tokeo_nicegui_extend_app(app):
    """
    Extend the application with NiceGUI functionality.

    This function adds the NiceGUI extension to the application and
    initializes it, making it available as app.nicegui.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    1. This function is called during application setup
    1. It creates the TokeoNicegui instance and attaches it to the app
      as app.nicegui

    """
    app.extend('nicegui', TokeoNicegui(app))
    app.nicegui._setup(app)


def tokeo_nicegui_shutdown(app):
    """
    Handle application shutdown for NiceGUI.

    Properly cleans up NiceGUI resources when the application is shutting down.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    1. Called during application shutdown to properly close connections
    1. Important for clean application termination without resource leaks

    """
    app.nicegui.shutdown()


def tokeo_nicegui_hotload(app):
    """
    Handle hot reloading for NiceGUI.

    Triggers application restart if file changes have been detected.
    This function is called after the application has closed to implement
    the hot-reload functionality.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    1. Called after application shutdown to check if a restart is needed
    1. Only triggers a restart if file changes were detected
    1. Uses Python's execv to replace the current process with a new one

    """
    app.nicegui.hotload()


def load(app):
    """
    Load the NiceGUI extension into a Tokeo application.

    Registers the controller and hooks needed for NiceGUI integration.
    This function is the main entry point for the extension, called by Cement
    during the application initialization process.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    1. Registers the TokeoNiceguiController for CLI commands
    1. Sets up hooks for application lifecycle integration
    1. Integrates with pdoc for documentation generation

    """
    app.handler.register(TokeoNiceguiController)
    app.hook.register('post_setup', tokeo_nicegui_extend_app)
    app.hook.register('pre_close', tokeo_nicegui_shutdown)
    app.hook.register('post_close', tokeo_nicegui_hotload)
    app.hook.register('tokeo_pdoc_render_decorator', tokeo_nicegui_pdoc_render_decorator)
