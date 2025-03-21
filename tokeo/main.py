"""
Tokeo main module providing CLI application framework.

This module defines the main Tokeo application classes and entry point.
It leverages the Cement framework to create a robust and extensible CLI
application with support for various extensions and handlers.

"""

import os
from cement import App, TestApp
from cement.core.exc import CaughtSignal
from cement.utils import fs
from .core.exc import TokeoError
from .controllers.base import BaseController


class Tokeo(App):
    """
    The Tokeo CLI application core class.

    Extends the Cement App class to provide a configurable and extensible
    CLI application framework. Tokeo applications use the Cement framework
    for command-line parsing, configuration management, logging, and more.

    ### Notes:

    - The application includes several extensions by default: colorlog, generate, pdoc, print, jinja2, and yaml
    - The application's configuration is loaded from YAML files
    - Signal handling (SIGINT, SIGTERM) is automatically managed

    """

    class Meta:
        # this app name
        label = 'tokeo'

        # this app main path
        main_dir = os.path.dirname(fs.abspath(__file__))

        # configuration defaults
        config_defaults = dict(
            debug=False,
        )

        # call sys.exit() on close
        exit_on_close = True

        # load additional framework extensions
        extensions = [
            'colorlog',
            'generate',
            'tokeo.ext.pdoc',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.yaml',
        ]

        # register handlers
        handlers = [
            BaseController,
        ]

        # configuration file suffix
        config_file_suffix = '.yaml'

        # set the log handler
        log_handler = 'colorlog'


class TokeoTest(TestApp, Tokeo):
    """
    A specialized subclass of Tokeo designed for testing purposes.

    This class extends both TestApp from the Cement framework and the Tokeo
    application class to provide a testing environment for Tokeo applications.
    It modifies various settings to be more suitable for automated testing.

    ### Usage:

    ```python
    # Basic test setup
    from tokeo.main import TokeoTest

    with TokeoTest() as app:
        app.run()
        # Perform assertions on app state

    ```

    ### Notes:

    - Uses standard logging instead of colorlog for cleaner test output
    - Includes a smaller set of extensions to reduce test complexity
    - Appends '_test' to the app label to distinguish from production instances

    """

    class Meta:
        # this app test name
        label = f'{Tokeo.Meta.label}_test'

        # load additional framework extensions
        extensions = [
            'tokeo.ext.print',
        ]

        # set the log handler
        log_handler = 'logging'


def main():
    """
    Main entry point for the Tokeo application.

    Creates a Tokeo application instance, runs it, and handles any exceptions
    that may occur during execution. This function serves as the primary entry
    point when running Tokeo as a command-line application.

    ### Returns:

    - **int**: Exit code indicating success (0) or failure (non-zero)

    ### Raises:

    - **AssertionError**: When an assertion fails during application execution
    - **TokeoError**: When a Tokeo-specific error occurs
    - **CaughtSignal**: When a signal (e.g., SIGINT, SIGTERM) is caught

    """
    with Tokeo() as app:
        try:
            app.run()

        except AssertionError as e:
            print(f'AssertionError > {e.args[0]}')
            app.exit_code = 1

            if app.debug is True:
                import traceback

                traceback.print_exc()

        except TokeoError as e:
            print(f'TokeoError > {e.args[0]}')
            app.exit_code = 1

            if app.debug is True:
                import traceback

                traceback.print_exc()

        except CaughtSignal as e:
            # Default Cement signals are SIGINT and SIGTERM, exit 0 (non-error)
            if e.signum == 2:
                print('\nstopped by Ctrl-C')
            elif e.signum == 15:
                print('\nterminated by SIGTERM')
            else:
                print(f'\n{e}')
            app.exit_code = 0


if __name__ == '__main__':
    main()
