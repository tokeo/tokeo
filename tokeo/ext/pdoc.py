"""
Tokeo Pdoc Extension Module.

Wraps pdoc behind a Tokeo extension: a renderer that walks configured
modules, applies a Tokeo-specific config.mako, plugs in custom decorator
docstrings via external markdown files, and an HTTP server controller to
browse the generated HTML during development.

### Features:

- Module list resolution from config or the conventional triple
    (app label, tests, tokeo) when modules is unset
- External docstring source: markdown files under templates/pdoc/docstrings
    keyed by `{group}/{identifier}` so decorator and extension docs can
    live outside the source they describe
- Warning filter that drops the noisy PEP-224 variable-docstring warning
    and lifts pdoc 'Error: ...' warnings to a visible ❗ line
- Healing pass for modules that pdoc imported with skip_errors=True: a
    missing __file__ or empty docstring would crash mod.html() later, so
    a stub is filled in and reported as ⚠️
- Config-files rendering: walks base + each ENVIRONMENT (with optional
    .local suffix) and renders them into the docs as a config index
- pdoc render hooks (pre/post + per-decorator) so other extensions can
    inject their own decorator documentation without editing this module

"""

from sys import argv
from os.path import basename, isdir, isfile, relpath
import shutil
import warnings
import pdoc
import socketserver
import http.server
import threading
from mako.template import Template
import re
import time
import yaml
from cement import ex
from cement.utils import fs
from cement.core.meta import MetaMixin
from cement.core.foundation import SIGNALS
from cement.core.exc import CaughtSignal
from tokeo.core.exc import TokeoError
from tokeo.ext.argparse import Controller
from tokeo.ext.appenv import ENVIRONMENTS
from tokeo.core.utils.controllers import controller_log_info_help
from tokeo.core.utils.modules import get_module_path


class TokeoPdocError(TokeoError):
    """
    Exception class for Pdoc extension errors.

    This class is used to raise and catch exceptions that are specific to
    the Tokeo Pdoc extension functionality.

    ### Notes:

    - Inherits from TokeoError to maintain consistent error handling
    - Used to indicate configuration or documentation generation issues

    """

    pass


class TokeoPdoc(MetaMixin):
    """
    Main handler for pdoc documentation generation in Tokeo applications.

    This class provides functionality for generating HTML documentation from
    Python source code docstrings using the pdoc library. It supports custom
    templates, docstring loading from external files, and a built-in web server.

    ### Notes:

    - Automatically generates documentation for the application module,
        tests, and tokeo
    - Supports custom docstring templates for extensions and decorators
    - Handles filtering of common warning messages
    - Provides a web server for browsing generated documentation

    """

    class Meta:
        """
        Extension meta-data and configuration defaults.

        ### Notes:

        - The config section is 'pdoc' in the application configuration
        - Default configuration provides reasonable starting values
            for most applications
        - All settings can be overridden in the application's configuration

        """

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
            templates=['tokeo.templates.pdoc.html'],
        )

    def __init__(self, *args, **kw):
        """
        Initialize the pdoc handler.

        Sets up the docstring cache and loads the default docstring templates.

        ### Args:

        - ***args**: Positional arguments passed to the parent class
        - ****kw**: Keyword arguments passed to the parent class

        """
        super(TokeoPdoc, self).__init__(*args, **kw)
        self._docstrings_cache = dict()
        self._docstrings_dirs = [get_module_path('tokeo.templates.pdoc.docstrings')]
        # http server
        self._http_server = None
        self._http_server_thread = None
        self._http_server_running = False

    def _setup(self, app):
        """
        Set up the TokeoPdoc extension.

        Initializes the extension with application configuration settings
        and determines which modules to document.

        ### Args:

        - **app** (Application): The Cement application instance

        ### Returns:

        - **TokeoPdoc**: Self reference for method chaining

        ### Raises:

        - **TokeoPdocError**: If the modules configuration is invalid

        """
        self.app = app
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        # get values from config
        self._output_dir = self._config('output_dir')
        self._host = self._config('host')
        self._port = int(self._config('port'))
        self._favicon = self._config('favicon')
        self._templates = self._config('templates')
        # identify modules to document and unique them
        if self._config('modules', fallback=None) is None:
            mods = [self.app._meta.label, 'tests', 'tokeo']
        elif isinstance(self._config('modules'), str):
            mods = self._config('modules').split(',')
        elif isinstance(self._config('modules'), (list, tuple)):
            mods = self._config('modules')
        else:
            raise TokeoPdocError('To define modules for rendering pdoc it must be from type str or list')
        # make list unique but ordered by given list
        self._modules = []
        for mod in mods:
            if mod not in self._modules:
                self._modules.append(mod)
        # rewrite the pdoc._get_config to primary load from tokeo
        pdoc._get_config = _get_config
        # return self as reference
        return self

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

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def docstrings(self, group, identifier):
        """
        Retrieve a docstring from external Markdown files.

        Loads docstrings from external .md files organized by group and identifier.
        Results are cached for repeated access. This allows for maintaining complex
        docstrings in dedicated files rather than embedding them in code.

        ### Args:

        - **group** (str): The group category for the docstring (e.g., 'decorator')
        - **identifier** (str): The specific identifier within the group

        ### Returns:

        - **str|None**: The docstring content if found, or None if not found

        ### Notes:

        - Searches in all registered docstrings directories for a matching file
            with the path pattern `{dir}/{group}/{identifier}.md`
        - Caches results to avoid repeated file system access
        - Returns None if no matching docstring file is found

        """
        # check if docstring is already cached
        if f'{group}/{identifier}' in self._docstrings_cache:
            return self._docstrings_cache[f'{group}/{identifier}']

        # try to get docstring from dirs and cache if found
        for dir in self._docstrings_dirs:
            try:
                with open(fs.join(dir, group, f'{identifier}.md'), 'r') as f:
                    docstring = f.read()
                    self._docstrings_cache[f'{group}/{identifier}'] = docstring
                    return docstring
            except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
                # file not in this dir, try the next one; other I/O errors
                # (permissions, decode failures) propagate as real bugs
                pass

        # not found
        return None

    def showwarning(self, message, *args, **kwargs):
        """
        Custom warning handler for pdoc documentation generation.

        Filters out common noisy warnings during the documentation generation
        process while still displaying important warnings to the user.

        ### Args:

        - **message** (str): The warning message
        - ***args**: Additional warning information
        - ****kwargs**: Keyword arguments for the warning handler

        ### Notes:

        - Silently ignores PEP-224 variable docstring warnings
        - Lifts pdoc 'Error: ...' warnings to a visible ❗ line; other
            warnings get a ⚠️ prefix

        """
        if re.match(r'Couldn\'t read PEP-224 variable docstrings', f'{message}', re.IGNORECASE):
            pass
        elif re.match(r'Error', f'{message}', re.IGNORECASE):
            self.app.print(f'❗{message}')
        else:
            self.app.print(f'⚠️ {message}')

    def render(self, clean=False):
        """
        Generate HTML documentation for the configured modules.

        This method performs the actual documentation generation process:

        1. Runs pre-render hooks to allow extensions to prepare
        1. Sets up custom warning handling and template directories
        1. Loads and processes all modules and their submodules
        1. Removes existing output-dir before new render
        1. Generates HTML output for each module
        1. Creates an index.html file
        1. Runs post-render hooks for cleanup

        ### Notes:

        - Heals modules that lack __file__ or a docstring before rendering
            so pdoc.html() does not crash downstream
        - Generates both per-module pages and a main index.html
        - The output is written to the directory configured under
            pdoc.output_dir; --clean removes the dir before rendering

        """
        # save original show method
        warnings_showwarning = warnings.showwarning
        try:
            # get the pdoc config from config.mako
            pdoc_config = pdoc._get_config()

            # send out hook to prepare other modules for pdoc rendering
            for res in self.app.hook.run('tokeo_pdoc_pre_render', self.app):
                pass

            # do not show the default warnings
            warnings.showwarning = self.showwarning
            # change templates if given
            if self._templates is not None:
                # only use templates as set, so clear dirs
                # and all currently cached
                pdoc.tpl_lookup.directories.clear()
                pdoc.tpl_lookup._collection.clear()
                pdoc.tpl_lookup._uri_cache.clear()
                for template in self._templates:
                    try:
                        # try given string as module name
                        tpl_module_path = get_module_path(template)
                    except Exception:
                        # if not module then add as file path
                        pdoc.tpl_lookup.directories.append(fs.abspath(template))
                    else:
                        # append the directory from module as source for templates
                        pdoc.tpl_lookup.directories.append(tpl_module_path)

            # loop modules to generate documentation
            context = pdoc.Context()
            # skip_errors=True lets pdoc tolerate failures *inside* a
            # module's traversal (broken submodules, syntax errors in
            # imports etc.), but the Module constructor itself can still
            # raise (ModuleNotFoundError on the top-level name); the outer
            # try/except handles that and drops the entire module, the
            # remaining ones are still rendered
            modules = []
            for mod in self._modules:
                try:
                    pmod = pdoc.Module(mod, context=context, skip_errors=True)
                    modules.append(pmod)
                except Exception as e:
                    # drop the module but make the loss debuggable;
                    # bare except would also swallow KeyboardInterrupt
                    self.app.log.debug(
                        f'pdoc.Module("{mod}") raised, skipping: {e}'
                    )
            pdoc.link_inheritance(context)

            def recursive_htmls(mod):
                # pdoc imports with skip_errors=True can leave a module
                # without __file__ on its obj (e.g. when the import raised
                # but pdoc kept the shell); mod.html() would crash on that,
                # so we synthesize __file__ and a docstring to keep the
                # render going. The user sees ⚠️ markers per module so the
                # gap in real docs is visible
                if not hasattr(mod.obj, '__file__'):
                    self.app.print(f'⚠️ Warning: Healing import error for "{mod.name}"')
                    mod.docstring = f'.. error:: Module "{mod.name}" had errors when processed by pdoc'
                    mod.obj.__file__ = mod.name

                if mod.docstring is None or mod.docstring == '':
                    self.app.print(f'⚠️ Warning: Healing empty docstring for "{mod.name}"')
                    mod.docstring = f'Documentation of module "{mod.name}"'

                path = mod.name.split('.')
                page = 'index' if mod.obj.__file__ is None or basename(mod.obj.__file__).lower() == '__init__.py' else path.pop()

                yield mod.name, mod.html(app=self.app), fs.join(self._output_dir, *path), page
                for submod in mod.submodules():
                    yield from recursive_htmls(submod)

            if clean:
                self.app.log.info('Cleaning output-dir before rendering...')
                shutil.rmtree(self._output_dir, ignore_errors=True)

            for mod in modules:
                for module_name, html, path, page in recursive_htmls(mod):
                    fs.ensure_dir_exists(path)
                    with open(fs.join(path, f'{page}.html'), 'w', encoding='utf8') as f:
                        f.write(html)

            # Create a single base `index.html`
            with open(fs.join(self._output_dir, 'index.html'), 'w', encoding='utf-8') as f:
                f.write(pdoc._render_template('/html.mako', app=self.app, modules=((module.name, module.docstring) for module in modules)))

            # Create the config documentation
            if hasattr(self.app, 'env') and pdoc_config['show_config']:
                # use appenv's resolver so the documented file list is
                # exactly what cement loads at boot: top-level <env>
                # config, then <env>.d/** sorted by lex+num, then .local
                # overrides appended. For non-base envs we additionally
                # splice in the top-level {env}.local{suffix} which
                # appenv currently does not pick up (it globs .local
                # only inside {env}.d/), but tokeo convention also has
                # standalone local overrides next to the env file
                #
                # section key is the path relative to APP_CONFIG_DIR
                # without the config_file_suffix, which preserves the
                # historic short names ('base', 'development',
                # 'development.local') and extends them naturally for
                # .d/ partials ('base.d/cache', 'development.d/db.local')
                suffix = self.app._meta.config_file_suffix
                config_dir = self.app.env.APP_CONFIG_DIR
                configdict = dict()
                for env in ('base', *ENVIRONMENTS):
                    file_list = self.app.env.get_config_files(
                        app_env=env, app_config_file_suffix=suffix,
                    )
                    if env != 'base':
                        local_top = fs.join(config_dir, f'{env}.local{suffix}')
                        if isfile(local_top) and local_top not in file_list:
                            # right after the env top-level entry
                            # (always index 0 from get_config_files)
                            file_list.insert(1, local_top)
                    for filename in file_list:
                        # top-level file is appended unconditionally by
                        # get_config_files; skip what really isn't there
                        if not isfile(filename):
                            continue
                        section = relpath(filename, config_dir)
                        if section.endswith(suffix):
                            section = section[:-len(suffix)]
                        try:
                            with open(filename, 'r') as f:
                                configcontent = f.read()
                                configyaml = yaml.safe_load(configcontent)
                                configdict[section] = dict(
                                    content=configcontent.split('\n'),
                                    yaml=configyaml,
                                )

                        except Exception as err:
                            self.app.print(f'❗Error: Processing config file "{section}": {err}')

                # Create a single base `config/index.html`
                fs.ensure_dir_exists(fs.join(self._output_dir, 'config'))
                with open(fs.join(self._output_dir, 'config', 'index.html'), 'w', encoding='utf-8') as f:
                    f.write(pdoc._render_template('/html.mako', app=self.app, configdict=configdict))

            # Copy assets into output dir
            try:
                for tpl_dir in reversed(pdoc.tpl_lookup.directories):
                    assets_dir = fs.join(tpl_dir, 'assets')
                    if isdir(assets_dir):
                        shutil.copytree(assets_dir, fs.join(self._output_dir, 'assets'), dirs_exist_ok=True)
            except Exception as err:
                # ignore the exception but log
                self.app.log.error(err)

            # send out hook to process other modules after pdoc rendering
            for res in self.app.hook.run('tokeo_pdoc_post_render', self.app):
                pass

        finally:
            # restore saved show method
            warnings.showwarning = warnings_showwarning

    def _run_http_server(self):
        """
        Thread target that runs the HTTP server's blocking loop.

        Lives in its own method because serve_forever() blocks; startup()
        spawns it on a daemon thread so the main flow stays responsive.
        A KeyboardInterrupt in this thread is swallowed (the signal goes
        to the main thread which calls shutdown), other exceptions are
        logged as errors.

        """
        try:
            self._http_server.serve_forever()
        except KeyboardInterrupt:
            pass
        except Exception as err:
            self.app.log.error(f'Error in Tokeo pdoc server: {err}')

    def startup(self):
        """
        Start the documentation web server.

        Initializes and starts the HTTP server for serving the generated
        documentation. This method is non-blocking - the server runs in
        a separate thread.

        ### Notes:

        - This method only starts the server but doesn't block execution
        - Use the serve() method to start the server and block until interrupted
        - The server serves files from the configured output directory

        ### Raises:

        - **TokeoPdocError**: If the server cannot be started

        """
        if self._http_server_running:
            self.app.log.warning('Tokeo pdoc server is already running')
            return

        try:
            # Create a custom handler that serves from the html directory
            handler = http.server.SimpleHTTPRequestHandler
            html_dir = self._output_dir

            class CustomHandler(handler):

                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=html_dir, **kwargs)

                def end_headers(self):
                    # Add caching headers for the assets directory
                    if self.path.startswith('/assets/'):
                        # Send cache control headers before content type
                        self.send_header('Cache-Control', 'max-age=600, public')
                    super().end_headers()

            # Start the server in a separate thread
            self._http_server = socketserver.TCPServer((self._host, self._port), CustomHandler)
            self._http_server_thread = threading.Thread(target=self._run_http_server)
            self._http_server_thread.daemon = True
            self._http_server_thread.start()

            self._http_server_running = True
            self.app.log.info(f'Tokeo pdoc server started at http://{self._host}:{self._port}')

        except Exception as err:
            raise TokeoPdocError(f'Failed to start Tokeo pdoc server: {err}')

    def shutdown(self):
        """
        Shut down the documentation web server.

        Stops the HTTP server immediately without waiting for ongoing
        connections to complete.

        ### Notes:

        - Safely handles the case where the server was never started
        - Cleans up server resources to prevent resource leaks

        """
        if self._http_server_running and self._http_server:
            self.app.log.info('Shutting down Tokeo pdoc server...')
            self._http_server_running = False
            self._http_server.shutdown()
            if self._http_server_thread:
                self._http_server_thread.join()
            self.app.log.info('Tokeo pdoc server was shut down')

    def serve(self):
        """
        Start the documentation web server and block until interrupted.

        This method:
        1. Starts the server
        1. Logs a message with the server URL
        1. Blocks until a keyboard interrupt (Ctrl+C) is received
        1. Shuts down the server cleanly

        ### Notes:

        - This method is blocking and is typically called from a CLI command
        - The server URL is determined from the host and port configuration

        """
        self.startup()
        try:
            # This is a simple way to block until interrupted
            while True:
                time.sleep(2.5)
        except KeyboardInterrupt:
            # defensive: with Cement's default catch_signals SIGINT is
            # turned into CaughtSignal before it surfaces as a Python
            # KeyboardInterrupt, so this branch should not trigger in
            # practice; kept as a safety net against future signal-
            # handling changes (in cement or in tokeo)
            pass
        except CaughtSignal as err:
            # normal Ctrl+C / SIGTERM / SIGHUP path; release the listener
            # socket and emit the shutdown log line
            self.app.print()
            if err.signum in SIGNALS:
                self.shutdown()


class TokeoPdocController(Controller):
    """
    Controller for pdoc documentation commands.

    This controller provides command-line commands for generating and serving
    documentation using the pdoc extension.

    ### Notes:

    : Provides commands for both static documentation generation and
        serving documentation via a web server

    """

    class Meta:
        label = 'pdoc'
        stacked_type = 'nested'
        stacked_on = 'base'

        # disable the ugly curly command doubled listing
        subparser_options = dict(metavar='')

        # text displayed at the top of --help output
        description = 'Handle project pdoc documentation.'

        # text displayed at the bottom of --help output
        epilog = f'Example: {basename(argv[0])} pdoc render --output-dir html'

        # short help information
        help = 'pdoc documentation tool'

    def _setup(self, app):
        """
        Set up the controller with the application.

        ### Args:

        - **app** (Application): The Cement application instance

        """
        super(TokeoPdocController, self)._setup(app)

    @ex(
        help='render the documentation',
        description='Generate HTML documentation from Python docstrings.',
        arguments=[
            (
                ['--clean'],
                dict(
                    action='store_true',
                    help='delete output-dir recursively before rendering',
                ),
            ),
            (
                ['--serve'],
                dict(
                    action='store_true',
                    help='serve the documentation after rendering',
                ),
            ),
        ],
    )
    def render(self):
        """
        Generate HTML documentation.

        This command renders API documentation for the configured modules
        into HTML files in the specified output directory.

        """
        controller_log_info_help(self)
        # Generate the documentation
        self.app.pdoc.render(clean=self.app.pargs.clean)
        # Print success message
        self.app.log.info(f'Documentation generated in: {self.app.pdoc._output_dir}')
        # Start server
        if self.app.pargs.serve:
            self.app.pdoc.serve()

    @ex(
        help='start http service',
        description='Spin up an HTTP server to serve the generated documentation.',
        arguments=[],
    )
    def serve(self):
        """
        Start a web server to serve the documentation.

        This command starts an HTTP server that serves the generated
        documentation, making it accessible through a web browser.

        ### Notes:

        - The server host and port are configurable in the application config
        - The configs can also be set by env vars like MYAPP_PDOC_PORT
        - The command blocks until interrupted with Ctrl+C
        - Logs the server URL once the listener is up

        """
        # Start the server
        self.app.pdoc.serve()


def tokeo_pdoc_render_decorator(app, func, decorator, args, kwargs):
    """
    Handle docstrings for general decorators in pdoc.

    This function provides custom handling for specific decorators
    during pdoc documentation rendering. It extracts information from
    the decorator and retrieves appropriate docstrings.

    ### Args:

    - **app** (Application): The Cement application instance
    - **func** (function): The function being decorated
    - **decorator** (str): The decorator string
    - **args** (list): Positional arguments to the decorator
    - **kwargs** (dict): Keyword arguments to the decorator

    ### Returns:

    - **dict|None**: Dictionary with decorator information or None if not handled

    ### Notes:

    - Currently handles the @contextmanager and @ex/@expose decorators
    - For supported decorators, returns information about the decorator
        including its docstring loaded from external files
    - Returns None for decorators that are not specifically handled

    """
    if decorator == '@contextmanager':
        return dict(
            decorator=decorator,
            params=None,
            docstring=app.pdoc.docstrings('decorator', 'contextmanager'),
        )
    elif decorator == '@ex' or decorator == '@expose':
        return dict(
            decorator='@expose',
            params=None,
            docstring=app.pdoc.docstrings('decorator', 'argparse.expose'),
        )


def tokeo_pdoc_extend_app(app):
    """
    Initialize and register the Pdoc extension with the application.

    This function creates a TokeoPdoc instance and attaches it to the
    application, making it available as app.pdoc.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    - This function is called during application setup
    - It creates the TokeoPdoc instance and attaches it to the app
        as app.pdoc
    - Called by the post_setup hook registered in the load function

    """
    app.extend('pdoc', TokeoPdoc(app))
    app.pdoc._setup(app)


def load(app):
    """
    Load the Pdoc extension into a Tokeo application.

    This function is called by Cement when loading extensions. It registers
    the controller, defines hooks, and sets up initialization handlers for
    pdoc integration.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    - Registers the TokeoPdocController for CLI commands
    - Defines extension-specific hooks for pre/post rendering
    - Sets up decorator handling for improved documentation

    """
    app.handler.register(TokeoPdocController)
    app.hook.define('tokeo_pdoc_pre_render')
    app.hook.define('tokeo_pdoc_post_render')
    app.hook.define('tokeo_pdoc_render_decorator')
    app.hook.register('post_setup', tokeo_pdoc_extend_app)
    app.hook.register('tokeo_pdoc_render_decorator', tokeo_pdoc_render_decorator)


def _get_config(**kwargs):
    """
    This is an overload function from original pdoc.__init__.py (_get_config).

    The DEFAULT_CONFIG is not changeable by API and the pdoc.tpl_lookup.get_template
    function will only returns the first other config.mako. So instead of repeating
    all values to newly created app, here overload and set tokeo as primary.

    """
    # Apply config.mako configuration
    MAKO_INTERNALS = Template('').module.__dict__.keys()
    DEFAULT_CONFIG = fs.join(get_module_path('tokeo.templates.pdoc.html'), 'config.mako')
    config = {}
    for config_module in (Template(filename=DEFAULT_CONFIG).module, pdoc.tpl_lookup.get_template('/config.mako').module):
        config.update((var, getattr(config_module, var, None)) for var in config_module.__dict__ if var not in MAKO_INTERNALS)

    known_keys = (
        set(config)
        | {'docformat'}  # Feature. https://github.com/pdoc3/pdoc/issues/169
        # deprecated
        | {'module', 'modules', 'http_server', 'external_links', 'search_query'}
    )
    invalid_keys = {k: v for k, v in kwargs.items() if k not in known_keys}
    if invalid_keys:
        warnings.warn(f'Unknown configuration variables (not in config.mako): {invalid_keys}')
    config.update(kwargs)

    if 'search_query' in config:
        warnings.warn('Option `search_query` has been deprecated. Use `google_search_query` instead', DeprecationWarning, stacklevel=2)
        config['google_search_query'] = config['search_query']
        del config['search_query']

    return config
