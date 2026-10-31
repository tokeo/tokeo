"""
Tests for tokeo.ext.jinja2: the extension makes its handlers the output and
template handlers of the app, sets trim_blocks and keep_trailing_newline of
the Jinja2 environment from the jinja2 config section and adds the
directories of jinja2.template_dirs to the template dirs of the app.
Templates render from strings and from those directories.
"""

import pytest
from jinja2.exceptions import TemplateSyntaxError
from cement.core.exc import FrameworkError
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.ext.jinja2 import TokeoJinja2OutputHandler, TokeoJinja2TemplateHandler

# a block tag on its own line, followed by a line with a trailing newline
BLOCK = '{% if x %}\nyes\n{% endif %}\nend\n'


class Jinja2Test(TokeoTest):
    """A test app with tokeo.ext.jinja2 and the given jinja2 settings."""

    class Meta:
        extensions = [
            'tokeo.ext.jinja2',
        ]

    def __init__(self, settings=None, **kw):
        config = init_defaults('jinja2')
        config['jinja2'].update(settings or {})
        super().__init__(config_defaults=config, **kw)


class Jinja2YamlTest(Jinja2Test):
    """A test app with tokeo.ext.jinja2 and the config by tokeo.ext.yaml."""

    class Meta:
        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.jinja2',
        ]


def _templates(path, **files):
    # a directory at path with the given files, the name of a file with
    # .jinja2 as suffix; returns the directory as str for the config
    path.mkdir()
    for name, text in files.items():
        (path / f'{name}.jinja2').write_text(text)
    return str(path)


# load


def test_load_makes_tokeo_jinja2_the_output_and_template_handler():
    """The output and template handler of the app are those of tokeo."""
    with Jinja2Test() as app:
        assert app._meta.output_handler == app._meta.template_handler == 'tokeo.jinja2'
        assert isinstance(app.output, TokeoJinja2OutputHandler)
        assert isinstance(app.template, TokeoJinja2TemplateHandler)


# TokeoJinja2OutputHandler


def test_output_handler_renders_by_the_template_handler_of_tokeo():
    """The label tokeo.jinja2 binds the template handler of tokeo."""
    with Jinja2Test() as app:
        assert isinstance(app.output.templater, TokeoJinja2TemplateHandler)


def test_output_handler_renders_a_template_of_template_dirs(tmp_path):
    """hello.jinja2 renders with its data and the part it includes."""
    templates = _templates(tmp_path / 'templates', hello='Hello {{ name }}! {% include "part.jinja2" %}', part='[part]\n')
    with Jinja2Test({'template_dirs': templates}) as app:
        assert app.render({'name': 'World'}, 'hello.jinja2', out=None) == 'Hello World! [part]\n'


def test_output_handler_takes_the_config_of_jinja2(tmp_path):
    """With trim_blocks off the template of app.render keeps its newlines."""
    templates = _templates(tmp_path / 'templates', block=BLOCK)
    with Jinja2Test({'template_dirs': templates, 'trim_blocks': False}) as app:
        assert app.render({'x': 1}, 'block.jinja2', out=None) == '\nyes\n\nend\n'


def test_output_handler_missing_template_raises():
    """A template in none of the template dirs raises FrameworkError."""
    with Jinja2Test() as app:
        with pytest.raises(FrameworkError, match='Could not locate template: missing.jinja2'):
            app.render({}, 'missing.jinja2', out=None)


@pytest.mark.parametrize('template', [None, ''], ids=['none', 'empty'])
def test_output_handler_without_template_raises(template):
    """No template or an empty name raises FrameworkError."""
    with Jinja2Test() as app:
        with pytest.raises(FrameworkError, match='Invalid template path'):
            app.render({}, template, out=None)


# TokeoJinja2TemplateHandler


def test_template_handler_merges_its_defaults_into_jinja2():
    """The jinja2 section holds template_dirs None and both switches on."""
    with Jinja2Test() as app:
        assert app.config.get_section_dict('jinja2') == {
            'template_dirs': None,
            'keep_trailing_newline': True,
            'trim_blocks': True,
        }


@pytest.mark.parametrize(
    'settings, expected',
    [
        ({}, (True, True)),
        ({'trim_blocks': False}, (False, True)),
        ({'keep_trailing_newline': False}, (True, False)),
        ({'trim_blocks': False, 'keep_trailing_newline': False}, (False, False)),
    ],
    ids=['defaults', 'trim_blocks_off', 'keep_trailing_newline_off', 'both_off'],
)
def test_template_handler_setup_applies_the_config_to_the_env(settings, expected):
    """trim_blocks and keep_trailing_newline of the env follow the config."""
    with Jinja2Test(settings) as app:
        assert (app.template.env.trim_blocks, app.template.env.keep_trailing_newline) == expected


def test_template_handler_setup_takes_the_config_of_a_yaml_file(tmp_path):
    """trim_blocks: false of a yaml config file turns trim_blocks off."""
    config = tmp_path / 'config.yaml'
    config.write_text('jinja2:\n  trim_blocks: false\n')
    with Jinja2YamlTest(config_files=[str(config)]) as app:
        assert app.template.env.trim_blocks is False


def test_template_handler_render_trims_the_newline_after_a_block_tag():
    """By default the newline after {% if %} and {% endif %} is dropped."""
    with Jinja2Test() as app:
        assert app.template.render(BLOCK, {'x': 1}) == 'yes\nend\n'


def test_template_handler_render_keeps_the_newline_with_trim_blocks_off():
    """With trim_blocks off the newlines after the block tags stay."""
    with Jinja2Test({'trim_blocks': False}) as app:
        assert app.template.render(BLOCK, {'x': 1}) == '\nyes\n\nend\n'


def test_template_handler_render_drops_the_trailing_newline_when_configured():
    """With keep_trailing_newline off 'end\\n' renders as 'end'."""
    with Jinja2Test({'keep_trailing_newline': False}) as app:
        assert app.template.render('end\n', {}) == 'end'


def test_template_handler_render_invalid_template_raises():
    """A template with an incomplete tag raises TemplateSyntaxError."""
    with Jinja2Test() as app:
        with pytest.raises(TemplateSyntaxError):
            app.template.render('{% if %}', {})


# TokeoJinja2TemplateHandler._config


def test_config_reads_from_the_jinja2_section():
    """_config('trim_blocks') gives the value of jinja2.trim_blocks."""
    with Jinja2Test({'trim_blocks': False}) as app:
        assert app.template._config('trim_blocks') is False


def test_config_passes_the_fallback_on():
    """A key missing in jinja2 gives the fallback passed to _config."""
    with Jinja2Test() as app:
        assert app.template._config('missing', fallback='fallback') == 'fallback'


# tokeo_jinja2_config


def test_tokeo_jinja2_config_adds_a_template_dir_given_as_str(tmp_path):
    """A str in jinja2.template_dirs is added as template dir."""
    templates = _templates(tmp_path / 'templates')
    with Jinja2Test({'template_dirs': templates}) as app:
        assert app._meta.template_dirs == [templates]


def test_tokeo_jinja2_config_adds_template_dirs_given_as_list(tmp_path):
    """The dirs of a list in jinja2.template_dirs are added in their order."""
    templates = [_templates(tmp_path / 'one'), _templates(tmp_path / 'two')]
    with Jinja2Test({'template_dirs': templates}) as app:
        assert app._meta.template_dirs == templates


def test_tokeo_jinja2_config_adds_no_template_dir_for_none():
    """jinja2.template_dirs None adds no template dir."""
    with Jinja2Test() as app:
        assert app._meta.template_dirs == []


def test_tokeo_jinja2_config_dirs_come_after_the_template_dirs_of_the_app(tmp_path):
    """A template of the app's own template_dirs wins over one of jinja2."""
    own = _templates(tmp_path / 'own', hello='own')
    configured = _templates(tmp_path / 'configured', hello='configured')
    with Jinja2Test({'template_dirs': configured}, template_dirs=[own]) as app:
        assert app._meta.template_dirs == [own, configured]
        assert app.render({}, 'hello.jinja2', out=None) == 'own'


def test_tokeo_jinja2_config_takes_template_dirs_of_a_yaml_file(tmp_path):
    """A yaml list in jinja2.template_dirs is added as template dirs."""
    templates = [_templates(tmp_path / 'one'), _templates(tmp_path / 'two')]
    config = tmp_path / 'config.yaml'
    config.write_text('jinja2:\n  template_dirs:\n' + ''.join(f'    - {t}\n' for t in templates))
    with Jinja2YamlTest(config_files=[str(config)]) as app:
        assert app._meta.template_dirs == templates
