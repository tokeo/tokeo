from sys import argv
from os.path import basename, dirname, abspath
from tokeo.ext.argparse import Controller
from cement.core.meta import MetaMixin
from cement import ex
from nicegui import ui, app as fastapi_app
from nicegui.element import Element
from nicegui.elements.mixins.text_element import TextElement
import importlib


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
            pages='tokeo.core.pages.index',
            default='default',
            title='Tokeo NiceGUI',
            favicon=None,
            viewport='width=device-width, initial-scale=1',
            dark=None,
            tailwind=True,
            storage_secret=None,
            binding_refresh_interval=0.5,
            reconnect_timeout=5.0,
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
        module = importlib.import_module(self._config('pages'))
        # check default web handler
        if self._config('default') != '':
            default_page = getattr(module, self._config('default'))
            # initialize page
            default_page()

    def _config(self, key, default=None):
        """
        This is a simple wrapper, and is equivalent to: ``self.app.config.get(<section>, <key>)``.
        """
        return self.app.config.get(self._meta.config_section, key)

    def startup(self):
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
            storage_secret=self._config('storage_secret'),
            binding_refresh_interval=float(self._config('binding_refresh_interval')),
            reconnect_timeout=float(self._config('reconnect_timeout')),
            uvicorn_logging_level=self._config('logging_level'),
            # config fixed
            show=False,
            native=False,
            show_welcome_message=True,
            reload=False,
            uvicorn_reload_dirs=None,
            uvicorn_reload_includes=None,
            uvicorn_reload_excludes=None,
            prod_js=True,
            endpoint_documentation=None,
            on_air=None,
        )
        pass

    def shutdown(self):
        pass


class TokeoNiceguiController(Controller):

    class Meta:
        label = 'nicegui'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'start web frontend server'
        description = 'Start the tokeo nicegui webservice.'
        epilog = f'Example: {basename(argv[0])} nicegui serve --background'

    def _setup(self, app):
        super()._setup(app)

    @ex(
        help='serve the wbeserver service',
        description='Spin up the webserver.',
        arguments=[
            (
                ['--background'],
                dict(
                    action='store_true',
                    help='do not startup in interactive shell',
                ),
            ),
        ],
    )
    def serve(self):
        self.app.nicegui.startup()


def tokeo_nicegui_extend_app(app):
    app.extend('nicegui', TokeoNicegui(app))
    app.nicegui._setup(app)


def tokeo_nicegui_shutdown(app):
    app.nicegui.shutdown()


def load(app):
    app.handler.register(TokeoNiceguiController)
    app.hook.register('post_setup', tokeo_nicegui_extend_app)
    app.hook.register('pre_close', tokeo_nicegui_shutdown)
