from cement.utils.misc import init_defaults
from {{ app_label }}.main import {{ app_class_name }}Test


defaults = init_defaults('')


class {{ app_class_name }}TestApp({{ app_class_name }}Test):

    class Meta:
        # set framework extensions
        extensions = [
            'colorlog',
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
        ]


def test_{{ app_label }}():
    # test {{ app_label }} without any subcommands or arguments
    with {{ app_class_name }}TestApp(config_defaults=defaults) as app:
        app.run()
        assert app.exit_code == 0


def test_{{ app_label }}_debug():
    # test that debug mode is functional
    argv = ['--debug']
    with {{ app_class_name }}TestApp(argv=argv, config_defaults=defaults) as app:
        app.run()
        assert app.debug is True
