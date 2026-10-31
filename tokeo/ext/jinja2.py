"""
Tokeo Jinja2 Extension Module.

This extension provides enhanced Jinja2 templating capabilities to Tokeo
applications. It extends the base Cement Jinja2 functionality with additional
configuration options and improved template handling.

### Features

- Configures the Jinja2 environment (trim_blocks, keep_trailing_newline)
    from the 'jinja2' config section
- Resolves and registers template directories from configuration
- Registers as the application's default output and template handler,
    unless the application chose its own
- Binds tokeo's own 'tokeo.jinja2' template handler for all rendering

"""

from cement.ext.ext_jinja2 import Jinja2OutputHandler, Jinja2TemplateHandler
from cement.core.output import OutputHandler
from cement.core.template import TemplateHandler
from tokeo.core.exc import TokeoError
from tokeo.core.utils.validate import is_bool


class TokeoJinja2Error(TokeoError):
    """
    Raised for a jinja2 config that cannot be applied.

    ### Notes

    : Derives from TokeoError, so main reports it as a clean message

    """

    pass


class TokeoJinja2OutputHandler(Jinja2OutputHandler):
    """
    Enhanced Jinja2 output handler for Tokeo applications.

    This class implements the OutputHandler interface from Cement with
    Tokeo-specific enhancements for template rendering across all output channels.
    It ensures that the application uses the Tokeo Jinja2 template handler for
    all rendering operations.

    ### Notes

    - Inherits from Cement's Jinja2OutputHandler but uses Tokeo's template handler
    - Provides consistent template rendering experience across all output channels
    - Automatically configured when the extension is loaded
    - Renders text from a Jinja2 template; the output format is whatever the
        template produces
    - Cement resolves the template handler by the label of the output
        handler, so the label 'tokeo.jinja2' binds tokeo's template handler

    """

    class Meta(OutputHandler.Meta):
        """
        Handler meta-data configuration.
        """

        label = 'tokeo.jinja2'


class TokeoJinja2TemplateHandler(Jinja2TemplateHandler):
    """
    Enhanced Jinja2 template handler for Tokeo applications.

    This class implements the Template Handler interface from Cement with
    additional configuration options and enhanced directory handling for
    Tokeo applications. It provides a configurable Jinja2 environment
    with settings controlled through the application's configuration.

    ### Notes

    - Provides additional configuration options for template processing
    - Supports proper template directory resolution and management
    - Can be configured through the application's 'jinja2' config section
    - Exposes the Jinja2 environment for further customization

    """

    class Meta(TemplateHandler.Meta):
        """
        Handler meta-data configuration.
        """

        label = 'tokeo.jinja2'

        # Id for config
        config_section = 'jinja2'

        # Configuration default values
        config_defaults = {
            'template_dirs': None,
            'keep_trailing_newline': True,
            'trim_blocks': True,
        }

    def _setup(self, app):
        """
        Configure the template handler.

        Runs the base setup (app, config section and the merge of the config
        defaults), then applies keep_trailing_newline and trim_blocks from
        the configuration to the Jinja2 environment.

        ### Args

        - **app** (Application): The Cement application instance

        ### Raises

        - **TokeoJinja2Error**: If keep_trailing_newline or trim_blocks is
            not true or false

        """
        super()._setup(app)

        # use exposed Jinja2 Environment instance to manipulate it
        for key in ('keep_trailing_newline', 'trim_blocks'):
            value = self._config(key)
            if not is_bool(value):
                raise TokeoJinja2Error(f'{key} must be true or false, not {value!r}')
            setattr(self.env, key, value)

    def _config(self, key, **kwargs):
        """
        Get configuration value from the extension's config section.

        This is a convenient wrapper around the application's config.get method,
        accessing values from the extension's config section.

        ### Args

        - **key** (str): Configuration key to retrieve

        ### Keyword Args

        - **kwargs**: Additional arguments passed to config.get()

        ### Returns

        - **Any**: The configuration value for the specified key

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)


def tokeo_jinja2_config(app):
    """
    Configure Jinja2 for the Tokeo application.

    Sets up template directories from configuration and ensures proper
    initialization of the template handler. This function runs during
    the post_setup application hook.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    : Resolves the template handler with setup=True so it has access to the
        application and can read its own config; the handler instance is used
        only to look up template_dirs

    : Adds template directories from the configuration if specified

    : Template directories can be specified as a string or a list of strings
    """
    template_handler = app.handler.resolve('template', TokeoJinja2TemplateHandler.Meta.label, setup=True)

    # add template dirs
    t_dirs = template_handler._config('template_dirs', fallback=None)
    if t_dirs is not None:
        for t_dir in [t_dirs] if isinstance(t_dirs, str) else t_dirs:
            app.add_template_dir(t_dir)


def load(app):
    """
    Load the Jinja2 extension into a Tokeo application.

    Registers handlers and hooks needed for Jinja2 templating support.
    Sets this extension's handlers as the application's default output
    and template handlers, unless the application chose its own.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    - Registers the post_setup hook to configure Jinja2
    - Registers both the output and template handlers
    - Sets the Tokeo Jinja2 handlers as the application defaults, while
        the application has only the dummy handlers of cement

    """
    app.hook.register('post_setup', tokeo_jinja2_config)
    app.handler.register(TokeoJinja2OutputHandler)
    app.handler.register(TokeoJinja2TemplateHandler)
    # jinja2 becomes the default, a handler the app chose itself stays
    if app._meta.output_handler == 'dummy':
        app._meta.output_handler = TokeoJinja2OutputHandler.Meta.label
    if app._meta.template_handler == 'dummy':
        app._meta.template_handler = TokeoJinja2TemplateHandler.Meta.label
