import os
from cement import App, TestApp
from cement.core.exc import CaughtSignal
from cement.utils import fs
from .core.exc import TokeoError
from .controllers.base import BaseController


class Tokeo(App):
    """The Tokeo primary application."""

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
            'jinja2',
            'tokeo.ext.print',
            'tokeo.ext.yaml',
            'tokeo.ext.appshare',
        ]

        # register handlers
        handlers = [
            BaseController,
        ]

        # configuration file suffix
        config_file_suffix = '.yaml'

        # set the log handler
        log_handler = 'colorlog'

        # set the output handler
        output_handler = 'jinja2'


class TokeoTest(TestApp, Tokeo):
    """A sub-class of Tokeo that is better suited for testing."""

    class Meta:
        # this app test name
        label = f'{Tokeo.Meta.label}:test'


def dramatiq():
    # instantiate app to get config etc. when starting as module via dramatiq
    app = Tokeo()
    # disable signal catching when started as module by dramatiq
    app._meta.catch_signals = None
    # run setup to inintializes config, handlers and hooks
    app.setup()


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
