import os
import signal
import tokeo.core.utils.strict  # noqa: F401
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

    ### Notes

    - The application includes several extensions by default:
        colorlog, generate, pdoc, print, jinja2, and yaml
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

    ### Usage

    ```python
    # Basic test setup
    from tokeo.main import TokeoTest

    with TokeoTest() as app:
        app.run()
        # Perform assertions on app state

    ```

    ### Notes

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

    ### Notes

    - Returns nothing; the process exit code is set on ``app.exit_code`` and
        applied on close via the app's exit_on_close behavior
    - AssertionError and TokeoError are caught and reported with exit code 1;
        a CaughtSignal (SIGINT/SIGTERM) is caught and exits with code 0

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
            if app and app.log and app.log.error:
                app.log.error(type(e).__name__)
                app.log.error(e.args[0] if e.args else str(e))
            else:
                print(type(e).__name__)
                print(e.args[0] if e.args else str(e))
            app.exit_code = 1

            if app.debug is True:
                import traceback

                traceback.print_exc()

        except CaughtSignal as e:
            # cement turns SIGINT and SIGTERM into CaughtSignal; exit 0 (non-error)
            if e.signum == signal.SIGINT:
                print('\nstopped by Ctrl-C')
            elif e.signum == signal.SIGTERM:
                print('\nterminated by SIGTERM')
            else:
                print(f'\n{e}')
            app.exit_code = 0


if __name__ == '__main__':
    main()
