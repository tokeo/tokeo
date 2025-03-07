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
    """The {{ app_name }} primary application."""

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
    """A sub-class of {{ app_name }} that is better suited for testing."""

    class Meta:
        # this app test name
        label = {{ "f'{" }}{{ app_class_name }}{{ ".Meta.label}_test'" }}


{% if feature_dramatiq == "Y" %}
def dramatiq():
    # instantiate app to get config etc. when starting as module via dramatiq
    app = {{ app_class_name }}()
    # disable signal catching when started as module by dramatiq
    app._meta.catch_signals = None
    # run setup to inintializes config, handlers and hooks
    app.setup()


{% endif -%}
def main():
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
