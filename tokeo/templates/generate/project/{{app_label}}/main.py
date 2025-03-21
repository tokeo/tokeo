"""
{{ app_class_name }} main module providing CLI application framework.

This module defines the main {{ app_class_name }} application classes and
entry point. It leverages the Cement framework to create a robust and
extensible CLI application with support for various extensions and handlers.

"""

import os
from cement import App, TestApp
from cement.utils import fs
from cement.core.exc import CaughtSignal
from .core.exc import {{ app_class_name }}Error
from .controllers.base import BaseController
{% if feature_dramatiq == "Y" %}
from .controllers.emit import EmitController
{% endif -%}
{% if feature_grpc == "Y" %}
from .controllers.grpccall import GrpcCallController
{% endif %}


class {{ app_class_name }}(App):
    """
    The {{ app_class_name }} CLI application core class.

    Extends the Cement App class to provide a configurable and extensible
    CLI application framework. {{ app_class_name }} applications use the
    Cement framework for command-line parsing, configuration management,
    logging, and more.

    ### Notes:

    - The application includes several extensions by default:
        colorlog, generate, pdoc, print, jinja2, yaml and others
    - The application's configuration is loaded from YAML files
    - Signal handling (SIGINT, SIGTERM) is automatically managed

    """

    class Meta:
        # this app name
        label = '{{ app_label }}'

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
            'tokeo.ext.pdoc',
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
            'tokeo.ext.smtp',
{% if feature_diskcache == "Y" or feature_dramatiq == "Y" or feature_apscheduler == "Y" %}
            'tokeo.ext.diskcache',
{% endif -%}
{% if feature_dramatiq == "Y" %}
            'tokeo.ext.dramatiq',
{% endif -%}
{% if feature_grpc == "Y" %}
            'tokeo.ext.grpc',
{% endif -%}
{% if feature_apscheduler == "Y" %}
            'tokeo.ext.scheduler',
{% endif -%}
{% if feature_nicegui == "Y" %}
            'tokeo.ext.nicegui',
{% endif -%}
{% if feature_pocketbase == "Y" %}
            'tokeo.ext.pocketbase',
{% endif -%}
{% if feature_automate == "Y" %}
            'tokeo.ext.automate',
{% endif %}
        ]

        # register handlers
        handlers = [
            BaseController,
{% if feature_dramatiq == "Y" %}
            EmitController,
{% endif -%}
{% if feature_grpc == "Y" %}
            GrpcCallController,
{% endif %}
        ]

        # configuration file suffix
        config_file_suffix = '.yaml'

        # set the log handler
        log_handler = 'colorlog'


class {{ app_class_name }}Test(TestApp, {{ app_class_name }}):
    """
    A specialized subclass of {{ app_class_name }} designed for testing purposes.

    This class extends both TestApp from the Cement framework and
    the {{ app_class_name }} application class to provide a testing environment
    for {{ app_class_name }} applications. It modifies various settings to be more
    suitable for automated testing.

    ### Usage:

    ```python
    # Basic test setup
    from {{ all_label }}.main import {{ app_class_name }}Test

    with {{ app_class_name }}Test() as app:
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
        label = {{ "f'{" }}{{ app_class_name }}{{ ".Meta.label}_test'" }}


{% if feature_dramatiq == "Y" %}
def dramatiq():
    """
    Entry point for Dramatiq worker processes.

    This function initializes the application with Dramatiq configuration
    and settings when the application is started as a Dramatiq worker.
    It sets up the necessary environment without running the full
    CLI application stack.

    ### Used by {{ app_name }} CLI for the dramatiq workers:

    ```bash
    {{ app_label }} dramatiq serve
    ```

    ### Notes:

    - Initializes the application without command processing
    - Disables signal handling as Dramatiq manages its own signals
    - Sets up configuration, handlers, and hooks for task processing
    - Does not block or run the event loop (Dramatiq handles that)
    - Dramatiq will find and register all tasks automatically

    """
    # instantiate app to get config etc. when starting as module via dramatiq
    app = {{ app_class_name }}()
    # disable signal catching when started as module by dramatiq
    app._meta.catch_signals = None
    # run setup to inintializes config, handlers and hooks
    app.setup()


{% endif -%}
def main():
    """
    Main entry point for the {{ app_name }} application.

    Creates a {{ app_class_name }} application instance, runs it, and handles
    any exceptions that may occur during execution. This function serves as
    the primary entry point when running {{ app_name }} as
    a command-line application.

    ### Returns:

    - **int**: Exit code indicating success (0) or failure (non-zero)

    ### Raises:

    - **AssertionError**: When an assertion fails during application execution
    - **{{ app_class_name }}Error**: When a {{ app_class_name }}-specific
        error occurs
    - **CaughtSignal**: When a signal (e.g., SIGINT, SIGTERM) is caught

    """
    with {{ app_class_name }}() as app:
        try:
            app.run()

        except AssertionError as e:
            print(f'AssertionError > {e.args[0]}')
            app.exit_code = 1

            if app.debug is True:
                import traceback

                traceback.print_exc()

        except {{ app_class_name }}Error as e:
            print(f'{{ app_class_name }}Error > {e.args[0]}')
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
