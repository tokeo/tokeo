"""
Tests for the tokeo.ext.pdoc extension.

The focus is on what this extension adds on top of pdoc, not on pdoc itself:

- `#:` variable comments, which pdoc3 understood and pdoc 16 does not
- the yaml config page: comment extraction, per-setting slicing, `.local`
  redaction and the block boundaries
- namespace-aware module discovery, which pkgutil-based walking misses
- `serve()`, including the daemon thread it starts for the watch monitor
- the controller's flag handling, driven through the real CLI

Everything that can fail at runtime is executed rather than inspected: the
server really binds and answers a request. The watch machinery itself is not
covered here — it needs a live filesystem observer and a process replacement,
which do not belong in a unit test run.
"""

import re
import threading
from html.parser import HTMLParser
import urllib.request
from pathlib import Path

import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest

from tokeo.ext.pdoc import (
    TokeoPdocError,
    _strip_comment_markers,
    _yaml_header_end,
)


# --------------------------------------------------------------------------------------
# app under test
# --------------------------------------------------------------------------------------


class PdocTest(TokeoTest):
    """A TokeoTest app with the pdoc extension loaded."""

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.pdoc',
        ]


def pdoc_defaults(tmp_path, **overrides):
    """Config for the pdoc section, pinned to a temp dir and a free port.

    `port=0` lets the kernel pick a free one, so parallel runs never collide.
    The watch timings are shortened so the monitor reacts inside a test.
    """
    defaults = init_defaults('pdoc')
    defaults['pdoc'].update(
        output_dir=str(tmp_path / 'html'),
        host='127.0.0.1',
        port=0,
        watch_interval=0.05,
        watch_settle=0.05,
    )
    defaults['pdoc'].update(overrides)
    return defaults


def booted_app(tmp_path, **overrides):
    """An app as a context manager, for tests that need their own config."""
    return PdocTest(config_defaults=pdoc_defaults(tmp_path, **overrides))


@pytest.fixture(autouse=True)
def no_process_replacement(monkeypatch):
    """Keep `hotload()` from replacing the test process.

    A test that leaves `_watchdog_restart_requested` set would otherwise have
    the `post_close` hook run `os.execv()` during app teardown, replacing the
    running pytest process. Recording the call instead keeps the behaviour
    observable without ending the session.
    """
    replaced = []
    monkeypatch.setattr(
        'tokeo.ext.pdoc.os.execv',
        lambda path, argv: replaced.append((path, argv)),
    )
    return replaced


@pytest.fixture
def app(tmp_path):
    """A booted app; `app.pdoc` is the handler under test."""
    with booted_app(tmp_path) as booted:
        booted.run()
        yield booted


@pytest.fixture
def log_lines(app, monkeypatch):
    """Capture the handler's log output so tests can assert on it."""
    lines = []

    def record(msg, *args, **kwargs):
        lines.append(str(msg))

    for level in ('info', 'warning', 'error', 'debug'):
        monkeypatch.setattr(app.log, level, record)
    return lines


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def write(path, text):
    """Write `text` to `path`, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def has(lines, needle):
    """Did any captured log line contain `needle`?"""
    return any(needle in line for line in lines)


# --------------------------------------------------------------------------------------
# yaml comment helpers
# --------------------------------------------------------------------------------------


def test_strip_comment_markers_keeps_markdown_headings():
    # the trap this replaced: `lstrip('# ')` also eats the markdown hashes,
    # so `### ### Heading` became a paragraph instead of a heading
    text = '### **Title**\n###\n### ### Heading\n### body'
    assert _strip_comment_markers(text) == '**Title**\n\n### Heading\nbody'


def test_strip_comment_markers_keeps_blank_lines():
    # pdoc3 used `^\s*#+\s`, where `\s` also matches the newline, so blank
    # separator lines disappeared and paragraphs ran together
    assert _strip_comment_markers('### a\n###\n### b') == 'a\n\nb'


def test_strip_comment_markers_keeps_indentation():
    # only one space after the marker is consumed; a greedy `[ \t]*` would
    # eat the indentation too and flatten nested lists and code blocks
    text = '### - first level\n###     - nested\n### ```\n###   code\n### ```'
    assert _strip_comment_markers(text) == ('- first level\n    - nested\n```\n  code\n```')


def test_strip_comment_markers_handles_empty_input():
    assert _strip_comment_markers(None) == ''
    assert _strip_comment_markers('') == ''


@pytest.mark.parametrize(
    'lines,expected',
    [
        (['### doc', '---', 'key: 1'], 1),
        (['key: 1'], 0),
        ([], 0),
    ],
)
def test_yaml_header_end(lines, expected):
    assert _yaml_header_end(lines) == expected


# --------------------------------------------------------------------------------------
# `#:` variable comments (pdoc3 behaviour restored)
# --------------------------------------------------------------------------------------


HASH_COLON_SOURCE = '''"""Module."""

#: single line above
A = 1

#: two lines above,
#: continued
B = 2

C = 3  #: trailing on the assignment

#: must not win against the string literal
D = 4
"""Literal wins."""

# ordinary comment without a colon
E = 5

#: annotated assignment
F: int = 6

G = "text with #: inside, not a comment"


class Klass:
    """Class."""

    #: class attribute above
    H = 7

    I = 8  #: class attribute trailing

    class Inner:
        """Nested."""

        #: nested attribute
        J = 9
'''


@pytest.fixture
def hash_colon_docs(app):
    return app.pdoc._collect_hash_colon_docs(HASH_COLON_SOURCE)


@pytest.mark.parametrize(
    'name,expected',
    [
        ('A', 'single line above'),
        ('B', 'two lines above,\ncontinued'),
        ('C', 'trailing on the assignment'),
        ('F', 'annotated assignment'),
        ('Klass.H', 'class attribute above'),
        ('Klass.I', 'class attribute trailing'),
        ('Klass.Inner.J', 'nested attribute'),
    ],
)
def test_collect_hash_colon_docs_finds_every_form(hash_colon_docs, name, expected):
    assert hash_colon_docs[name] == expected


def test_collect_hash_colon_docs_ignores_plain_comments(hash_colon_docs):
    assert 'E' not in hash_colon_docs


def test_collect_hash_colon_docs_ignores_colon_inside_a_string(hash_colon_docs):
    # pdoc3 got this wrong and pulled a description out of the string value
    assert 'G' not in hash_colon_docs


def test_collect_hash_colon_docs_survives_broken_source(app):
    assert app.pdoc._collect_hash_colon_docs('def (:::') == {}


def test_inject_hash_colon_docstrings_fills_pdoc_members(app, tmp_path, monkeypatch):
    pdoc_doc = pytest.importorskip('pdoc.doc')
    write(tmp_path / 'hcmod.py', HASH_COLON_SOURCE)
    monkeypatch.syspath_prepend(str(tmp_path))

    module = pdoc_doc.Module.from_name('hcmod')
    app.pdoc._inject_hash_colon_docstrings(module)

    assert module.members['A'].docstring == 'single line above'
    assert module.members['B'].docstring == 'two lines above,\ncontinued'
    # a string literal after the assignment stays authoritative
    assert module.members['D'].docstring == 'Literal wins.'
    # an ordinary comment does not become documentation
    assert module.members['E'].docstring == ''


# --------------------------------------------------------------------------------------
# config page: per-setting slicing and redaction
# --------------------------------------------------------------------------------------


BASE_YAML = """### **Main Configuration**
###
### ### Loading order
###
### Later files override earlier ones.
---

### Outgoing mail.
### Second line of the intro.
smtp:
  host: localhost
  port: 25

log.colorlog:
  level: info
"""

LOCAL_YAML = """### Secrets, never checked in.
---

smtp:
  password: PLAINTEXT-SECRET
  host: mail.internal
"""


@pytest.fixture
def base_settings(app):
    import yaml

    return app.pdoc._config_settings('base', BASE_YAML.split('\n'), yaml.safe_load(BASE_YAML))


@pytest.fixture
def local_settings(app):
    import yaml

    return app.pdoc._config_settings('development.local', LOCAL_YAML.split('\n'), yaml.safe_load(LOCAL_YAML))


def test_config_settings_extracts_intro_and_source(base_settings):
    assert base_settings['smtp']['intro'] == ('Outgoing mail.\nSecond line of the intro.')
    assert base_settings['smtp']['source'] == ('smtp:\n  host: localhost\n  port: 25')


def test_config_settings_block_ends_at_a_dotted_key(base_settings):
    # pdoc3's `^([a-zA-Z0-9]+:$|#)` knew no dots, so `log.colorlog:` did not
    # end the previous block and its source leaked into `smtp`
    assert 'log.colorlog' not in base_settings['smtp']['source']
    assert base_settings['log.colorlog']['source'] == 'log.colorlog:\n  level: info'


def test_config_settings_redacts_local_files(local_settings):
    source = local_settings['smtp']['source']

    assert 'PLAINTEXT-SECRET' not in source
    assert 'mail.internal' not in source
    # structure survives, only the values are replaced
    assert 'host' in source and 'password' in source
    assert source.count('***') == 2


def test_config_settings_keeps_key_order_when_redacting(local_settings):
    # the file lists password first; sorting would reorder it to host first
    source = local_settings['smtp']['source']
    assert source.index('password') < source.index('host')


def test_config_settings_returns_empty_for_a_non_mapping(app):
    assert app.pdoc._config_settings('base', ['- a', '- b'], ['a', 'b']) == {}


# --------------------------------------------------------------------------------------
# config page: which files appear and in what order
# --------------------------------------------------------------------------------------


def build_config_tree(root):
    """A config dir covering every file kind appenv knows."""
    write(root / 'base.yaml', 'smtp:\n  host: base\n')
    write(root / 'base.d' / 'smtp.yaml', 'smtp:\n  port: 25\n')
    write(root / 'production.yaml', 'smtp:\n  host: prod\n')
    write(root / 'production.d' / 'smtp.yaml', 'smtp:\n  port: 587\n')
    write(root / 'production.d' / 'creds.local.yaml', 'smtp:\n  user: u\n')
    write(root / 'production.local.yaml', 'smtp:\n  password: SECRET\n')
    return root


def collect_config_sections(app, tmp_path, monkeypatch):
    """Run `_render_config_page` and return the section names it produced."""
    import types

    config_dir = build_config_tree(tmp_path / 'config' / 'spiral')
    monkeypatch.setattr(
        app,
        'env',
        types.SimpleNamespace(
            APP_CONFIG_DIR=str(config_dir),
            get_config_files=lambda app_env='base', app_config_file_suffix='.yaml': (
                fake_appenv_files(config_dir, app_env, app_config_file_suffix)
            ),
        ),
        raising=False,
    )
    app.pdoc.set_show_config(show=True)

    import pdoc.render

    monkeypatch.setattr(
        pdoc.render.env,
        'get_template',
        lambda name: types.SimpleNamespace(render=lambda **kw: '<html></html>'),
    )
    app.pdoc._render_config_page(str(tmp_path / 'out'))
    seen = pdoc.render.env.globals.get('configdict', {})
    return list(seen)


def fake_appenv_files(config_dir, app_env, suffix):
    """Reproduce appenv's resolver, including its ordering.

    Env file, then `.d/*`, then the top-level `.local`, then `.d/*.local`.
    That is cement's load order, and the config page follows it so the
    entries read in the order the values are applied.
    """
    import glob
    import os

    env_file = str(config_dir / f'{app_env}{suffix}')
    configs = [env_file] if os.path.isfile(env_file) else []
    local_file = str(config_dir / f'{app_env}.local{suffix}')
    local = [local_file] if app_env != 'base' and os.path.isfile(local_file) else []
    for path in sorted(glob.glob(str(config_dir / f'{app_env}.d' / '**' / f'*{suffix}'), recursive=True)):
        if path.endswith(f'.local{suffix}'):
            if app_env != 'base':
                local.append(path)
        else:
            configs.append(path)
    return [*configs, *local]


def test_config_page_follows_the_load_order(app, tmp_path, monkeypatch):
    # the order carries meaning: cement merges the files in this sequence and
    # the last one wins, so the page reads top to bottom as values are applied
    sections = collect_config_sections(app, tmp_path, monkeypatch)

    assert sections == [
        'base',
        'base.d/smtp',
        'production',
        'production.d/smtp',
        'production.local',
        'production.d/creds.local',
    ]


def test_config_page_skips_base_local(app, tmp_path, monkeypatch):
    # appenv ignores `.local` for the base environment, so documenting one
    # would promise an override that never happens
    write(tmp_path / 'config' / 'spiral' / 'base.local.yaml', 'smtp:\n  x: 1\n')

    sections = collect_config_sections(app, tmp_path, monkeypatch)

    assert 'base.local' not in sections


# --------------------------------------------------------------------------------------
# module discovery, including namespace packages
# --------------------------------------------------------------------------------------


def test_discover_walks_namespace_subpackages(app, tmp_path, monkeypatch):
    # `pkg.space` has no __init__.py; pkgutil-based walking would skip it
    # and everything below it
    write(tmp_path / 'pkg' / '__init__.py', '"""pkg."""\n')
    write(tmp_path / 'pkg' / 'plain.py', '"""plain."""\n')
    write(tmp_path / 'pkg' / 'space' / 'deep.py', '"""deep."""\n')
    monkeypatch.syspath_prepend(str(tmp_path))

    found = app.pdoc._discover('pkg')

    assert {'pkg', 'pkg.plain', 'pkg.space', 'pkg.space.deep'} <= found


def test_discover_honours_excludes(app, tmp_path, monkeypatch):
    write(tmp_path / 'expkg' / '__init__.py', '"""expkg."""\n')
    write(tmp_path / 'expkg' / 'keep.py', '"""keep."""\n')
    write(tmp_path / 'expkg' / 'drop.py', '"""drop."""\n')
    monkeypatch.syspath_prepend(str(tmp_path))

    found = app.pdoc._discover('expkg', excludes=['expkg.drop'])

    assert 'expkg.keep' in found
    assert 'expkg.drop' not in found


def test_discover_skips_template_placeholders(app, tmp_path, monkeypatch):
    # code-generation trees carry `{{ name }}` directories that are not
    # importable modules
    write(tmp_path / 'tplpkg' / '__init__.py', '"""tplpkg."""\n')
    write(tmp_path / 'tplpkg' / '{{ project }}' / 'x.py', 'x = 1\n')
    monkeypatch.syspath_prepend(str(tmp_path))

    found = app.pdoc._discover('tplpkg')

    assert not any('{{' in spec for spec in found)


def test_discover_honours_pdoc_overrides(app, tmp_path, monkeypatch):
    write(
        tmp_path / 'ovpkg' / '__init__.py',
        '"""ovpkg."""\n\n__pdoc__ = {"hidden": False}\n',
    )
    write(tmp_path / 'ovpkg' / 'hidden.py', '"""hidden."""\n')
    write(tmp_path / 'ovpkg' / 'shown.py', '"""shown."""\n')
    monkeypatch.syspath_prepend(str(tmp_path))

    found = app.pdoc._discover('ovpkg')

    assert 'ovpkg.shown' in found
    assert 'ovpkg.hidden' not in found


# --------------------------------------------------------------------------------------
# watched directories (pure path logic, no observer involved)
# --------------------------------------------------------------------------------------


def stub_dirs(app, monkeypatch, dirs):
    """Pretend the documented modules resolve to `dirs`."""
    monkeypatch.setattr(app.pdoc, '_module_dirs', lambda: dirs)
    monkeypatch.setattr(app.pdoc, '_resolve_docstrings_dirs', lambda: [])


def test_watch_dirs_drops_directories_outside_the_project(app, tmp_path, monkeypatch):
    # an installed tokeo lives in site-packages and is never edited there,
    # so it must not be watched — but it has to be reported back
    project = tmp_path / 'project'
    (project / 'spiral').mkdir(parents=True)
    (project / 'tests').mkdir()
    outside_dir = tmp_path / 'site-packages' / 'tokeo'
    outside_dir.mkdir(parents=True)
    monkeypatch.chdir(project)
    stub_dirs(
        app,
        monkeypatch,
        [
            str(project / 'spiral'),
            str(project / 'tests'),
            str(outside_dir),
        ],
    )

    dirs, outside = app.pdoc._watch_dirs()

    assert sorted(Path(d).name for d in dirs) == ['spiral', 'tests']
    assert outside == [str(outside_dir)]


def test_watch_dirs_collapses_nested_directories(app, tmp_path, monkeypatch):
    project = tmp_path / 'project'
    (project / 'spiral' / 'core').mkdir(parents=True)
    monkeypatch.chdir(project)
    stub_dirs(
        app,
        monkeypatch,
        [
            str(project / 'spiral'),
            str(project / 'spiral' / 'core'),
        ],
    )

    assert app.pdoc._watch_dirs()[0] == [str(project / 'spiral')]


def test_watch_dirs_does_not_confuse_a_prefix_with_a_parent(
    app,
    tmp_path,
    monkeypatch,
):
    # `proj2` starts with `proj` but is not inside it; a plain startswith()
    # check on the paths would wrongly keep it
    project = tmp_path / 'proj'
    (project / 'app').mkdir(parents=True)
    sibling = tmp_path / 'proj2'
    sibling.mkdir()
    monkeypatch.chdir(project)
    stub_dirs(app, monkeypatch, [str(project / 'app'), str(sibling)])

    assert app.pdoc._watch_dirs()[0] == [str(project / 'app')]


def test_watch_dirs_includes_the_docstrings_dirs(app, tmp_path, monkeypatch):
    # the decorator snippets are watched too, so editing one rebuilds. Cannot
    # use stub_dirs() here: that helper pins _resolve_docstrings_dirs to []
    project = tmp_path / 'project'
    (project / 'spiral').mkdir(parents=True)
    (project / 'snippets').mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        app.pdoc,
        '_module_dirs',
        lambda: [str(project / 'spiral')],
    )
    monkeypatch.setattr(
        app.pdoc,
        '_resolve_docstrings_dirs',
        lambda: [str(project / 'snippets')],
    )

    dirs, _ = app.pdoc._watch_dirs()

    assert sorted(Path(d).name for d in dirs) == ['snippets', 'spiral']


def test_watch_dirs_includes_the_config_dir_when_appenv_is_loaded(
    app,
    tmp_path,
    monkeypatch,
):
    import types

    project = tmp_path / 'project'
    (project / 'spiral').mkdir(parents=True)
    (project / 'config').mkdir()
    monkeypatch.chdir(project)
    stub_dirs(app, monkeypatch, [str(project / 'spiral')])
    monkeypatch.setattr(
        app,
        'env',
        types.SimpleNamespace(APP_CONFIG_DIR=str(project / 'config')),
        raising=False,
    )

    dirs, _ = app.pdoc._watch_dirs()

    assert sorted(Path(d).name for d in dirs) == ['config', 'spiral']


# --------------------------------------------------------------------------------------
# external docstrings for namespace packages
# --------------------------------------------------------------------------------------


def test_init_md_becomes_the_docstring_of_a_namespace_package(
    app,
    tmp_path,
    monkeypatch,
):
    # a directory with only __init__.md has no python docstring, so pdoc
    # would render the package page blank
    pdoc_doc = pytest.importorskip('pdoc.doc')
    package = tmp_path / 'nspkg'
    write(package / '__init__.md', 'Intro from markdown.\n')
    write(package / 'mod.py', '"""A module."""\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    pdoc_doc.Module.from_name.cache_clear()

    module = pdoc_doc.Module.from_name('nspkg')
    app.pdoc._inject_external_docstrings(module)

    assert module.docstring.strip() == 'Intro from markdown.'


def test_init_md_is_skipped_for_a_regular_package(app, tmp_path, monkeypatch):
    # with an __init__.py present the python docstring is authoritative,
    # whatever an __init__.md next to it says
    pdoc_doc = pytest.importorskip('pdoc.doc')
    package = tmp_path / 'realpkg'
    write(package / '__init__.py', '"""The real docstring."""\n')
    write(package / '__init__.md', 'Should be ignored.\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    pdoc_doc.Module.from_name.cache_clear()

    module = pdoc_doc.Module.from_name('realpkg')
    app.pdoc._inject_external_docstrings(module)

    assert module.docstring.strip() == 'The real docstring.'


def test_init_md_does_not_override_an_existing_docstring(app, tmp_path, monkeypatch):
    # the second guard: even without an __init__.py, a docstring that is
    # already set (e.g. injected earlier in the chain) must survive
    pdoc_doc = pytest.importorskip('pdoc.doc')
    package = tmp_path / 'guardpkg'
    write(package / '__init__.md', 'From markdown.\n')
    write(package / 'mod.py', '"""A module."""\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    pdoc_doc.Module.from_name.cache_clear()

    module = pdoc_doc.Module.from_name('guardpkg')
    module.__dict__['docstring'] = 'Already documented.'
    app.pdoc._inject_external_docstrings(module)

    assert module.docstring == 'Already documented.'


def test_decorator_docstrings_are_read_from_the_configured_packages(app):
    # `docstrings` feeds the decorator documentation the extensions ask for
    # via `app.pdoc.docstrings('decorator', ...)`; unlike the removed
    # `docstrings_dir` it is a search path with first-wins order
    text = app.pdoc.docstrings('decorator', 'argparse.expose')

    assert text and text.strip()


def test_decorator_docstrings_return_none_when_missing(app):
    assert app.pdoc.docstrings('decorator', 'does.not.exist') is None


# --------------------------------------------------------------------------------------
# sidebar rendering: class entries and their indentation
# --------------------------------------------------------------------------------------


SIDEBAR_SOURCES = {
    # a class whose only member is a nested Meta: nothing to list, so it
    # renders as a plain link without a chevron
    'bare': '''"""Bare classes."""


class Alpha:
    """No listable members."""

    class Meta:
        """Meta."""

        label = 'alpha'


class Beta:
    """Also nothing to list."""

    class Meta:
        """Meta."""

        label = 'beta'


def helper():
    """A module function."""
''',
    # every class has public methods, so all of them are expandable
    'expandable': '''"""Expandable classes."""


class Alpha:
    """Has methods."""

    def start(self):
        """Start."""

    def stop(self):
        """Stop."""


class Beta:
    """Has a method too."""

    def go(self):
        """Go."""
''',
    # the interesting one: both kinds side by side
    'mixed': '''"""Mixed classes."""


class Alpha:
    """No listable members."""

    class Meta:
        """Meta."""

        label = 'alpha'


class Beta:
    """Has methods."""

    def go(self):
        """Go."""
''',
}


def render_sidebar(tmp_path, monkeypatch, source, package_name='sample'):
    """Render one module through the real pipeline, return its sidebar HTML.

    Goes through `app.pdoc.render()` rather than calling Jinja directly, so
    template resolution, the custom globals and the asset copy are all part
    of what is being tested.

    Each call uses a fresh package name and drops pdoc's cache: an already
    imported module stays in `sys.modules`, and `Module.from_name` is cached
    too, so a second render in the same process would document the previous
    test's source. (That is exactly why `--watch` replaces the process
    instead of re-rendering in place.)
    """
    import pdoc.doc

    package = tmp_path / package_name
    package.mkdir()
    write(package / '__init__.py', '"""Sample."""\n')
    write(package / 'main.py', source)
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    pdoc.doc.Module.from_name.cache_clear()

    defaults = pdoc_defaults(
        tmp_path,
        modules=[package_name],
        show_config=False,
    )
    with PdocTest(config_defaults=defaults) as booted:
        booted.run()
        booted.pdoc.render()

    html = (tmp_path / 'html' / package_name / 'main.html').read_text()
    match = re.search(r'>Classes</h3>(.*?)(?=<h3|</nav>)', html, re.S)
    assert match, 'the sidebar has no Classes section'
    return match.group(1)


class SidebarEntries(HTMLParser):
    """Read the sidebar's class entries with a real parser.

    A regex cannot do this: `<details>` nests its own `<div>` and carries
    several links, so any non-greedy pattern stops in the wrong place. This
    tracks nesting depth instead and records, per class, whether it rendered
    as an expandable `<details>` or as a plain link, plus that link
    wrapper's css classes.
    """

    def __init__(self):
        super().__init__()
        #: class name -> 'expandable' or the wrapper's css classes
        self.entries = {}
        self._details_depth = 0
        self._wrapper = None
        self._div_depth = 0
        self._pending = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'details':
            self._details_depth += 1
            self._pending = 'expandable'
        elif tag == 'div':
            self._div_depth += 1
            css = attrs.get('class', '')
            if self._details_depth == 0 and css.startswith('py-0.5'):
                self._wrapper = (css, self._div_depth)
                self._pending = css
        elif tag == 'a':
            href = attrs.get('href', '')
            # only the top-level entry link, not a member link inside details
            if href.startswith('#') and '.' not in href and self._pending:
                self.entries[href[1:]] = self._pending
                self._pending = None

    def handle_endtag(self, tag):
        if tag == 'details':
            self._details_depth = max(self._details_depth - 1, 0)
        elif tag == 'div':
            if self._wrapper and self._wrapper[1] == self._div_depth:
                self._wrapper = None
            self._div_depth = max(self._div_depth - 1, 0)


def class_entries(sidebar):
    """Map each class name in the sidebar to how it was rendered."""
    parser = SidebarEntries()
    parser.feed(sidebar)
    return parser.entries


def test_sidebar_bare_classes_are_not_indented(tmp_path, monkeypatch):
    # with no chevron anywhere in the section there is nothing to line up
    # with, so padding would only push the names out of line with every
    # other sidebar section
    entries = class_entries(render_sidebar(tmp_path, monkeypatch, SIDEBAR_SOURCES['bare'], 'bare_a'))

    assert entries == {'Alpha': 'py-0.5', 'Beta': 'py-0.5'}


def test_sidebar_bare_classes_align_with_the_functions_section(
    tmp_path,
    monkeypatch,
):
    # the visible symptom of a wrong padding: class names sitting further in
    # than the function names right below them
    sidebar = render_sidebar(tmp_path, monkeypatch, SIDEBAR_SOURCES['bare'], 'bare_b')

    assert 'pl-' not in class_entries(sidebar)['Alpha']


def test_sidebar_expandable_classes_get_a_chevron(tmp_path, monkeypatch):
    sidebar = render_sidebar(tmp_path, monkeypatch, SIDEBAR_SOURCES['expandable'], 'exp_a')
    entries = class_entries(sidebar)

    assert entries == {'Alpha': 'expandable', 'Beta': 'expandable'}
    assert sidebar.count('tk-chev') == 2


def test_sidebar_expandable_classes_list_their_members(tmp_path, monkeypatch):
    sidebar = render_sidebar(tmp_path, monkeypatch, SIDEBAR_SOURCES['expandable'], 'exp_b')

    assert 'href="#Alpha.start"' in sidebar
    assert 'href="#Alpha.stop"' in sidebar
    assert 'href="#Beta.go"' in sidebar


def test_sidebar_mixed_classes_line_up_with_the_chevron(tmp_path, monkeypatch):
    # here the padding is earned: the plain link has to start where the
    # expandable one's name starts, past its chevron
    entries = class_entries(render_sidebar(tmp_path, monkeypatch, SIDEBAR_SOURCES['mixed'], 'mix_a'))

    assert entries['Beta'] == 'expandable'
    assert entries['Alpha'] == 'py-0.5 pl-3.5'


def test_sidebar_padding_matches_the_chevron_width(tmp_path, monkeypatch):
    # `tk-chev` is .4rem wide and the summary uses `gap-2` (.5rem), so the
    # name starts .9rem in; pl-3.5 is .875rem, the closest value the
    # pre-built tailwind.min.css actually ships
    sidebar = render_sidebar(tmp_path, monkeypatch, SIDEBAR_SOURCES['mixed'], 'mix_b')
    padding = class_entries(sidebar)['Alpha']

    assert padding.endswith('pl-3.5')
    assert 'gap-2' in sidebar  # the chevron gap the padding is derived from


def test_sidebar_padding_class_exists_in_the_stylesheet(tmp_path, monkeypatch):
    # an arbitrary value like `pl-[0.9rem]` would need a tailwind rebuild and
    # would silently have no effect until someone runs it
    render_sidebar(tmp_path, monkeypatch, SIDEBAR_SOURCES['mixed'], 'mix_c')
    stylesheet = (tmp_path / 'html' / 'assets' / 'tailwind.min.css').read_text()

    assert '.pl-3\\.5{' in stylesheet


# --------------------------------------------------------------------------------------
# serve
# --------------------------------------------------------------------------------------


def test_serve_answers_and_stops_via_the_started_monitor(app, tmp_path, monkeypatch):
    write(tmp_path / 'html' / 'index.html', '<html>tokeo</html>\n')
    seen = {}

    def monitor(httpd):
        seen['httpd'] = httpd
        port = httpd.server_address[1]
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=5) as r:
            seen['body'] = r.read().decode().strip()
        httpd.shutdown()

    # `_watch()` returns the monitor; serve() runs it in a daemon thread
    monkeypatch.setattr(
        app.pdoc,
        '_watch',
        lambda restarted=False: monitor,
    )

    thread = threading.Thread(target=lambda: app.pdoc.serve(watch=True))
    thread.start()
    thread.join(timeout=15)

    assert not thread.is_alive()
    assert 'httpd' in seen
    assert seen['body'] == '<html>tokeo</html>'


def test_serve_reports_a_missing_output_dir(tmp_path):
    with booted_app(tmp_path, output_dir=str(tmp_path / 'gone')) as booted:
        booted.run()
        lines = []
        booted.log.error = lambda msg, *a, **kw: lines.append(str(msg))

        assert booted.pdoc.serve() is None
        assert has(lines, 'nothing to serve')


def test_serve_raises_a_clean_error_when_the_port_is_taken(tmp_path):
    import socket

    write(tmp_path / 'html' / 'index.html', '<html>x</html>\n')
    blocker = socket.socket()
    blocker.bind(('127.0.0.1', 0))
    blocker.listen(1)
    try:
        with booted_app(tmp_path, port=blocker.getsockname()[1]) as booted:
            booted.run()
            with pytest.raises(TokeoPdocError) as excinfo:
                booted.pdoc.serve()
            assert 'port' in str(excinfo.value)
    finally:
        blocker.close()


# --------------------------------------------------------------------------------------
# startup output and the hidden --restart flag
# --------------------------------------------------------------------------------------


def test_documented_lists_the_configured_modules(app, monkeypatch):
    # the summary says what is *documented*; an installed tokeo is
    # documented even though --watch never observes it
    app.pdoc.set_modules(['spiral', 'tests', 'tokeo'])
    monkeypatch.setattr(app, 'env', None, raising=False)

    assert app.pdoc._documented() == ['spiral', 'tests', 'tokeo']


def test_documented_appends_config_when_the_page_is_written(app, monkeypatch):
    import types

    app.pdoc.set_modules(['spiral'])
    app.pdoc.set_show_config(show=True)
    monkeypatch.setattr(
        app,
        'env',
        types.SimpleNamespace(APP_CONFIG_DIR='/x'),
        raising=False,
    )

    assert app.pdoc._documented() == ['spiral', 'config']


def test_documented_omits_config_when_disabled(app, monkeypatch):
    import types

    app.pdoc.set_modules(['spiral'])
    app.pdoc.set_show_config(show=False)
    monkeypatch.setattr(
        app,
        'env',
        types.SimpleNamespace(APP_CONFIG_DIR='/x'),
        raising=False,
    )

    assert app.pdoc._documented() == ['spiral']


def test_documented_keeps_the_specs_as_configured(app, monkeypatch):
    # `modules: [spiral.core, spiral.ext, tests]` documents exactly those
    # three, so shortening them to `spiral` would misreport the render
    app.pdoc.set_modules(['spiral.core', 'spiral.ext', 'tests'])
    monkeypatch.setattr(app, 'env', None, raising=False)

    assert app.pdoc._documented() == ['spiral.core', 'spiral.ext', 'tests']


def test_documented_removes_duplicates_but_keeps_the_order(app, monkeypatch):
    app.pdoc.set_modules(['tests', 'spiral', 'tests'])
    monkeypatch.setattr(app, 'env', None, raising=False)

    assert app.pdoc._documented() == ['tests', 'spiral']


def test_set_modules_takes_a_list(app):
    app.pdoc.set_modules(['alpha', 'beta'])

    assert app.pdoc._modules == ['alpha', 'beta']


def test_set_modules_splits_a_string(app):
    app.pdoc.set_modules('alpha beta gamma')

    assert app.pdoc._modules == ['alpha', 'beta', 'gamma']


def test_set_modules_defaults_to_app_tests_and_tokeo(app):
    app.pdoc.set_modules(None)

    assert app.pdoc._modules == [app._meta.label, 'tests', 'tokeo']


def test_setup_reads_the_configured_modules(tmp_path):
    with booted_app(tmp_path, modules=['alpha', 'beta']) as booted:
        booted.run()

        assert booted.pdoc._modules == ['alpha', 'beta']


def test_setup_reads_the_configured_show_config(tmp_path):
    for configured in (True, False):
        with booted_app(tmp_path / str(configured), show_config=configured) as booted:
            booted.run()

            assert booted.pdoc._show_config is configured


def test_set_show_config_accepts_truthy_strings(app):
    for value, expected in (
        (True, True),
        ('yes', True),
        ('1', True),
        (False, False),
        ('no', False),
        ('off', False),
    ):
        app.pdoc.set_show_config(show=value)
        assert app.pdoc._show_config is expected


def test_set_show_config_without_argument_takes_the_config(app):
    app.pdoc.set_show_config(show=False)
    app.pdoc.set_show_config()

    assert app.pdoc._show_config == app.config.get('pdoc', 'show_config')


def watch_output(app, monkeypatch, tmp_path, restarted=False):
    """Start a watch and return the lines it logged, by level."""
    pytest.importorskip('watchdog')
    project = tmp_path / 'project'
    (project / 'spiral').mkdir(parents=True)
    monkeypatch.chdir(project)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    stub_dirs(app, monkeypatch, [str(project / 'spiral'), str(elsewhere)])

    lines = {'info': [], 'debug': []}
    for level in lines:
        monkeypatch.setattr(
            app.log,
            level,
            lambda msg, *a, _l=level, **k: lines[_l].append(str(msg)),
        )

    app.pdoc._watch(restarted=restarted)
    app.pdoc.shutdown()
    return lines


def test_watch_announces_itself_on_info(app, monkeypatch, tmp_path):
    lines = watch_output(app, monkeypatch, tmp_path)

    assert lines['info'] == ['pdoc watching for changes on project files']


def test_watch_puts_the_directory_detail_on_debug(app, monkeypatch, tmp_path):
    # the paths are noise on info level but the only way to tell why an edit
    # does nothing, so they belong on debug rather than nowhere
    lines = watch_output(app, monkeypatch, tmp_path)

    assert any(line.startswith('pdoc watching /') for line in lines['debug'])
    assert any('not watching' in line for line in lines['debug'])
    assert not any('not watching' in line for line in lines['info'])


def test_watch_stays_quiet_after_a_restart(app, monkeypatch, tmp_path):
    # a restart replays the whole command, and repeating the startup output
    # would bury the one line that says what changed
    lines = watch_output(app, monkeypatch, tmp_path, restarted=True)

    assert lines['info'] == []
    assert lines['debug'] == []


def test_hotload_hands_the_restart_flag_on(app, monkeypatch):
    # the replayed command line drops --clean (wipe once, not per rebuild)
    # and gains --restart (stay quiet next time)
    seen = {}
    monkeypatch.setattr(
        'tokeo.ext.pdoc.os.execv',
        lambda path, argv: seen.update(path=path, argv=argv),
    )
    monkeypatch.setattr(
        'sys.argv',
        ['/venv/bin/spiral', 'pdoc', 'render', '--clean', '--serve', '--watch'],
    )
    app.pdoc._watchdog_restart_requested = True

    app.pdoc.hotload()

    # argv[0] is the interpreter, argv[1] the console script
    assert seen['argv'][2:] == ['pdoc', 'render', '--serve', '--watch', '--restart']


def test_hotload_does_not_repeat_the_restart_flag(app, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        'tokeo.ext.pdoc.os.execv',
        lambda path, argv: seen.update(argv=argv),
    )
    monkeypatch.setattr(
        'sys.argv',
        ['/venv/bin/spiral', 'pdoc', 'render', '--serve', '--watch', '--restart'],
    )
    app.pdoc._watchdog_restart_requested = True

    app.pdoc.hotload()

    assert seen['argv'].count('--restart') == 1


def test_restart_flag_is_hidden_from_the_help(tmp_path, monkeypatch, capsys):
    # it exists for the process replacement, not for users to type
    with booted_app(tmp_path) as booted:
        monkeypatch.setattr(booted._meta, 'argv', ['pdoc', 'render', '--help'])
        with pytest.raises(SystemExit):
            booted.run()

    help_text = capsys.readouterr().out
    assert '--watch' in help_text
    assert '--restart' not in help_text


# --------------------------------------------------------------------------------------
# render error handling
# --------------------------------------------------------------------------------------


def render_minimal(tmp_path, monkeypatch, package_name, clean):
    """Render a one-module package, so the clean step really runs."""
    import pdoc.doc

    package = tmp_path / package_name
    package.mkdir()
    write(package / '__init__.py', '"""Sample."""\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    pdoc.doc.Module.from_name.cache_clear()

    defaults = pdoc_defaults(tmp_path, modules=[package_name], show_config=False)
    with PdocTest(config_defaults=defaults) as booted:
        booted.run()
        booted.pdoc.render(clean=clean)


def test_clean_wipes_the_output_dir(tmp_path, monkeypatch):
    # pages of modules that no longer exist would otherwise stay reachable
    stale = tmp_path / 'html' / 'stale.html'
    write(stale, '<html>from an earlier render</html>\n')

    render_minimal(tmp_path, monkeypatch, 'cleanpkg', clean=True)

    assert not stale.exists()
    assert (tmp_path / 'html' / 'index.html').exists()


def test_without_clean_the_output_dir_is_kept(tmp_path, monkeypatch):
    stale = tmp_path / 'html' / 'stale.html'
    write(stale, '<html>from an earlier render</html>\n')

    render_minimal(tmp_path, monkeypatch, 'keeppkg', clean=False)

    assert stale.exists()
    assert (tmp_path / 'html' / 'index.html').exists()


def test_output_dir_is_resolved_at_setup(tmp_path):
    # resolved once from the config, so render and serve cannot disagree and
    # a read before the first render already answers
    with booted_app(tmp_path) as booted:
        booted.run()

        assert booted.pdoc._output_dir == str(tmp_path / 'html')


def test_output_dir_is_absolute(tmp_path, monkeypatch):
    # `output_dir: html` in the config is relative; every consumer needs the
    # absolute path
    monkeypatch.chdir(tmp_path)
    with booted_app(tmp_path, output_dir='html') as booted:
        booted.run()

        assert Path(booted.pdoc._output_dir).is_absolute()
        assert Path(booted.pdoc._output_dir).name == 'html'


def test_render_reports_the_output_directory(app, monkeypatch):
    lines = []
    monkeypatch.setattr(app.pdoc, '_render', lambda **kwargs: None)
    monkeypatch.setattr(app.log, 'info', lambda msg, *a, **k: lines.append(str(msg)))

    app.pdoc.render()

    assert any('documentation updated in' in line for line in lines)


def test_render_reports_nothing_when_it_failed(app, monkeypatch):
    # the message sits inside the try, so a failed render must not claim
    # that anything was written
    lines = []

    def boom(**kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(app.pdoc, '_render', boom)
    monkeypatch.setattr(app.log, 'info', lambda msg, *a, **k: lines.append(str(msg)))

    app.pdoc.render(raise_on_error=False)

    assert not any('documentation updated in' in line for line in lines)


def test_render_reports_after_a_restart_too(tmp_path, monkeypatch):
    # a restart shows the change and what came of it; this line is the only
    # confirmation that the rebuild finished
    lines = []

    with booted_app(tmp_path) as booted:
        monkeypatch.setattr(booted.pdoc, '_render', lambda **kwargs: None)
        monkeypatch.setattr(
            booted.log,
            'info',
            lambda msg, *a, **k: lines.append(str(msg)),
        )
        monkeypatch.setattr(booted._meta, 'argv', ['pdoc', 'render', '--restart'])
        booted.run()

    assert any('documentation updated in' in line for line in lines)


def test_render_raises_by_default(app, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(app.pdoc, '_render', boom)

    with pytest.raises(RuntimeError):
        app.pdoc.render()


def test_render_logs_instead_of_raising_for_watch(app, log_lines, monkeypatch):
    # a broken template must not end the watch session, the next save fixes it
    def boom(**kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(app.pdoc, '_render', boom)

    app.pdoc.render(raise_on_error=False)

    assert has(log_lines, 'render failed')


def test_module_source_dir_of_a_package(app, tmp_path, monkeypatch):
    import importlib
    import types

    write(tmp_path / 'mdpkg' / '__init__.py', '"""mdpkg."""\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    module = types.SimpleNamespace(obj=importlib.import_module('mdpkg'))

    assert Path(app.pdoc._module_source_dir(module)).name == 'mdpkg'


# --------------------------------------------------------------------------------------
# controller flags
# --------------------------------------------------------------------------------------


def run_cli(tmp_path, monkeypatch, argv):
    """Run the real CLI and record what the handler was asked to do.

    Goes through cement's argument parsing, so the flag combinations are
    exercised exactly as a user types them. `render()` and `serve()` are
    stubbed out because the point here is the controller's branching, not
    another full render.
    """
    calls = []

    with booted_app(tmp_path) as booted:
        monkeypatch.setattr(
            booted.pdoc,
            'render',
            lambda **kwargs: calls.append(('render', kwargs.get('raise_on_error'))),
        )
        monkeypatch.setattr(
            booted.pdoc,
            'serve',
            lambda **kwargs: calls.append(('serve', kwargs.get('watch'))),
        )
        monkeypatch.setattr(booted._meta, 'argv', argv)
        booted.run()

    return calls


def test_config_switch_turns_the_page_on(tmp_path, monkeypatch):
    with booted_app(tmp_path, show_config=False) as booted:
        monkeypatch.setattr(booted.pdoc, 'render', lambda **kwargs: None)
        monkeypatch.setattr(booted._meta, 'argv', ['pdoc', 'render', '--config'])
        booted.run()

        assert booted.pdoc._show_config is True
        # the switch is for one run; the configured value stays as written
        assert booted.config.get('pdoc', 'show_config') is False


def test_no_config_switch_turns_the_page_off(tmp_path, monkeypatch):
    with booted_app(tmp_path, show_config=True) as booted:
        monkeypatch.setattr(booted.pdoc, 'render', lambda **kwargs: None)
        monkeypatch.setattr(booted._meta, 'argv', ['pdoc', 'render', '--no-config'])
        booted.run()

        assert booted.pdoc._show_config is False
        assert booted.config.get('pdoc', 'show_config') is True


def test_both_config_switches_are_a_contradiction(tmp_path, monkeypatch):
    with booted_app(tmp_path) as booted:
        monkeypatch.setattr(booted.pdoc, 'render', lambda **kwargs: None)
        monkeypatch.setattr(
            booted._meta,
            'argv',
            ['pdoc', 'render', '--config', '--no-config'],
        )

        with pytest.raises(TokeoPdocError) as excinfo:
            booted.run()

        assert 'exclude each other' in str(excinfo.value)


def test_config_switches_leave_the_setting_alone_when_unused(tmp_path, monkeypatch):
    # both switches are always offered, so the one matching the current
    # setting must simply do nothing
    with booted_app(tmp_path, show_config=True) as booted:
        monkeypatch.setattr(booted.pdoc, 'render', lambda **kwargs: None)
        monkeypatch.setattr(booted._meta, 'argv', ['pdoc', 'render'])
        booted.run()

        assert booted.pdoc._show_config is True


def test_no_config_switch_removes_config_from_the_summary(tmp_path, monkeypatch):
    import types

    with booted_app(tmp_path, show_config=True) as booted:
        monkeypatch.setattr(booted.pdoc, 'render', lambda **kwargs: None)
        monkeypatch.setattr(
            booted,
            'env',
            types.SimpleNamespace(APP_CONFIG_DIR=str(tmp_path)),
            raising=False,
        )
        monkeypatch.setattr(booted._meta, 'argv', ['pdoc', 'render', '--no-config'])
        booted.run()

        assert 'config' not in booted.pdoc._documented()


def test_controller_renders_only_without_flags(tmp_path, monkeypatch):
    assert run_cli(tmp_path, monkeypatch, ['pdoc', 'render']) == [
        ('render', True),
    ]


def test_controller_serves_after_rendering(tmp_path, monkeypatch):
    assert run_cli(tmp_path, monkeypatch, ['pdoc', 'render', '--serve']) == [
        ('render', True),
        ('serve', False),
    ]


def test_controller_ignores_watch_without_serve(tmp_path, monkeypatch):
    # --watch only means something while serving; on its own the command
    # stays a plain render and keeps failing loudly
    assert run_cli(tmp_path, monkeypatch, ['pdoc', 'render', '--watch']) == [
        ('render', True),
    ]


def test_controller_watches_and_tolerates_a_failed_render(tmp_path, monkeypatch):
    # a broken template must not end the watch session, the next save fixes it
    argv = ['pdoc', 'render', '--serve', '--watch']
    assert run_cli(tmp_path, monkeypatch, argv) == [
        ('render', False),
        ('serve', True),
    ]


def test_controller_serve_command_does_not_render(tmp_path, monkeypatch):
    assert run_cli(tmp_path, monkeypatch, ['pdoc', 'serve']) == [('serve', None)]


def test_controller_passes_positional_modules(tmp_path, monkeypatch):
    seen = {}

    with booted_app(tmp_path, modules=['from', 'config']) as booted:
        monkeypatch.setattr(
            booted.pdoc,
            'render',
            lambda **kwargs: seen.update(kwargs),
        )
        monkeypatch.setattr(booted._meta, 'argv', ['pdoc', 'render', 'spiral', 'tests'])
        booted.run()

        # positionals replace the configured modules for this run
        assert booted.pdoc._modules == ['spiral', 'tests']

    assert seen['clean'] is False


def test_controller_passes_clean(tmp_path, monkeypatch):
    seen = {}

    with booted_app(tmp_path) as booted:
        monkeypatch.setattr(booted.pdoc, 'render', lambda **kwargs: seen.update(kwargs))
        monkeypatch.setattr(booted._meta, 'argv', ['pdoc', 'render', '--clean'])
        booted.run()

    assert seen['clean'] is True
