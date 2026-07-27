"""
Tokeo Pdoc Extension Module (pdoc / MIT-0 backend).

Wraps pdoc (https://pdoc.dev, MIT-0) behind the existing Tokeo extension
surface. The controller commands (`pdoc render`, `pdoc serve`), their
arguments (`--clean`, `--serve`, positional `modules`) and the config keys
(`modules`, `output_dir`, `host`, `port`, `favicon`, `templates`) are kept
identical to the previous pdoc3-based extension, so `tokeo pdoc` keeps
working unchanged.

The rendering itself uses pdoc's public API: `pdoc.doc.Module`,
`pdoc.render`, `pdoc.render.env` for custom globals, and pdoc's built-in
web server for `--serve`. No private APIs are monkeypatched.
"""

import sys
import os
import re
import enum
from time import monotonic
import importlib
import ast
import inspect
import shutil
from pathlib import Path
import warnings
import logging
from threading import Thread, Lock, Event

from cement.utils import fs
from cement.utils.misc import is_true
from cement.core.handler import Handler
from cement import ex
from argparse import SUPPRESS
from tokeo.ext.argparse import Controller
from tokeo.core.exc import TokeoError


#: A yaml comment marker at the start of a line: one run of `#` plus at most
#: one following space. Only the marker is removed, so a documentation line
#: written as `### ### Heading` keeps its Markdown `###` and still renders as
#: a heading. (Stripping every leading `#` would flatten it to a paragraph.)
_RE_COMMENT_MARKER = re.compile(r'^[ \t]*#+[ \t]?', re.MULTILINE)

#: A line that ends a settings block: either a new top-level key or a comment
#: introducing the next one.
_RE_BLOCK_END = re.compile(r'^([a-zA-Z0-9_.-]+:$|#)')


def _strip_comment_markers(text):
    """Turn a block of yaml comment lines into plain Markdown."""
    return _RE_COMMENT_MARKER.sub('', text or '')


def _yaml_header_end(lines):
    """Index of the `---` document marker, or 0 when there is no header."""
    try:
        return lines.index('---')
    except ValueError:
        return 0


class TokeoPdocError(TokeoError):
    """Errors raised by the pdoc extension.

    Inherits from TokeoError so the application's `main()` reports it as a
    clean message instead of dumping a traceback.
    """

    pass


try:
    from watchdog.observers import Observer
    from watchdog.events import PatternMatchingEventHandler

    class TokeoPdocWatchdogEventHandler(PatternMatchingEventHandler):
        """
        Event handler for watchdog to monitor file changes.

        Same shape as `TokeoNiceguiWatchdogEventHandler`, with two deliberate
        differences that rendering forces:

        - only `created`, `deleted`, `modified` and `moved` are forwarded.
          Rendering imports and reads every documented module, which raises
          `opened` and `closed_no_write` events on the very files being
          watched. Reacting to those would make the watcher retrigger itself
          forever.
        - the callback receives the event, so the path that triggered the
          restart can be logged.

        ### Notes

        - Used for the `--watch` flag of `pdoc render`
        - Monitors file changes based on configured patterns
        - Calls the provided callback with the event when changes are detected
        - Only available when the watchdog package is installed

        """

        #: event types that mean a file really changed
        WATCH_EVENTS = frozenset({'created', 'deleted', 'modified', 'moved'})

        def __init__(self, patterns=None, ignore_patterns=None, ignore_directories=False, case_sensitive=False, callback=None):
            """
            Initialize the watchdog event handler.

            ### Args

            - **patterns** (list): List of file patterns to watch for changes
            - **ignore_patterns** (list): List of file patterns to ignore
            - **ignore_directories** (bool): Whether to ignore directory events
            - **case_sensitive** (bool): Whether patterns are case sensitive
            - **callback** (callable): Function to call with the detected event

            ### Notes

            - The patterns use glob syntax and are matched against the full
              path, so a directory is excluded as `*/__pycache__/*`

            """
            super().__init__(
                patterns=patterns, ignore_patterns=ignore_patterns, ignore_directories=ignore_directories, case_sensitive=case_sensitive
            )
            self.callback = callback

        def on_any_event(self, event):
            """
            Forward a real change to the callback.

            ### Args

            - **event** (FileSystemEvent): The file system event that was detected

            ### Notes

            - Called automatically by watchdog on a matching file system event

            - Read-only events are dropped, see the class docstring

            """
            if self.callback and event.event_type in self.WATCH_EVENTS:
                self.callback(event)

except ImportError:

    class TokeoPdocWatchdogEventHandler:
        """
        Fallback event handler when watchdog is not available.

        This class provides a placeholder implementation that raises an error
        when instantiated, indicating that the watchdog library is missing.

        ### Notes

        - Used when the watchdog package is not installed

        - Raises an error to indicate watchdog is required for file monitoring
        """

        def __init__(self, patterns=None, ignore_patterns=None, ignore_directories=True, case_sensitive=False, callback=None):
            """
            Raise an error indicating watchdog is not available.

            ### Raises

            - **TokeoPdocError**: Always raised to indicate watchdog is missing

            """
            raise TokeoPdocError('Watchdog library is missing to observe file changes')

        def on_any_event(self, event):
            """
            Placeholder method for handling file system events.

            ### Notes

            - This method is never called as initialization always raises an error

            """
            pass


class TokeoPdoc(Handler):
    """Render and serve API documentation via pdoc."""

    class Meta:
        label = 'pdoc'
        interface = 'pdoc'

        #: config section this extension reads
        config_section = 'pdoc'

        #: default settings, merged (non-overriding) at setup so the section
        #: always exists even when the app config does not declare it
        config_defaults = dict(
            modules=None,
            exclude=[],
            output_dir='html',
            host='127.0.0.1',
            port=9999,
            favicon='public/favicon.ico',
            lang='en',
            title='Tokeo API',
            brand=None,
            templates=['tokeo.templates.pdoc.html'],
            docstrings=['tokeo.templates.pdoc.docstrings'],
            show_config=True,
            ancestors_max_depth=2,
            watch_includes='*.py, *.yaml, *.yml, *.md',
            watch_excludes='.*',
            watch_interval=2.0,
            watch_settle=1.5,
        )

    def _setup(self, app):
        super()._setup(app)
        self.app = app
        # ensure the config section exists with sane defaults; app-provided
        # values win (override=False), mirroring the other tokeo extensions
        self.app.config.merge(
            {self._meta.config_section: self._meta.config_defaults},
            override=False,
        )
        self._output_dir = fs.abspath(self._config('output_dir'))
        self.set_modules(self._config('modules'))
        self.set_show_config(self._config('show_config'))
        # external decorator/docstring markdown snippets (see `docstrings()`)
        self._docstrings_cache = dict()
        self._docstrings_dirs = None
        # watchdog files components
        self._watchdog_observer = None
        self._watchdog_handler = None
        self._watchdog_render_requested = False
        self._watchdog_last_event = 0.0
        self._watchdog_last_path = None
        self._watchdog_lock = None
        self._watchdog_stop = None
        self._watchdog_restart_requested = False

    # --- config helpers ---------------------------------------------------

    def _config(self, key, default=None):
        """Read a value from the extension's config section, never raising.

        Returns ``default`` when the section or key is absent so the renderer
        works even in an app that has not declared a ``pdoc`` section.
        """
        try:
            val = self.app.config.get(self._meta.config_section, key)
        except Exception:
            return default
        return val if val is not None else default

    def _resolve_docstrings_dirs(self):
        """Resolve the configured docstrings sources to filesystem paths.

        Accepts dotted module paths (e.g. `tokeo.templates.pdoc.docstrings`)
        or plain directories, mirroring how `templates` is resolved. The order
        is preserved, so a derived project listing its own package first wins.
        """
        if self._docstrings_dirs is not None:
            return self._docstrings_dirs

        dirs = []
        for entry in self._config('docstrings'):
            if os.path.isdir(entry):
                dirs.append(fs.abspath(entry))
                continue
            try:
                mod = importlib.import_module(entry)
            except Exception:
                self.app.log.debug(f'pdoc: docstrings source not found: {entry}')
                continue
            path = None
            if getattr(mod, '__file__', None):
                path = os.path.dirname(inspect.getfile(mod))
            elif getattr(mod, '__path__', None):
                path = list(mod.__path__)[0]
            if path and os.path.isdir(path):
                dirs.append(path)
        self._docstrings_dirs = dirs
        return dirs

    def docstrings(self, group, identifier):
        """Retrieve a docstring from external Markdown files.

        Loads `{dir}/{group}/{identifier}.md` from the configured docstrings
        directories and caches the result. Used by the extensions' decorator
        hooks (e.g. `docstrings('decorator', 'dramatiq.actor')`) to document
        what a decorator does without repeating it in every docstring.

        Returns the file content, or None when no matching file exists.
        """
        key = f'{group}/{identifier}'
        if key in self._docstrings_cache:
            return self._docstrings_cache[key]

        for dir in self._resolve_docstrings_dirs():
            try:
                with open(os.path.join(dir, group, f'{identifier}.md'), 'r') as f:
                    docstring = f.read()
                    self._docstrings_cache[key] = docstring
                    return docstring
            except Exception:
                pass

        return None

    def decorators(self, func):
        """Decorator metadata for a pdoc function, for use from templates.

        Wraps the function in `DecoratedFunction`, which AST-parses the source
        for decorators, asks the extensions (via the
        `tokeo_pdoc_render_decorator` hook) how to present each one, and
        appends the decorators' docstrings to the function's own docstring.

        Returns a list of `dict(decorator, params, docstring)`; empty when the
        function carries no (known) decorators.
        """
        try:
            from tokeo.core.utils.pdoc import DecoratedFunction

            decorated = DecoratedFunction(
                self.app,
                func,
                update_func_docstring=True,
                prepend_docstrings='\n\n---\n\n',
            )
            return decorated.decorators
        except Exception as err:
            self.app.log.debug(f'pdoc: decorator parsing failed: {err}')
            return []

    def set_modules(self, modules=None):
        """Set the modules this render documents.

        Called once at setup from the config and again from the CLI when
        positional modules are given, so everything below reads one settled
        list instead of resolving the override on every call.

        ### Args

        - **modules** (str|list): Module specs, or None for the default: the
            app package, its tests and the tokeo framework

        """
        if modules is None:
            self._modules = [self.app._meta.label, 'tests', 'tokeo']
        elif isinstance(modules, str):
            self._modules = modules.split()
        else:
            self._modules = list(modules)

    def set_show_config(self, show=None):
        """Set whether the yaml config page is rendered.

        Called once at setup from the config and again from the CLI for
        `--config` / `--no-config`, which override the setting for one run
        without writing it back to the config.

        ### Args

        - **show** (bool|str): Truthy to render the page, or None to take
            the configured value

        """
        if show is None:
            self._show_config = self._config('show_config')
        else:
            self._show_config = is_true(show)

    def _template_dirs(self):
        """Resolve configured template modules/paths to filesystem dirs.

        Accepts dotted module paths (e.g. `tokeo.templates.pdoc.html`) or
        absolute paths, mirroring the previous extension. The first existing
        directory wins as pdoc's single `template_directory`; the others are
        made available to Jinja2 as additional search paths.
        """

        dirs = []
        for entry in self._config('templates'):
            if os.path.isdir(entry):
                dirs.append(fs.abspath(entry))
                continue
            try:
                mod = importlib.import_module(entry)
            except Exception:
                self.app.log.debug(f'pdoc: template source not found: {entry}')
                continue
            # namespace packages carry no __file__; use __path__ instead.
            # regular packages/modules resolve via inspect.getfile.
            path = None
            if getattr(mod, '__file__', None):
                path = os.path.dirname(inspect.getfile(mod))
            elif getattr(mod, '__path__', None):
                path = list(mod.__path__)[0]
            if path and os.path.isdir(path):
                dirs.append(path)
            else:
                self.app.log.debug(f'pdoc: template dir not resolvable: {entry}')
        return dirs

    # --- rendering --------------------------------------------------------

    def render(self, clean=False, raise_on_error=True):
        """Render HTML documentation for the configured modules.

        ### Args

        - **clean** (bool): Wipe output_dir before rendering
        - **raise_on_error** (bool): Let a failure through. `--watch` passes
            False: a broken template or an unreadable config file must not
            end the watch session, since the next save usually fixes it. A
            plain `pdoc render` keeps failing loudly.

        """
        try:
            self._render(clean=clean)
            self.app.log.info(f'pdoc documentation updated in: {self._output_dir}')
        except Exception as err:
            if raise_on_error:
                # re-raise and abort if set
                raise
            self.app.log.error(f'pdoc render failed: {err}', exc_info=True)

    def _render(self, clean=False):
        import pdoc
        import pdoc.doc
        import pdoc.render

        # documenting a project from its root: make project-local packages win
        # over identically named installed ones (e.g. a local ``tests`` package
        # vs. one pulled in as a dependency), so their real docstrings show
        cwd = os.getcwd()
        if cwd not in sys.path:
            sys.path.insert(0, cwd)

        # pdoc is chatty during the module walk: it warns (often with a full
        # traceback) for every module it cannot import, every unresolved
        # ``__all__`` entry, type-annotation parse issues, suppressed subprocess
        # calls during import, and name ambiguities. For a doc build that is
        # just noise — the loader below records what it skipped and prints one
        # clean summary. Silence pdoc's own UserWarning/RuntimeWarning stream.
        warnings.filterwarnings('ignore', category=UserWarning)
        warnings.filterwarnings('ignore', category=RuntimeWarning)
        logging.getLogger('pdoc').setLevel(logging.ERROR)

        if clean and os.path.isdir(self._output_dir):
            shutil.rmtree(self._output_dir)
        os.makedirs(self._output_dir, exist_ok=True)

        template_dirs = self._template_dirs()
        primary_tpl = Path(template_dirs[0]) if template_dirs else None

        # pdoc render configuration
        pdoc.render.configure(
            docformat='markdown',
            show_source=True,
            math=False,
            mermaid=True,
            search=False,
            favicon=self._config('favicon'),
            template_directory=primary_tpl,
        )

        # additional template search paths: every configured directory must sit
        # BEFORE pdoc's bundled defaults, so a derived project (e.g. Spiral) can
        # override individual files while tokeo — and only then pdoc — supply the
        # rest. pdoc's configure() puts its own template dirs right after the
        # primary, so we insert the extras just after the primary (index 1+),
        # keeping the configured order and pushing pdoc's defaults to the back.
        if len(template_dirs) > 1:
            searchpath = pdoc.render.env.loader.searchpath
            for offset, extra in enumerate(template_dirs[1:]):
                searchpath.insert(1 + offset, extra)

        # --- custom globals available to every template ---
        pdoc.render.env.globals['app'] = self.app
        pdoc.render.env.globals['app_name'] = getattr(
            self.app._meta, 'label', 'app'
        )

        # branding, overridable from a derived project's [pdoc] config without
        # touching templates: `title` is the <title> suffix, `brand` the sidebar
        # label (falls back to the app label in the template). The favicon is
        # passed to pdoc.render.configure() above and honoured by its default
        # favicon block, so `pdoc.favicon` works out of the box too.
        # decorator metadata for the templates (also appends the decorators'
        # docstrings to the documented function, so call it before rendering
        # the function's docstring)
        pdoc.render.env.globals['decorators'] = self.decorators

        # is a config page going to be written? (index links to it)
        pdoc.render.env.globals['has_config'] = bool(
            self._show_config and getattr(self.app, 'env', None) is not None
        )

        pdoc.render.env.globals['html_lang'] = self._config('lang')
        pdoc.render.env.globals['doc_title'] = self._config('title')
        pdoc.render.env.globals['brand'] = self._config('brand')

        # versions for the footer credit ("generated with Tokeo … and pdoc …")
        # importlib.metadata is not free (~39 ms), so it stays local
        import importlib.metadata as _md
        from tokeo.core.version import get_version as _tokeo_version

        # the calls can fail for real: reading version metadata from a plain
        # source checkout raises PackageNotFoundError
        try:
            tokeo_version = _tokeo_version()
        except Exception:
            try:
                tokeo_version = _md.version('tokeo')
            except Exception:
                tokeo_version = ''
        pdoc.render.env.globals['tokeo_version'] = tokeo_version
        pdoc.render.env.globals['pdoc_version'] = getattr(
            pdoc, '__version__', ''
        )

        # pdoc collapses namespace packages (PEP 420, no __init__.py) and
        # regular packages both into "package"; expose a namespace check so
        # the template can label the three cases distinctly
        def _is_namespace(mod):
            obj = getattr(mod, 'obj', None)
            spec = getattr(obj, '__spec__', None)
            return bool(spec) and spec.origin is None and hasattr(obj, '__path__')
        pdoc.render.env.globals['is_namespace'] = _is_namespace

        # ancestors: the MRO above this class (excluding the class itself and
        # ``object``), capped at ``depth`` levels. pdoc only exposes the direct
        # bases; this restores the fuller chain the previous docs showed.
        def _ancestors(cls, depth):
            out = []
            for k in list(getattr(cls.obj, '__mro__', []))[1:]:
                if k is object:
                    continue
                mod = getattr(k, '__module__', '') or ''
                qual = getattr(k, '__qualname__', getattr(k, '__name__', ''))
                out.append((mod, qual, f'{mod}.{qual}' if mod else qual))
                if len(out) >= (depth or 0):
                    break
            return out
        pdoc.render.env.globals['ancestors'] = _ancestors

        # subclasses: the loaded direct subclasses (type.__subclasses__()).
        # Because the render imports every module first, subclasses defined in
        # other modules are present by the time a class is rendered — mirroring
        # the previous behaviour. Returned sorted by name.
        def _subclasses(cls):
            out = []
            try:
                subs = cls.obj.__subclasses__()
            except Exception:
                subs = []
            for k in subs:
                mod = getattr(k, '__module__', '') or ''
                qual = getattr(k, '__qualname__', getattr(k, '__name__', ''))
                out.append((mod, qual, f'{mod}.{qual}' if mod else qual))
            return sorted(out, key=lambda t: t[1].lower())
        pdoc.render.env.globals['subclasses'] = _subclasses

        pdoc.render.env.globals['ancestors_max_depth'] = int(
            self._config('ancestors_max_depth')
        )

        # own_init: the class's own __init__ member, but only when the class
        # actually defines one (not an inherited/generic constructor). Lets the
        # template show a meaningful constructor signature in the class header
        # while hiding the noise of inherited ``(*args, **kw)`` constructors.
        def _own_init(cls):
            if '__init__' in getattr(cls.obj, '__dict__', {}):
                for mem in cls.members.values():
                    if mem.name == '__init__':
                        return mem
            return None
        pdoc.render.env.globals['own_init'] = _own_init

        def _is_enum(cls):
            # true when the documented class derives from enum.Enum, so the
            # sidebar can label it "E" (enum) instead of "C" (class)
            # issubclass raises TypeError on odd objects, so the call is
            # guarded; the import cannot fail
            obj = getattr(cls, 'obj', None)
            try:
                return isinstance(obj, type) and issubclass(obj, enum.Enum)
            except Exception:
                return False
        pdoc.render.env.globals['is_enum'] = _is_enum

        # our own template head (head.html) already wires up mermaid and
        # highlighting; disable pdoc's built-in mermaid/math includes, which
        # target `main.pdoc` (a class our layout doesn't use) and would throw
        pdoc.render.env.globals['mermaid'] = False
        pdoc.render.env.globals['math'] = False

        # replace pdoc's markdown2-based to_html with Python-Markdown so the
        # admonition syntax (`!!! note`) renders to the same DOM pdoc3 emitted
        # (`<div class="admonition note">` + `admonition-title`)
        self._install_markdown_filter()

        # pre-render hook, kept for compatibility
        for res in self.app.hook.run('tokeo_pdoc_pre_render', self.app):
            pass

        module_names = self._modules

        # discover the full set of modules (incl. namespace subtrees) that
        # walk_specs would miss, then load each — preserving the order the
        # modules were configured in (roots appear as named, not alphabetical)
        ordered_specs = []
        seen = set()
        for name in module_names:
            for spec in sorted(self._discover(name)):
                if spec not in seen:
                    seen.add(spec)
                    ordered_specs.append(spec)

        all_modules = {}
        skipped = []
        for spec in ordered_specs:
            try:
                all_modules[spec] = pdoc.doc.Module.from_name(spec)
            except Exception:
                skipped.append(spec)
        if skipped:
            self.app.log.info(
                f'pdoc: skipped {len(skipped)} module(s) that could not be '
                f'imported (missing optional dependencies): '
                f'{", ".join(skipped)}'
            )

        # optional external-docstring injection (replaces md-injection)
        for mod in all_modules.values():
            self._inject_hash_colon_docstrings(mod)
            self._inject_external_docstrings(mod)

        # drop members inherited from undocumented external classes (e.g. a
        # subclass of a third-party base), so pages document the package's own
        # surface rather than the whole inherited API
        documented = set(all_modules.keys())
        # expose the documented module set so templates can hide members
        # inherited from classes that live outside the documentation
        pdoc.render.env.globals['all_modules'] = documented
        for mod in all_modules.values():
            if mod is not None:
                self._filter_external_inherited(mod, documented)

        # render each module
        for spec, mod in all_modules.items():
            if mod is None:
                continue
            # point the markdown filter at this module's directory so its
            # docstring's `.. include:: ./FILE.md` resolves relative to it
            if getattr(self, '_md_state', None) is not None:
                self._md_state['module_dir'] = self._module_source_dir(mod)
            html = pdoc.render.html_module(mod, all_modules)
            # pdoc's native flat convention: every module renders to
            # `dotted/name.html`. This keeps output paths consistent with
            # pdoc's internal relative_link generation. It differs from
            # pdoc3, which used `pkg/index.html` for packages; that is a
            # deliberate, documented divergence (links stay self-consistent).
            rel = spec.replace('.', '/') + '.html'
            out = Path(self._output_dir) / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding='utf-8')

        # index page
        index = pdoc.render.html_index(all_modules)
        if index:
            (Path(self._output_dir) / 'index.html').write_text(index, encoding='utf-8')

        # copy template assets (highlight.js, mermaid, hljs themes, css) into
        # the output dir so the pages' /assets/ references resolve
        self._copy_assets(template_dirs, self._output_dir)

        # optional config documentation page
        self._render_config_page(self._output_dir)

        # post-render hook: lets extensions restore what they swapped in
        # `tokeo_pdoc_pre_render` (e.g. dramatiq's real actor decorator)
        for res in self.app.hook.run('tokeo_pdoc_post_render', self.app):
            pass

    def _copy_assets(self, template_dirs, output_dir):
        """Copy each template dir's `assets/` subtree into `output/assets`."""
        for tpl_dir in reversed(template_dirs):
            assets = os.path.join(tpl_dir, 'assets')
            if os.path.isdir(assets):
                shutil.copytree(
                    assets, os.path.join(output_dir, 'assets'),
                    dirs_exist_ok=True,
                )

    def _config_settings(self, section, lines, parsed):
        """Slice a config file into its top-level settings.

        For every top-level yaml key this returns the comment block written
        directly above it (as Markdown) and the yaml source of the key itself,
        reproducing the prior pdoc3 `show_settings` behaviour. Files ending in
        `.local` are redacted: the structure is kept, every value replaced.

        ### Returns

        - **dict**: `{key: {'intro': str, 'source': str}}`, empty when the file
          holds no mapping.
        """
        import yaml

        if not isinstance(parsed, dict):
            return {}

        from tokeo.core.utils.dict import redact_data

        settings = {}
        for key in parsed:
            start = None
            for i, line in enumerate(lines):
                if line == f'{key}:' or line.startswith(f'{key}: '):
                    start = i
                    break
            if start is None:
                continue

            # comment block directly above the key, blank line terminates it
            top = start
            while top > 0 and lines[top - 1][:1] == '#':
                top -= 1
            intro = _strip_comment_markers('\n'.join(lines[top:start]))

            # the key's own block, up to the next top-level key or comment
            end = len(lines) - 1
            for j in range(start + 1, len(lines)):
                if _RE_BLOCK_END.match(lines[j]):
                    end = j - 1
                    break
            while end > start and lines[end] == '':
                end -= 1
            source = '\n'.join(lines[start:end + 1])

            if section.endswith('.local'):
                try:
                    source = yaml.dump(
                        redact_data(yaml.safe_load(source), '***'),
                        default_flow_style=False,
                        sort_keys=False,
                    ).rstrip('\n')
                except Exception as err:
                    source = f"# source hidden, could not be redacted\n# {err}"

            settings[key] = dict(intro=intro, source=source)

        return settings

    def _render_config_page(self, output_dir):
        """Render the app's YAML config files as a documentation page.

        Reproduces the prior pdoc3 `show_config` feature: it collects the
        config files cement actually loads (via appenv's resolver), reads
        each into `configdict[section] = {content: [lines], yaml: parsed}`,
        redacts `.local` files, and renders a synthetic `config/index.html`.
        Requires the appenv extension (`app.env`); skipped otherwise.
        """
        if not self._show_config:
            return
        env = getattr(self.app, 'env', None)
        if env is None:
            return

        import yaml
        from tokeo.ext.appenv import ENVIRONMENTS

        suffix = self.app._meta.config_file_suffix
        config_dir = env.APP_CONFIG_DIR
        configdict = {}
        for app_env in ('base', *ENVIRONMENTS):
            try:
                file_list = env.get_config_files(
                    app_env=app_env, app_config_file_suffix=suffix,
                )
            except Exception:
                continue
            for filename in file_list:
                if not os.path.isfile(filename):
                    continue
                section = os.path.relpath(filename, config_dir)
                if section.endswith(suffix):
                    section = section[: -len(suffix)]
                try:
                    with open(filename, 'r', encoding='utf-8') as f:
                        content = f.read()
                    lines = content.split('\n')
                    parsed = yaml.safe_load(content)
                    configdict[section] = {
                        'content': lines,
                        'yaml': parsed,
                        'description': _strip_comment_markers(
                            '\n'.join(lines[: _yaml_header_end(lines)])
                        ),
                        'settings': self._config_settings(
                            section, lines, parsed,
                        ),
                    }
                except Exception as err:
                    self.app.log.warning(
                        f'pdoc: config file "{section}": {err}'
                    )

        if not configdict:
            return

        # union of top-level yaml keys across all files
        configsettings = []
        for data in configdict.values():
            if data['yaml']:
                for key in data['yaml']:
                    if key not in configsettings:
                        configsettings.append(key)

        # top-level environment files in documentation order, used for the
        # intro section. `ENVIRONMENTS` already reads production, staging,
        # development, testing; entries appear only when the file exists.
        # `.d/` partials and `.local` overrides stay out on purpose: the
        # intro is meant to be a short, hand-written orientation.
        configenvs = [
            app_env
            for app_env in ('base', *ENVIRONMENTS)
            if app_env in configdict and configdict[app_env]['description']
        ]

        import pdoc.render
        pdoc.render.env.globals['configdict'] = configdict
        pdoc.render.env.globals['configsettings'] = configsettings
        pdoc.render.env.globals['configenvs'] = configenvs
        from tokeo.core.utils.dict import redact_data
        pdoc.render.env.globals['redact_data'] = redact_data

        try:
            tmpl = pdoc.render.env.get_template('config.html.jinja2')
            html = tmpl.render(app_name=self.app._meta.label)
            out = Path(output_dir) / 'config' / 'index.html'
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding='utf-8')
        except Exception as err:
            self.app.log.warning(f'pdoc: config page skipped: {err}')

    def _collect_hash_colon_docs(self, source):
        """Read Sphinx-style `#:` variable comments out of a source file.

        pdoc3 documented module and class variables through `#:` comments;
        pdoc 16 dropped that and only reads a string literal placed after the
        assignment. Everything written for pdoc3 would therefore lose its
        description without a word. This restores the pdoc3 behaviour by
        reading the comments straight from the source.

        Recognised, all as pdoc3 did:

        - a block of `#:` lines directly above the assignment, joined
        - a `#:` comment at the end of the assignment line
        - plain assignments, annotated ones (`x: int = 1`) and multiple
          targets (`a = b = 1`), at module level and inside classes

        ### Args

        - **source** (str): Full text of the module

        ### Returns

        - **dict**: Qualified name (`NAME` or `Class.NAME`) to description

        """

        try:
            tree = ast.parse(source)
        except Exception:
            return {}
        lines = source.split('\n')

        def comment_above(lineno):
            """Join the `#:` block sitting directly above line `lineno`."""
            out = []
            i = lineno - 2  # 0-based index of the line above
            while i >= 0:
                stripped = lines[i].strip()
                if not stripped.startswith('#:'):
                    break
                out.append(stripped[2:].strip())
                i -= 1
            return '\n'.join(reversed(out))

        def comment_behind(lineno):
            """Read a trailing `#:` comment off the assignment line itself."""
            try:
                line = lines[lineno - 1]
            except IndexError:
                return ''
            # a `#:` inside a string literal would be a false positive, so
            # only accept it when the part before it has balanced quotes
            head, sep, tail = line.partition('#:')
            if not sep:
                return ''
            if head.count("'") % 2 or head.count('"') % 2:
                return ''
            return tail.strip()

        def names_of(node):
            """Target names of an assignment, ignoring attributes and tuples."""
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            return [t.id for t in targets if isinstance(t, ast.Name)]

        docs = {}

        def walk(body, prefix=''):
            for node in body:
                if isinstance(node, ast.ClassDef):
                    walk(node.body, f'{prefix}{node.name}.')
                    continue
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                text = comment_above(node.lineno) or comment_behind(node.lineno)
                if not text:
                    continue
                for name in names_of(node):
                    docs[f'{prefix}{name}'] = text

        walk(tree.body)
        return docs

    def _inject_hash_colon_docstrings(self, module):
        """Fill empty variable docstrings from `#:` comments.

        Only members pdoc already found are touched, and only when their
        docstring is empty — a string literal after the assignment stays
        authoritative, exactly as under pdoc3.

        ### Args

        - **module** (pdoc.doc.Module): The module whose members get filled

        """
        source_file = getattr(module, 'source_file', None)
        if not source_file:
            return
        try:
            with open(source_file, 'r', encoding='utf-8') as handle:
                docs = self._collect_hash_colon_docs(handle.read())
        except Exception as err:
            self.app.log.debug(f'pdoc: cannot read "{source_file}": {err}')
            return
        if not docs:
            return

        def fill(members, prefix=''):
            for name, member in members.items():
                qualname = f'{prefix}{name}'
                sub = getattr(member, 'members', None)
                if sub:
                    fill(sub, f'{qualname}.')
                if getattr(member, 'docstring', None):
                    continue
                text = docs.get(qualname)
                if text and hasattr(member, 'docstring'):
                    member.docstring = text

        fill(module.members)

    def _inject_external_docstrings(self, module):
        """Give namespace packages a docstring from their ``__init__.md``.

        A package directory that carries only ``__init__.md`` (no
        ``__init__.py``) has no Python docstring, so pdoc would render it
        blank. Reading ``__init__.md`` as the module docstring reproduces the
        prior extension's behaviour and gives namespace packages (``tokeo``,
        ``tokeo.core``, ...) their intro text.

        ### Args

        - **module** (pdoc.doc.Module): The module whose docstring gets filled

        """
        obj = getattr(module, 'obj', None)

        # package intro from __init__.md when there is no real docstring
        for d in list(getattr(obj, '__path__', []) or []):
            if not os.path.isdir(d):
                continue
            has_py = os.path.isfile(os.path.join(d, '__init__.py'))
            md = os.path.join(d, '__init__.md')
            if not has_py and os.path.isfile(md) and \
                    not (module.docstring or '').strip():
                with open(md, encoding='utf-8') as f:
                    module.__dict__['docstring'] = f.read()
                break

    def _excludes(self):
        """Module prefixes to skip during discovery (from the ``exclude``
        config key). Submodule-level exclusion is additionally driven by each
        package's own ``__pdoc__`` declaration (see ``_discover``)."""
        ex = self._config('exclude')
        if isinstance(ex, str):
            ex = [ex]
        return list(ex)

    @staticmethod
    def _is_excluded(spec, excludes):
        # template placeholders are never valid modules
        if '{{' in spec or '}}' in spec:
            return True
        for pref in excludes:
            if spec == pref or spec.startswith(pref + '.'):
                return True
        return False

    def _discover(self, root, excludes=None):
        """Filesystem-based, namespace-aware module discovery.

        pdoc's walk_specs (and pkgutil.iter_modules) skip namespace
        sub-packages, i.e. directories without an ``__init__.py``. In the
        tokeo ecosystem whole subtrees live in the ``tokeo`` namespace
        (e.g. ``tokeo.core.ai`` is contributed by tokeo-fundi and
        ``tokeo.core`` carries no ``__init__.py``), so they would be
        invisible to standard discovery. This walks the package ``__path__``
        directories and descends into every subdirectory, regular or
        namespace, matching what the previous pdoc3-based extension saw.

        Entries under an excluded prefix, and any path carrying a
        ``{{ ... }}`` template placeholder, are skipped so code-generation
        template trees are never treated as importable modules.
        """

        if excludes is None:
            excludes = self._excludes()
        if self._is_excluded(root, excludes):
            return set()

        found = set()
        try:
            mod = importlib.import_module(root)
        except Exception:
            return {root}
        found.add(root)

        # honour the package's own `__pdoc__` overrides: an entry mapped to
        # False hides that submodule (pdoc's native mechanism). This is how a
        # package opts a subtree — e.g. code-generation templates — out of the
        # documentation, without the extension hard-coding anything.
        pdoc_over = getattr(mod, '__pdoc__', None) or {}

        for base in list(getattr(mod, '__path__', [])):
            if not os.path.isdir(base):
                continue
            for entry in os.listdir(base):
                if entry.startswith(('_', '.')):
                    continue
                if '{{' in entry or '}}' in entry:
                    continue
                name = entry[:-3] if entry.endswith('.py') else entry
                spec = f'{root}.{name}'
                if pdoc_over.get(name) is False or pdoc_over.get(spec) is False:
                    continue
                full = os.path.join(base, entry)
                if entry.endswith('.py') and entry != '__init__.py':
                    if not self._is_excluded(spec, excludes):
                        found.add(spec)
                elif os.path.isdir(full):
                    if self._is_excluded(spec, excludes):
                        continue
                    try:
                        children = os.listdir(full)
                    except OSError:
                        continue
                    if any(c.endswith('.py') for c in children) or \
                       any(os.path.isdir(os.path.join(full, c)) for c in children):
                        found |= self._discover(spec, excludes)
        return found

    def _filter_external_inherited(self, mod, documented):
        """Restrict each class's inherited members to documented origins.

        pdoc groups a class's inherited members by the class they come from
        (``inherited_members`` maps ``(module, qualname)`` to the members). A
        class that subclasses a third-party base (say invoke's ``Context``)
        thus carries that base's entire API. Keeping only origins whose module
        is part of the documented set mirrors the prior pdoc3 output, which
        did not surface members inherited from external, undocumented classes.
        ``inherited_members`` is a cached_property, so the filtered dict is
        written into the instance ``__dict__`` to take effect.
        """
        def keep(origin_module):
            return any(
                origin_module == d or origin_module.startswith(d + '.')
                or d.startswith(origin_module + '.')
                for d in documented
            )

        def walk(members):
            for member in members.values():
                if getattr(member, 'kind', None) != 'class':
                    continue
                try:
                    inherited = member.inherited_members
                except Exception:
                    continue
                filtered = {
                    origin: mems
                    for origin, mems in inherited.items()
                    if keep(origin[0])
                }
                if len(filtered) != len(inherited):
                    member.__dict__['inherited_members'] = filtered
                # recurse into nested classes
                if hasattr(member, 'members'):
                    walk(member.members)

        if hasattr(mod, 'members'):
            walk(mod.members)

    def _module_source_dir(self, mod):
        """Directory that holds a module's source, for include resolution.

        For a package this is the package directory (where sibling guide files
        like CONFIG.md live); for a plain module it is the directory of its
        source file. Returns None when neither can be determined.
        """
        obj = getattr(mod, 'obj', None)
        if obj is None:
            return None
        # package: first __path__ entry
        for d in list(getattr(obj, '__path__', []) or []):
            if os.path.isdir(d):
                return d
        # plain module: directory of its file
        f = getattr(obj, '__file__', None)
        if f and os.path.isfile(f):
            return os.path.dirname(f)
        return None

    def _install_markdown_filter(self):
        """Swap pdoc's to_html filter for Python-Markdown with admonitions,
        plus a docstring preprocessor for the reStructuredText directives the
        tokeo docstrings use.

        pdoc renders docstrings with markdown2, which understands neither the
        admonition syntax nor the directives the prior pdoc3 extension relied
        on. This reinstates Python-Markdown with the `admonition` extension and
        preprocesses two RST directives before conversion:

        - ``.. include:: ./FILE.md`` inlines an external markdown guide,
          resolved relative to the module's own directory.
        - ``.. note::`` / ``.. warning::`` / ``.. tip::`` (and the other
          admonition kinds) are rewritten to Python-Markdown's ``!!! kind``
          form so they render to the same ``<div class="admonition kind">``.
        """
        import pdoc.render

        # markdown is a declared dependency of tokeo, so it is here
        import markdown as pymd

        extensions = ['admonition', 'fenced_code', 'tables', 'attr_list', 'smarty']
        # SmartyPants: turn `--` into an en-dash and `...` into an ellipsis,
        # matching the prior output; leave quotes straight
        extension_configs = {
            'smarty': {
                'smart_dashes': True,
                'smart_ellipses': True,
                'smart_quotes': False,
                'smart_angled_quotes': False,
            },
        }
        admonitions = (
            'note', 'info', 'important', 'tip', 'hint', 'todo', 'success',
            'warning', 'versionadded', 'versionchanged', 'deprecated',
            'error', 'danger', 'caution',
        )
        kinds = '|'.join(admonitions)

        # Sphinx version directives carry a version argument that renders as a
        # descriptive title, matching the prior pdoc3 output
        version_titles = {
            'versionadded': 'Added in version:\u2002{}',
            'versionchanged': 'Changed in version:\u2002{}',
            'deprecated': 'Deprecated since version:\u2002{}',
        }

        def convert_admonitions(text):
            def repl(m):
                indent, kind, arg = m.group(1), m.group(2), m.group(3).strip()
                if kind in version_titles and arg:
                    title = version_titles[kind].format(arg)
                    return f'{indent}!!! {kind} "{title}"'
                if arg:
                    return f'{indent}!!! {kind} "{arg}"'
                return f'{indent}!!! {kind}'

            # `.. note::` (optionally with a title) -> `!!! note`
            return re.sub(
                rf'^([ \t]*)\.\.[ \t]+({kinds})::[ \t]*(.*)$',
                repl, text, flags=re.MULTILINE,
            )

        def resolve_includes(text, module_dir):
            def repl(m):
                rel = m.group(2).strip()
                if module_dir:
                    path = os.path.normpath(os.path.join(module_dir, rel))
                    if os.path.isfile(path):
                        with open(path, 'r', encoding='utf-8') as f:
                            return f.read()
                return ''
            return re.sub(
                r'^([ \t]*)\.\.[ \t]+include::[ \t]+(\S+)[ \t]*$',
                repl, text, flags=re.MULTILINE,
            )

        def strip_note_markers(text):
            # tokeo docstrings introduce a note detail with a leading ": " at
            # the start of a line; the marker is not meaningful Markdown and
            # would otherwise render literally, so drop it (keeping indentation).
            return re.sub(r'^([ \t]*): (?=\S)', r'\1', text, flags=re.MULTILINE)

        # bind the current module's directory for include resolution
        state = {'module_dir': None}

        import markupsafe

        def tokeo_to_html(text, *args, **kwargs):
            text = text or ''
            text = resolve_includes(text, state['module_dir'])
            text = convert_admonitions(text)
            text = strip_note_markers(text)
            html = pymd.markdown(text, extensions=extensions,
                                 extension_configs=extension_configs)
            # pdoc's env autoescapes; return a safe string so the generated
            # markup (admonition divs, code blocks) is not HTML-escaped
            return markupsafe.Markup(html)

        pdoc.render.env.filters['to_html'] = tokeo_to_html
        # expose the state so render() can set the current module dir
        self._md_state = state

    # --- watching ---------------------------------------------------------

    def _module_dirs(self):
        """Filesystem roots of the packages that get documented."""

        dirs = []
        for spec in self._modules:
            top = spec.split('.')[0]
            try:
                mod = importlib.import_module(top)
            except Exception:
                # not importable right now (broken edit, missing optional
                # dependency); a directory of that name below the project
                # root is still worth watching so the fix gets picked up
                cand = fs.abspath(top)
                if os.path.isdir(cand):
                    dirs.append(cand)
                continue
            paths = [p for p in (getattr(mod, '__path__', None) or [])
                     if os.path.isdir(p)]
            if paths:
                dirs.extend(paths)
            elif getattr(mod, '__file__', None):
                try:
                    dirs.append(os.path.dirname(inspect.getfile(mod)))
                except TypeError:
                    continue
        return dirs

    def _documented(self):
        """Readable names of everything this render documents.

        The module specs exactly as configured, plus `config` when the yaml
        config page is written. Used for the one-line summary; it says what is
        *documented*, which is not the same as what `--watch` observes — an
        installed `tokeo` is documented but never watched. The `--watch`
        detail is on debug level.

        ### Returns

        - **list**: Names in configured order, duplicates removed

        """
        # the specs as configured: `modules: [spiral.core, tests]` documents
        # exactly those two, so that is what the line has to say
        documented = list(dict.fromkeys(self._modules))
        # mirror the condition in `_render_config_page`, so the line never
        # claims a config page that was not written. `app.env` comes from the
        # optional appenv extension, hence getattr rather than attribute access
        if self._show_config and getattr(self.app, 'env', None):
            documented.append('config')
        return documented

    def _watch_dirs(self):
        """Every directory whose contents can change and be edited here.

        Modules, external docstring snippets and — when appenv is loaded —
        the config directory. Nested entries are collapsed into their parent
        so no directory is watched twice.

        Roots outside the project are dropped. `modules` documents the app,
        its tests **and** the framework, but an installed `tokeo` lives in
        site-packages and nobody edits it there. The rule adjusts itself:
        working on the framework in its own checkout puts `tokeo/` below the
        project root, so it is watched again.

        Template directories are left out on purpose: they hold `.jinja2`,
        `.html`, `.css` and font files, none of which match `watch_includes`.
        Adding `*.jinja2` there is therefore a config change plus this line.

        ### Returns

        - **tuple**: `(roots, outside)` — the directories to watch and the
          ones dropped for sitting outside the project

        """
        candidates = list(self._module_dirs())
        candidates.extend(self._resolve_docstrings_dirs() or [])
        env = getattr(self.app, 'env', None)
        config_dir = getattr(env, 'APP_CONFIG_DIR', None) if env else None
        if config_dir and os.path.isdir(config_dir):
            candidates.append(config_dir)

        project = fs.abspath(os.getcwd())
        dirs, outside = [], []
        for entry in sorted({fs.abspath(x) for x in candidates if x}, key=len):
            if not Path(entry).is_relative_to(project):
                outside.append(entry)
                continue
            if not any(Path(entry).is_relative_to(r) for r in dirs):
                dirs.append(entry)
        # logging is left to the caller, so the summary line can precede the
        # per-directory detail
        return dirs, outside

    def _setup_watchdog(self, dirs):
        """
        Set up watchdog observer to watch for file changes.

        Configures and starts the watchdog observer to monitor the given
        directories for changes that should trigger a re-render. One handler
        is scheduled recursively on every directory.

        ### Args

        - **dirs** (list): Directories to monitor recursively

        ### Notes

        - Uses configuration settings to determine which files to watch
        - Starts the observer in a separate thread to monitor for changes

        """
        includes = self._config('watch_includes')
        excludes = self._config('watch_excludes')
        # Convert string patterns to lists
        include_patterns = [p.strip() for p in str(includes).split(',') if p.strip()]
        exclude_patterns = [p.strip() for p in str(excludes).split(',') if p.strip()]
        # Create event handler that will request a re-render on file changes
        self._watchdog_handler = TokeoPdocWatchdogEventHandler(
            patterns=include_patterns,
            ignore_patterns=exclude_patterns,
            ignore_directories=True,
            case_sensitive=False,
            callback=self._watchdog_on_event,
        )
        # Create observer
        self._watchdog_observer = Observer()
        # Create scheduler
        for directory in dirs:
            self._watchdog_observer.schedule(self._watchdog_handler, directory, recursive=True)
        # Start the observer
        self._watchdog_observer.start()

    def _watchdog_on_event(self, event):
        """Record a detected change for the watch loop to pick up.

        Runs on the observer thread, so the bookkeeping is guarded by a lock.
        Only the timestamp and the last path are kept; what kind of change it
        was no longer matters, since every restart renders from scratch and
        never cleans.

        ### Args

        - **event** (FileSystemEvent): The file system event that was detected

        """

        path = getattr(event, 'dest_path', None) or event.src_path
        with self._watchdog_lock:
            self._watchdog_render_requested = True
            self._watchdog_last_event = monotonic()
            self._watchdog_last_path = path

    def _watch(self, restarted=False):
        """Start the observer and returns the monitor

        On a settled change the monitor stops the server, `serve()` returns,
        the command ends, and cement's `post_close` hook replaces the process
        (see `hotload`). Rendering again in a fresh interpreter is what makes
        changed docstrings visible at all: pdoc caches `Module.from_name` and
        Python keeps imported modules in `sys.modules`.

        ### Args

        - **restarted** (bool): Suppress the startup output. Set after a
            process replacement, where repeating it would bury the one line
            that says what changed

        ### Returns

        - **callable**: `_watch_file_changes`, watcher for file changes

        ### Raises

        - **TokeoPdocError**: When watchdog is missing or nothing to watch

        """
        dirs, outside = self._watch_dirs()
        if not dirs:
            raise TokeoPdocError(
                'pdoc --watch: nothing to watch. Check the "modules" setting '
                'in the [pdoc] config section.'
            )

        self._watchdog_lock = Lock()
        self._watchdog_stop = Event()
        self._watchdog_render_requested = False
        self._watchdog_last_event = 0.0
        self._watchdog_last_path = None
        self._watchdog_restart_requested = False

        # raises TokeoPdocError when watchdog is not installed, before the
        # observer is touched
        self._setup_watchdog(dirs)

        if not restarted:
            self.app.log.info('pdoc watching for changes on project files')
            # the paths are noise on info level, but the only way to tell why
            # an edit does nothing, so they go to debug rather than nowhere
            for directory in dirs:
                self.app.log.debug(f'pdoc watching {directory}')
            for directory in outside:
                self.app.log.debug(
                    f'pdoc not watching {directory} (outside the project root)'
                )

        # returns the watch handler
        return self._watch_file_changes

    def _watch_file_changes(self, httpd):
        """Wait for a settled change, then stop the server.

        Runs as a daemon thread started by `serve()`. Mirrors nicegui's
        `_watchdog_file_changes`, which polls the same flag and calls
        `fastapi_app.shutdown()`.

        Ends on its own in three cases: a settled change, the stop event set
        by `shutdown()`, or an unexpected error. It waits on the event rather
        than sleeping, so Ctrl+C does not leave it lingering for another
        interval — and the stop check shares the lock with the restart flag,
        so a change arriving during the shutdown cannot turn a quit into a
        restart.

        ### Args

        - **httpd** (ThreadingHTTPServer): The running server to stop

        """
        try:
            # inside the try on purpose: a nonsense value in the config would
            # otherwise kill this thread before the handler below exists, and
            # the session would look alive while nothing is being watched
            interval = float(self._config('watch_interval'))
            settle = float(self._config('watch_settle'))
            while not self._watchdog_stop.wait(interval):
                with self._watchdog_lock:
                    if self._watchdog_stop.is_set():
                        return
                    if not self._watchdog_render_requested:
                        continue
                    # an editor writes in bursts; wait for quiet first
                    if monotonic() - self._watchdog_last_event < settle:
                        continue
                    path = self._watchdog_last_path
                    self._watchdog_render_requested = False
                    # set under the same lock that shutdown() takes, so this
                    # is either fully before or fully after the stop
                    self._watchdog_restart_requested = True

                self.app.log.info(f'pdoc change detected: {path}')
                # hand over: stopping the server ends serve(), the command
                # returns, and the post_close hook replaces this process
                httpd.shutdown()
                return
        except Exception as err:
            # a dead monitor with a running server looks like a working
            # watch session but silently is not, so end the session instead
            self.app.log.error(
                f'pdoc watch monitor failed: {err}', exc_info=True
            )
            httpd.shutdown()

    def shutdown(self):
        """Clean up resources when the application shuts down.

        Stops the monitor thread and the watchdog observer, so neither is
        left behind. Called by the `pre_close` hook and a no-op for every
        command that never watched.

        Order matters: `pre_close` runs before `post_close`, so the monitor
        is stopped before `hotload` reads the restart flag. Without that a
        change detected right after Ctrl+C would turn a quit into a restart.
        """
        if self._watchdog_stop is not None and self._watchdog_lock is not None:
            with self._watchdog_lock:
                self._watchdog_stop.set()
        if self._watchdog_observer and self._watchdog_observer.is_alive():
            self._watchdog_observer.stop()
            self._watchdog_observer.join(timeout=1)
        self._watchdog_observer = None
        self._watchdog_handler = None

    def hotload(self):
        """Replace this process when `--watch` saw a change.

        Called by the `post_close` hook, after cement has torn everything
        down. `--clean` is dropped from the replayed command line on
        purpose: it means "wipe the output once at the start", not "wipe it
        on every rebuild".

        ### Notes

        - Performs a full process replacement, so caches, `sys.modules` and
          the observer all start from scratch
        - The listening socket does not survive the replacement: Python marks
          sockets non-inheritable, so the new process rebinds cleanly
        - No-op unless the watch loop asked for it

        """

        # only needed for the replacement itself and not free (~19 ms), so
        # it is not paid by every other command
        from multiprocessing.util import _exit_function as mp_exit_function

        if not self._watchdog_restart_requested:
            return
        # `--clean` means "wipe the output once at the start", not on every
        # rebuild; `--restart` tells the next process to skip the startup
        # output so only the line naming the change remains visible
        argv = [arg for arg in sys.argv if arg not in ['--clean', '--restart']]
        mp_exit_function()
        os.execv(sys.executable, [sys.executable] + argv + ['--restart'])

    # --- serving ----------------------------------------------------------

    def serve(self, watch=False, restarted=False):
        """Serve the rendered documentation directory over HTTP.

        The render step already produced static HTML in ``output_dir``. Serving
        that directory (rather than pdoc's live ``DocServer``) avoids importing
        every module again on each request — which would re-hit modules that
        cannot be imported (missing optional deps) or are not valid Python at
        all (code-generation templates), crashing the request handler.

        ### Args

        - **watch** (bool): Also watch for file changes. The observer is
            set up here and its monitor runs in a daemon thread beside the
            server, mirroring nicegui's `fastapi_app.on_startup(...)`: the
            server keeps the foreground, so Ctrl+C lands where expected and
            the monitor ends the session by stopping the server.
        - **restarted** (bool): Suppress the startup output, because this
            process came out of a `--watch` restart and the interesting line
            is the one naming the change.

        ### Raises

        - **TokeoPdocError**: When the port cannot be bound, or — with
            `watch` — when watchdog is missing or there is nothing to watch

        """
        import functools
        import http.server

        host = self._config('host')
        port = int(self._config('port'))

        if not os.path.isdir(self._output_dir):
            self.app.log.error(
                f'pdoc: nothing to serve at {self._output_dir}; run '
                f'"pdoc render" first'
            )
            return

        class _QuietHandler(http.server.SimpleHTTPRequestHandler):
            # keep the console quiet; no per-request access log line
            def log_message(self, *args, **kwargs):
                pass

        handler = functools.partial(_QuietHandler, directory=self._output_dir)
        try:
            httpd = http.server.ThreadingHTTPServer((host, port), handler)
        except OSError as err:
            # binding failed (port taken, address not assignable, privileged
            # port, …) — report it as a tokeo error so the CLI prints a clean
            # message instead of a socket traceback
            reason = err.strerror or str(err)
            raise TokeoPdocError(
                f'cannot serve the documentation on {host}:{port}: {reason}. '
                f'Another process may already use that port; stop it or set a '
                f'different "port" in the [pdoc] config section.'
            ) from err
        if not restarted:
            self.app.log.info(f'pdoc serving at http://{host}:{port}')
            self.app.log.info(
                f'pdoc explaining modules: '
                f'{", ".join(self._documented())}'
            )
        # the monitor needs the running server to stop it later, so the
        # observer can only be set up once `httpd` exists. Consequence worth
        # knowing: a failing `_watch()` (watchdog missing, nothing to watch)
        # raises after the line above already announced the URL, and `httpd`
        # is left unclosed because the `try/finally` starts below. Harmless
        # at process exit, but the messages read out of order.
        if watch:
            watch_daemon = self._watch(restarted=restarted)
            Thread(
                target=watch_daemon, args=(httpd,), daemon=True,
                name='tokeo-pdoc-watch-daemon',
            ).start()

        if not restarted:
            self.app.log.info('press Ctrl+C to stop')

        try:
            # blocks until Ctrl+C, or until the watch monitor calls
            # `httpd.shutdown()` after a settled file change
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()


class TokeoPdocController(Controller):
    """CLI: `tokeo pdoc render` / `tokeo pdoc serve` (unchanged surface)."""

    class Meta:
        label = 'pdoc'
        stacked_type = 'nested'
        stacked_on = 'base'
        help = 'generate and serve api documentation'
        description = 'Render and serve the project documentation with pdoc.'

    def _setup(self, app):
        super()._setup(app)

    @ex(
        help='render the documentation',
        description='Generate HTML documentation from Python docstrings.',
        arguments=[
            (['--clean'], dict(action='store_true',
                               help='delete output-dir recursively before rendering')),
            (['--serve'], dict(action='store_true',
                               help='serve the documentation after rendering')),
            (['--watch'], dict(action='store_true',
                               help='only with --serve: watch modules, docstrings '
                                    'and configs, and restart on every change')),
            # hotload suppress the startup output
            (['--restart'], dict(action='store_true', help=SUPPRESS)),
            (['--config'], dict(action='store_true',
                                help='add the yaml configuration page')),
            (['--no-config'], dict(action='store_true',
                                   help='skip the yaml configuration page')),
            (['modules'], dict(nargs='*',
                               help='modules to render; overrides the configured modules')),
        ],
    )
    def render(self):
        # both switches are always offered, whatever `show_config` says, so a
        # command line keeps working when the config default is flipped. They
        # override the setting for this run only; the config stays untouched
        if self.app.pargs.config and self.app.pargs.no_config:
            raise TokeoPdocError(
                'pdoc render: --config and --no-config exclude each other'
            )
        if self.app.pargs.config:
            self.app.pdoc.set_show_config(show=True)
        elif self.app.pargs.no_config:
            self.app.pdoc.set_show_config(show=False)
        if self.app.pargs.modules:
            self.app.pdoc.set_modules(self.app.pargs.modules)
        self.app.pdoc.render(
            clean=self.app.pargs.clean,
            raise_on_error=not self.app.pargs.serve or not self.app.pargs.watch,
        )
        if self.app.pargs.serve:
            # the modules were resolved and remembered by the render above
            self.app.pdoc.serve(
                watch=self.app.pargs.watch,
                restarted=self.app.pargs.restart,
            )

    @ex(
        help='start http service',
        description='Spin up an HTTP server to serve the generated documentation.',
        arguments=[],
    )
    def serve(self):
        self.app.pdoc.serve()


def tokeo_pdoc_extend_app(app):
    app.extend('pdoc', TokeoPdoc())
    app.pdoc._setup(app)


def tokeo_pdoc_render_decorator(app, func, decorator, args, kwargs):
    """Handle the generic decorators tokeo documents itself.

    Extensions document their own decorators by registering additional
    handlers for the `tokeo_pdoc_render_decorator` hook; this one covers the
    stdlib/cement decorators that belong to no particular extension.

    Returns `dict(decorator, params, docstring)` for a handled decorator, or
    None to let other handlers decide.
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


def tokeo_pdoc_shutdown(app):
    """Stop the watch observer when the application closes (`pre_close`)."""
    app.pdoc.shutdown()


def tokeo_pdoc_hotload(app):
    """Replace the process when `--watch` saw a change (`post_close`)."""
    app.pdoc.hotload()


def load(app):
    app.handler.register(TokeoPdocController)
    # hooks the other extensions plug into to document their decorators
    app.hook.define('tokeo_pdoc_pre_render')
    app.hook.define('tokeo_pdoc_post_render')
    app.hook.define('tokeo_pdoc_render_decorator')
    app.hook.register('tokeo_pdoc_render_decorator', tokeo_pdoc_render_decorator)
    app.hook.register('post_setup', tokeo_pdoc_extend_app)
    app.hook.register('pre_close', tokeo_pdoc_shutdown)
    app.hook.register('post_close', tokeo_pdoc_hotload)
