from {{ app_label }}.main import {{ app_class_name }}Test


def test_{{ app_label }}():
    # test {{ app_label }} without any subcommands or arguments
    with {{ app_class_name }}Test() as app:
        app.run()
        assert app.exit_code == 0


def test_{{ app_label }}_debug():
    # test that debug mode is functional
    argv = ['--debug']
    with {{ app_class_name }}Test(argv=argv) as app:
        app.run()
        assert app.debug is True
