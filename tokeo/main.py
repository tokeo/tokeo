import os
from cement import App, TestApp
from cement.core.exc import CaughtSignal
from cement.utils import fs
from .core.exc import TokeoError
from .controllers.base import BaseController


class Tokeo(App):
    """The Tokeo generator application."""

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
    """A sub-class of Tokeo that is better suited for testing."""

    class Meta:
        # this app test name
        label = f'{Tokeo.Meta.label}_test'


def main():
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
