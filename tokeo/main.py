import os
from cement import App, TestApp
from cement.core.exc import CaughtSignal
from cement.utils import fs
from .config import config_defaults
from .core.exc import TokeoError
from .core.hooks import hook_dramatiq_setup
from .controllers.base import Base
from .controllers.emit import Emit
from .controllers.dramatiq import Dramatiq
from .controllers.grpc import Grpc


class Tokeo(App):
    """The Tokeo primary application."""

    class Meta:
        label = 'tokeo'

        # configuration defaults
        config_defaults = config_defaults()

        # call sys.exit() on close
        exit_on_close = True

        # load additional framework extensions
        extensions = [
            'yaml',
            'colorlog',
            'jinja2',
        ]

        # configuration handler
        config_handler = 'yaml'

        # configuration file suffix
        config_file_suffix = '.yaml'

        # add local config to app
        config_dirs = [fs.abspath(os.path.dirname(__file__) + '/../config')]

        # set the log handler
        log_handler = 'colorlog'

        # set the output handler
        output_handler = 'jinja2'

        # register handlers
        handlers = [
            Base,
            Emit,
            Dramatiq,
            Grpc,
        ]

        # register hooks
        hooks = [
            ('post_setup', hook_dramatiq_setup),
        ]


class TokeoTest(TestApp, Tokeo):
    """A sub-class of Tokeo that is better suited for testing."""

    class Meta:
        label = 'tokeo'


def main():
    with Tokeo() as app:
        try:
            app.run()

        except AssertionError as e:
            print('AssertionError > %s' % e.args[0])
            app.exit_code = 1

            if app.debug is True:
                import traceback

                traceback.print_exc()

        except TokeoError as e:
            print('TokeoError > %s' % e.args[0])
            app.exit_code = 1

            if app.debug is True:
                import traceback

                traceback.print_exc()

        except CaughtSignal as e:
            # Default Cement signals are SIGINT and SIGTERM, exit 0 (non-error)
            print('\n%s' % e)
            app.exit_code = 0


if __name__ == '__main__':
    main()