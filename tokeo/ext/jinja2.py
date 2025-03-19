"""
Tokeo Jinja2 Extension Module.

This extension provides enhanced Jinja2 templating capabilities to Tokeo
applications. It extends the base Cement Jinja2 functionality with additional
configuration options and improved template handling.

### Features:

- **Enhanced environment configuration** with custom options like trim_blocks
- **Improved template directory** handling and discovery
- **Configurable through application settings** with sensible defaults
- **Automatic registration** as the default output and template handlers
- **Proper inheritance** from Cement's handlers with Tokeo-specific enhancements
- **Consistent rendering** across all output channels

"""

from cement.ext.ext_jinja2 import Jinja2OutputHandler, Jinja2TemplateHandler
from cement.core.output import OutputHandler
from cement.core.template import TemplateHandler
from jinja2 import Environment  # noqa: F401


class TokeoJinja2OutputHandler(Jinja2OutputHandler):
    """
    Enhanced Jinja2 output handler for Tokeo applications.

    This class implements the OutputHandler interface from Cement with
    Tokeo-specific enhancements for template rendering across all output channels.
    It ensures that the application uses the Tokeo Jinja2 template handler for
    all rendering operations.

    ### Notes:

    - Inherits from Cement's Jinja2OutputHandler but uses Tokeo's template handler
    - Provides consistent template rendering experience across all output channels
    - Automatically configured when the extension is loaded
    - Used for rendering output in various formats (json, yaml, etc.)

    """

    class Meta(OutputHandler.Meta):
        """
        Handler meta-data configuration.
        """

        label = 'tokeo.jinja2'

    def _setup(self, app):
        """
        Configure the output handler.

        Initializes the handler and ensures it uses Tokeo's template handler
        for rendering templates. This override ensures proper integration
        with the Tokeo Jinja2 template handler.

        ### Args:

        - **app** (Application): The Cement application instance

        """
        super(Jinja2OutputHandler, self)._setup(app)
        self.templater = self.app.handler.resolve(
            # ftm: skip
            'template',
            'tokeo.jinja2',
            setup=True,
        )


class TokeoJinja2TemplateHandler(Jinja2TemplateHandler):
    """
    Enhanced Jinja2 template handler for Tokeo applications.

    This class implements the Template Handler interface from Cement with
    additional configuration options and enhanced directory handling for
    Tokeo applications. It provides a configurable Jinja2 environment
    with settings controlled through the application's configuration.

    ### Notes:

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

    def __init__(self, *args, **kw):
        """
        Initialize the template handler.
        """
        super(TokeoJinja2TemplateHandler, self).__init__(*args, **kw)

    def _setup(self, app):
        """
        Configure the template handler.

        Sets up the Jinja2 environment with application-specific settings
        from configuration, applying options like keep_trailing_newline and
        trim_blocks to the environment.

        ### Args:

        - **app** (Application): The Cement application instance

        """
        # save pointer to app
        self.app = app

        # prepare the config
        self.app.config.merge(
            # fmt: skip
            {self._meta.config_section: self._meta.config_defaults},
            override=False,
        )

        # use exposed Jinja2 Environment instance to manipulate it
        self.env.keep_trailing_newline = self._config('keep_trailing_newline')
        self.env.trim_blocks = self._config('trim_blocks')

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


def tokeo_jinja2_config(app):
    """
    Configure Jinja2 for the Tokeo application.

    Sets up template directories from configuration and ensures proper
    initialization of the template handler. This function runs during
    the post_setup application hook.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    : This function addresses a limitation in Cement by ensuring the template
      handler is properly initialized and has access to the application

    : Adds template directories from the configuration if specified

    : Template directories can be specified as a string or a list of strings
    """
    template_handler = app.handler.resolve(
        # fmt: skip
        'template',
        TokeoJinja2TemplateHandler.Meta.label,
    )

    # as long as patch is missing from cement
    # we need to make sure that the object has app
    template_handler._setup(app)

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
    and template handlers.

    ### Args:

    - **app** (Application): The Cement application instance

    ### Notes:

    1. Registers the post_setup hook to configure Jinja2
    1. Registers both the output and template handlers
    1. Sets the Tokeo Jinja2 handlers as the application defaults

    """
    app.hook.register('post_setup', tokeo_jinja2_config)
    app.handler.register(TokeoJinja2OutputHandler)
    app.handler.register(TokeoJinja2TemplateHandler)
    app._meta.output_handler = TokeoJinja2OutputHandler.Meta.label
    app._meta.template_handler = TokeoJinja2TemplateHandler.Meta.label
