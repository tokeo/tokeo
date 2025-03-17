from sys import argv
from os.path import basename
import importlib
import warnings
import pdoc
import re
from cement import ex
from cement.utils import fs
from cement.core.meta import MetaMixin
from tokeo.core.exc import TokeoError
from tokeo.ext.argparse import Controller
from tokeo.core.utils.controllers import controller_log_info_help


class TokeoPdocError(TokeoError):

    pass


class TokeoPdoc(MetaMixin):

    class Meta:

        #: Unique identifier for this handler
        label = 'tokeo.pdoc'

        #: Id for config
        config_section = 'pdoc'

        #: Dict with initial settings
        config_defaults = dict(
            modules=None,
            output_dir='html',
            host='127.0.0.1',
            port=9999,
            favicon=None,
            templates='tokeo.templates.pdoc.html',
        )

    def __init__(self, *args, **kw):
        super(TokeoPdoc, self).__init__(*args, **kw)
        self._docstrings = dict()
        docstrings_module = importlib.import_module('tokeo.templates.pdoc.docstrings')
        self._docstrings_dirs = [fs.abspath(docstrings_module.__path__[0])]

    def _setup(self, app):
        """
        Set up the TokeoPdoc extension.

        Args:
            app: The Cement application instance
        """
        self.app = app
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        # get values from config
        self._output_dir = self._config('output_dir')
        self._host = self._config('host')
        self._port = self._config('port')
        self._favicon = self._config('favicon')
        self._templates = self._config('templates')
        # identify modules to document
        if self._config('modules', fallback=None) is None:
            self._modules = [self.app._meta.label, 'tests', 'tokeo']
        elif isinstance(self._config('modules'), str):
            self._modules = self._config('modules').split(',')
        elif isinstance(self._config('modules'), (list, tuple)):
            self._modules = self._config('modules')
        else:
            raise TokeoPdocError('To define modules for rendering pdoc it must be from type str or list')
        # return self as reference
        return self

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

    def docstrings(self, group, identifier):
        # check if docstring is already cached
        if f'{group}/{identifier}' in self._docstrings:
            return self._docstrings[f'{group}/{identifier}']

        # try to get docstring from dirs and cache if found
        for dir in self._docstrings_dirs:
            try:
                with open(fs.join(dir, group, f'{identifier}.md'), 'r') as f:
                    docstring = f.read()
                    self._docstrings[f'{group}/{identifier}'] = docstring
                    return docstring
            except Exception:
                pass

        # not found
        return None

    def showwarning(self, message, *args, **kwargs):
        if re.match(r'Couldn\'t read PEP-224 variable docstrings', f'{message}', re.IGNORECASE):
            pass
        else:
            self.app.print(f'⚠️ {message}')

    def render(self):
        # when running pdoc do not catch signals
        self.app._meta.catch_signals = None
        # send out hook to prepare other modules for pdoc rendering
        for res in self.app.hook.run('tokeo_pdoc_pre_render', self.app):
            pass
        # save original show method
        warnings_showwarning = warnings.showwarning
        try:
            # do not show the default warnings
            warnings.showwarning = self.showwarning
            # change templates if given
            if self._templates is not None:
                # only use templates as set
                pdoc.tpl_lookup.directories.clear()
                try:
                    # try given string as module name
                    tpl_module = importlib.import_module(self._templates)
                except Exception:
                    # if not module then add as file path
                    pdoc.tpl_lookup.directories.append(fs.abspath(self._templates))
                else:
                    # append the directory from module as source for templates
                    pdoc.tpl_lookup.directories.append(fs.abspath(tpl_module.__path__[0]))

            # loop modules to generate documentation
            context = pdoc.Context()
            modules = [pdoc.Module(mod, context=context) for mod in self._modules]
            pdoc.link_inheritance(context)

            def recursive_htmls(mod):
                if mod.docstring is None or mod.docstring == '':
                    self.app.print(f'⚠️ Warning: Healing empty docstring for "{mod.name}"')
                    mod.docstring = f'Documentation of module "{mod.name}"'

                path = mod.name.split('.')
                page = 'index' if mod.obj.__file__ is None or basename(mod.obj.__file__).lower() == '__init__.py' else path.pop()

                yield mod.name, mod.html(app=self.app), fs.join(self._output_dir, *path), page
                for submod in mod.submodules():
                    yield from recursive_htmls(submod)

            for mod in modules:
                for module_name, html, path, page in recursive_htmls(mod):
                    fs.ensure_dir_exists(path)
                    with open(fs.join(path, f'{page}.html'), 'w', encoding='utf8') as f:
                        f.write(html)

            # Create a single base `index.html`
            with open(fs.join(self._output_dir, 'index.html'), 'w', encoding='utf-8') as f:
                f.write(
                    pdoc._render_template(
                        '/html.mako', app=self.app, modules=sorted((module.name, module.docstring) for module in modules)
                    )
                )

        finally:
            # restore saved show method
            warnings.showwarning = warnings_showwarning
            # send out hook to process other modules after pdoc rendering
            for res in self.app.hook.run('tokeo_pdoc_post_render', self.app):
                pass

    def startup(self):
        self.server.start()

    def shutdown(self):
        self.server.stop(0)

    def serve(self):
        self.startup()
        self.app.log.info('Http server started, listening on ' + self._config('url'))
        try:
            while True:
                self.server.wait_for_termination()
        except KeyboardInterrupt:
            self.shutdown()


class TokeoPdocController(Controller):

    class Meta:
        label = 'pdoc'
        stacked_type = 'nested'
        stacked_on = 'base'

        # disable the ugly curly command doubled listening
        subparser_options = dict(metavar='')

        # text displayed at the top of --help output
        description = 'Handle project pdoc documentation.'

        # text displayed at the bottom of --help output
        epilog = f'Example: {basename(argv[0])} pdoc render --output-dir html'

        # short help information
        help = 'pdoc documentation tool'

    def _setup(self, app):
        """
        Set up the controller.

        Args:
            app: The Cement application instance
        """
        super(TokeoPdocController, self)._setup(app)

    @ex(
        help='render the documentation',
        arguments=[],
    )
    def render(self):
        controller_log_info_help(self)
        self.app.pdoc.render()

    @ex(
        help='start http service',
        description='Spin up the http service.',
        arguments=[
            (
                ['--output-dir'],
                dict(
                    type=str,
                    action='store',
                    required=False,
                    default='html',
                    help='Url for the resource to get counted',
                ),
            ),
        ],
    )
    def serve(self):
        self.app.pdoc.serve()


def tokeo_pdoc_render_decorator(app, decorator, args, kwargs):
    """
    Handle docstrings for general decorators in pdoc
    """
    if decorator == '@contextmanager':
        return dict(
            decorator=decorator,
            params=None,
            docstring=app.pdoc.docstrings('decorator', 'contextmanager'),
        )
    elif decorator == '@ex' or decorator == '@expose':
        return dict(
            decorator=decorator,
            params=None,
            docstring=app.pdoc.docstrings('decorator', 'argparse.expose'),
        )


def tokeo_pdoc_extend_app(app):
    """
    Initialize and register the Pdoc extension with the application.

    Args:
        app: The Cement application instance.
    """
    app.extend('pdoc', TokeoPdoc(app))
    app.pdoc._setup(app)


def load(app):
    app.handler.register(TokeoPdocController)
    app.hook.define('tokeo_pdoc_pre_render')
    app.hook.define('tokeo_pdoc_post_render')
    app.hook.define('tokeo_pdoc_render_decorator')
    app.hook.register('post_setup', tokeo_pdoc_extend_app)
    app.hook.register('tokeo_pdoc_render_decorator', tokeo_pdoc_render_decorator)
