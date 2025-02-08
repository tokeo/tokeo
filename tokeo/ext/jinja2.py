from cement.ext.ext_jinja2 import Jinja2OutputHandler, Jinja2TemplateHandler
from cement.core.output import OutputHandler
from cement.core.template import TemplateHandler
from jinja2 import Environment


class TokeoJinja2OutputHandler(Jinja2OutputHandler):
    """
    This class implements the :ref:`OutputHandler <cement.core.output>`
    interface.  It provides text output from template and uses the
    `Jinja2 Templating Language
    <http://jinja.pocoo.org/>`_.
    Please see the developer documentation on
    :cement:`Output Handling <dev/output>`.

    """

    class Meta(OutputHandler.Meta):
        """Handler meta-data."""

        label = 'tokeo.jinja2'

    def _setup(self, app):
        super(Jinja2OutputHandler, self)._setup(app)
        self.templater = self.app.handler.resolve('template', 'tokeo.jinja2', setup=True)  # type: ignore


class TokeoJinja2TemplateHandler(Jinja2TemplateHandler):
    """
    This class implements the :ref:`Template <cement.core.template>` Handler
    interface.  It renders content as template, and supports copying entire
    source template directories using the
    `Jinja2 Templating Language <http://jinja.pocoo.org/>`_.  Please
    see the developer documentation on
    :cement:`Template Handling <dev/template>`.
    """

    class Meta(TemplateHandler.Meta):
        """Handler meta-data."""

        label = 'tokeo.jinja2'

        #: Id for config
        config_section = 'jinja2'

        #: Configuration default values
        config_defaults = {
            'keep_trailing_newline': True,
            'trim_blocks': True,
        }

    def __init__(self, *args, **kw):
        super(TokeoJinja2TemplateHandler, self).__init__(*args, **kw)

    def _setup(self, app):
        # save pointer to app
        self.app = app
        # prepare the config
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        # use exposed Jinja2 Environment instance to manipulate it
        self.env.keep_trailing_newline = self.app.config.get(self._meta.config_section, 'keep_trailing_newline')
        self.env.trim_blocks = self.app.config.get(self._meta.config_section, 'trim_blocks')


def tokeo_jinja2_extend_app(app):
    template_handler = app.handler.resolve('template', TokeoJinja2TemplateHandler.Meta.label)
    template_handler._setup(app)


def load(app):
    app.hook.register('post_setup', tokeo_jinja2_extend_app)
    app.handler.register(TokeoJinja2OutputHandler)
    app.handler.register(TokeoJinja2TemplateHandler)
    app._meta.output_handler = TokeoJinja2OutputHandler.Meta.label
    app._meta.template_handler = TokeoJinja2TemplateHandler.Meta.label
