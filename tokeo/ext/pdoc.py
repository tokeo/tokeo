"""
Tokeo Pdoc Extension Module.

This extension integrates the pdoc documentation generator with Tokeo
applications, providing a way to generate API documentation directly
from Python docstrings. It supports rendering documentation as HTML and
serving it via a built-in web server.

The extension handles custom docstring formatting, template customization, and
provides hooks for extensions to modify documentation rendering behavior.

### Features:

- **Automatic documentation** generation from module docstrings
- **Custom template** support for controlling documentation appearance
- **External docstring** handling for complex documentation needs
- **Warning filtering** to reduce noise during documentation generation
- **Web server** for browsing generated documentation
- **Inheritance linking** for proper class hierarchy documentation
- **Documentation hooks** for extending functionality in other modules

"""

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
    """
    Exception class for Pdoc extension errors.

    This class is used to raise and catch exceptions that are specific to
    the Tokeo Pdoc extension functionality.

    ### Notes:

    : Inherits from TokeoError to maintain consistent error handling

    : Used to indicate configuration or documentation generation issues

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
            templates='tokeo.templates.pdoc.html',
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
        self._docstrings = dict()
        docstrings_module = importlib.import_module('tokeo.templates.pdoc.docstrings')
        self._docstrings_dirs = [fs.abspath(docstrings_module.__path__[0])]

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
        - Displays other warnings to the user with a warning emoji prefix

        ### Output:

        : Prints warnings to the application's output with an emoji prefix

        """
        if re.match(r'Couldn\'t read PEP-224 variable docstrings', f'{message}', re.IGNORECASE):
            pass
        else:
            self.app.print(f'⚠️ {message}')

    def render(self):
        """
        Generate HTML documentation for the configured modules.

        This method performs the actual documentation generation process:

        1. Runs pre-render hooks to allow extensions to prepare
        1. Sets up custom warning handling and template directories
        1. Loads and processes all modules and their submodules
        1. Generates HTML output for each module
        1. Creates an index.html file
        1. Runs post-render hooks for cleanup

        ### Notes:

        - Automatically creates empty docstrings for modules that lack them
        - Generates both module documentation and a main index page
        - The output is written to the directory specified in configuration

        ### Output:

        1. Generates HTML files in the configured output directory
        1. Each module gets its own HTML file with proper directory structure

        """
        # when running pdoc do not catch signals
        self.app._meta.catch_signals = None
        # save original show method
        warnings_showwarning = warnings.showwarning
        try:
            # send out hook to prepare other modules for pdoc rendering
            for res in self.app.hook.run('tokeo_pdoc_pre_render', self.app):
                pass

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

            # send out hook to process other modules after pdoc rendering
            for res in self.app.hook.run('tokeo_pdoc_post_render', self.app):
                pass

        finally:
            # restore saved show method
            warnings.showwarning = warnings_showwarning

    def startup(self):
        """
        Start the documentation web server.

        Initializes and starts the HTTP server for serving the generated
        documentation. This method is non-blocking - the server runs in
        a separate thread.

        ### Notes:

        1. This method only starts the server but doesn't block execution
        1. Use the serve() method to start the server and block until interrupted
        1. The server serves files from the configured output directory

        ### Raises:

        - **TokeoPdocError**: If the server cannot be started

        """
        try:
            # Create server if not already created
            self.server.start()

        except Exception as err:
            raise TokeoPdocError(f'Failed to start documentation server: {err}')

    def shutdown(self):
        """
        Shut down the documentation web server.

        Stops the HTTP server immediately without waiting for ongoing
        connections to complete.

        ### Notes:

        1. Safely handles the case where the server was never started
        1. Cleans up server resources to prevent resource leaks

        """
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def serve(self):
        """
        Start the documentation web server and block until interrupted.

        This method:
        1. Starts the server
        2. Logs a message with the server URL
        3. Blocks until a keyboard interrupt (Ctrl+C) is received
        4. Shuts down the server cleanly

        ### Notes:

        - This method is blocking and is typically called from a CLI command
        - The server URL is determined from the host and port configuration

        """
        self.startup()
        self.app.log.info(f'Documentation server started, listening on {self._config("url")}')
        try:
            # This is a simple way to block until interrupted
            import time

            while True:
                time.sleep(15)
        except KeyboardInterrupt:
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
        arguments=[],
    )
    def render(self):
        """
        Generate HTML documentation.

        This command renders API documentation for the configured modules
        into HTML files in the specified output directory.

        """
        controller_log_info_help(self)
        # Generate the documentation
        self.app.pdoc.render()
        # Print success message
        self.app.log.info(f'Documentation generated in: {self.app.pdoc._output_dir}')

    @ex(
        help='start http service',
        description='Spin up an HTTP server to serve the generated documentation.',
        arguments=[
            (
                ['--output-dir'],
                dict(
                    type=str,
                    action='store',
                    required=False,
                    default='html',
                    help='Directory containing the HTML documentation',
                ),
            ),
        ],
    )
    def serve(self):
        """
        Start a web server to serve the documentation.

        This command starts an HTTP server that serves the generated
        documentation, making it accessible through a web browser.

        ### Args:

        - **--output-dir** (str): Directory containing the HTML documentation

        ### Notes:

        - The server host and port are configurable in the application config

        - The command blocks until interrupted with Ctrl+C

        ### Output:

        1. Logs the server URL when it starts

        1. Serves the documentation files via HTTP

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

    1. Currently handles the @contextmanager and @ex/@expose decorators

    1. For supported decorators, returns information about the decorator
      including its docstring loaded from external files

    1. Returns None for decorators that are not specifically handled

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

    1. This function is called during application setup

    1. It creates the TokeoPdoc instance and attaches it to the app
      as app.pdoc

    1. Called by the post_setup hook registered in the load function

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

    1. Registers the TokeoPdocController for CLI commands

    1. Defines extension-specific hooks for pre/post rendering

    1. Sets up decorator handling for improved documentation

    """
    app.handler.register(TokeoPdocController)
    app.hook.define('tokeo_pdoc_pre_render')
    app.hook.define('tokeo_pdoc_post_render')
    app.hook.define('tokeo_pdoc_render_decorator')
    app.hook.register('post_setup', tokeo_pdoc_extend_app)
    app.hook.register('tokeo_pdoc_render_decorator', tokeo_pdoc_render_decorator)
