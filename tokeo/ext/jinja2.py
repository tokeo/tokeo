"""
Tokeo Jinja2 Extension Module.

This extension provides enhanced Jinja2 templating capabilities to Tokeo
applications. It extends the base Cement Jinja2 functionality with additional
configuration options and improved template handling.

Example:
    To use this extension in your application:

    .. code-block:: python

        from tokeo.app import TokeoApp

        with TokeoApp('myapp', extensions=['tokeo.ext.jinja2']) as app:
            # App now has access to enhanced Jinja2 template handling
            app.render('my_template.j2', {'key': 'value'})
"""

from cement.ext.ext_jinja2 import Jinja2OutputHandler, Jinja2TemplateHandler
from cement.core.output import OutputHandler
from cement.core.template import TemplateHandler
from jinja2 import Environment  # noqa: F401


class TokeoJinja2OutputHandler(Jinja2OutputHandler):
    """
    Enhanced Jinja2 output handler for Tokeo applications.

    This class implements the OutputHandler interface from Cement. It provides
    text output from templates using the Jinja2 templating language with
    Tokeo-specific enhancements.

    The handler integrates with Tokeo's template handling system to provide a
    consistent template rendering experience across all output channels.
    """

    class Meta(OutputHandler.Meta):
        """
        Handler meta-data configuration.

        Attributes:
            label (str): The identifier for this handler.
        """

        label = 'tokeo.jinja2'

    def _setup(self, app):
        """
        Configure the output handler.

        Args:
            app: The application object.
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

    This class implements the Template Handler interface from Cement. It
    renders content as templates and supports copying entire source template
    directories using the Jinja2 templating language.

    The handler provides additional configuration options for template
    processing and enhanced directory handling for Tokeo applications.
    """

    class Meta(TemplateHandler.Meta):
        """
        Handler meta-data configuration.

        Attributes:
            label (str): The identifier for this handler.
            config_section (str): Id for configuration section.
            config_defaults (dict): Default configuration values.
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

        Args:
            *args: Variable length argument list.
            **kw: Arbitrary keyword arguments.
        """
        super(TokeoJinja2TemplateHandler, self).__init__(*args, **kw)

    def _setup(self, app):
        """
        Configure the template handler.

        Sets up the Jinja2 environment with application-specific settings
        from configuration.

        Args:
            app: The application object.
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
        Get configuration value.

        This is a simple wrapper around the application's config.get method.

        Args:
            key (str): Configuration key to retrieve.
            **kwargs: Additional arguments passed to config.get().

        Returns:
            The configuration value for the specified key.
        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)


def tokeo_jinja2_config(app):
    """
    Configure Jinja2 for the Tokeo application.

    Sets up template directories from configuration and ensures proper
    initialization of the template handler.

    Args:
        app: The application object.
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

    Args:
        app: The application object.
    """
    app.hook.register('post_setup', tokeo_jinja2_config)
    app.handler.register(TokeoJinja2OutputHandler)
    app.handler.register(TokeoJinja2TemplateHandler)
    app._meta.output_handler = TokeoJinja2OutputHandler.Meta.label
    app._meta.template_handler = TokeoJinja2TemplateHandler.Meta.label
