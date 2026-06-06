"""
Tokeo ai extension.

Wires the ai core into a Cement application: registers the built-in providers,
exposes the ``app.ai`` handler, and adds the ``ai`` command group for the
agentic and ai-facing side. An extension registers its own provider or tool
directly in its ``load`` (the registries are module-global, so no hook is
needed).

The technical namespace and the command group are both ``ai`` (this module,
the ``tokeo.core.ai`` package, and the ``ai`` config section). ``fundi`` is
the built-in local model.

```yaml
ai:
  default: mock
  tools:
    calc: { type: myapp.core.ai.tools.calc.TokeoAiCalcTool }
  profiles:
    mock:
      type: mock
      purpose: mocking
      tools:
        - calc
```

### Notes

    : With no selector given, ``app.ai`` uses ``ai.default``, which ships as
        the built-in ``mock`` profile, so ``ai ask`` answers out of the box
        without any model or server; there is no hard-coded code fallback.

"""

from cement import ex
from cement.core.meta import MetaMixin

import json
import importlib
from copy import deepcopy
from dataclasses import asdict

from tokeo.ext.argparse import Controller
from tokeo.core.ai import (
    TokeoAiError,
    ToolResult,
    find_profile,
)
from tokeo.core.ai.linter import TokeoAiLinter
from tokeo.core.ai.mock import TokeoAiMockProvider
from tokeo.core.ai.fundi import TokeoAiFundiProvider


class TokeoAi(MetaMixin):
    """
    AI handler for Tokeo applications, reached through ``app.ai``.

    Resolves a profile from the ``ai`` config section (by name, or by a field
    such as ``model`` or ``purpose``) and hands the resolved profile to the
    selected provider. Holds no mutable per-call state, so it is safe to use
    from several threads at once (for example dramatiq workers or scheduler
    jobs).

    ### Notes

    : The handler is registered as ``ai`` and is reached through ``app.ai``.
        It is a thin dispatcher over the registered providers, not a wrapper
        around any provider's full surface.

    """

    class Meta:
        """Handler meta-data and configuration defaults."""

        # Unique identifier for this handler
        label = 'tokeo.ai'

        # Configuration section name in the application config
        config_section = 'ai'

        # Default configuration settings
        config_defaults = dict(
            # profile used when a call names none
            default='mock',
            # named profiles; each binds a provider type to its details. the
            # built-in mock profile lets a fresh app answer without any setup.
            # core ships no tools, so the mock starts empty; a project adds
            # and activates its own tools (for example the calc demo tool)
            profiles=dict(
                mock=dict(
                    type='mock',
                    model='mock',
                    purpose='mocking',
                ),
            ),
        )

    def __init__(self, app, *args, **kw):
        """
        Initialize the ai handler.

        Stores the application reference only; the configuration is merged in
        the ``_setup`` method once the framework has loaded it.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments passed to the parent initializer
        - ****kw**: Keyword arguments passed to the parent initializer

        """
        super(TokeoAi, self).__init__(*args, **kw)
        self.app = app
        # the ai component registry lives on the handler: kind -> {name: cls}.
        # built-ins register here at post_setup; a project or third-party class
        # is named by a dotted ``type`` in the config and imported on demand.
        self._registry = {}
        # the handler instantiates resolved classes with the application on
        # first use and caches the (stateless) instances here
        self._provider_objs = {}
        self._tool_objs = {}

    def _setup(self, app):
        """
        Set up the ai handler.

        Called by the framework after the configuration has been loaded.
        Merges the default configuration so the ``ai`` section always exists,
        without overriding values the application provides.

        ### Args

        - **app**: The Tokeo application instance

        """
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)

    def _config(self, key, **kwargs):
        """
        Get a configuration value from the extension's config section.

        A simple wrapper around the application's ``config.get`` that uses the
        correct configuration section.

        ### Args

        - **key** (str): Configuration key to retrieve
        - ****kwargs**: Additional arguments passed to ``config.get``

        ### Returns

        - **Any**: Configuration value for the specified key

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def register(self, kind, name, cls):
        """
        Register a class under a short name within a kind.

        ### Args

        - **kind** (str): The component kind, e.g. ``provider`` or ``tool``
        - **name** (str): The short ``type`` name that selects this class
        - **cls** (type): The class; the handler instantiates it with the app

        """
        self._registry.setdefault(kind, {})[name] = cls

    def resolve(self, kind, type_value):
        """
        Resolve a config ``type`` to a class.

        A dotted ``type`` (one containing a ``.``) is imported on demand, so a
        project or third-party class needs no registration; a bare short name
        is looked up in the kind's registry (the built-ins tokeo ships).

        ### Args

        - **kind** (str): The component kind, e.g. ``provider`` or ``tool``
        - **type_value** (str): A short name or a dotted ``module.Class`` path

        ### Returns

        - **type**: The resolved class

        ### Raises

        - **TokeoAiError**: If a short name is unknown, or a dotted path cannot
            be imported

        """
        if not isinstance(type_value, str) or not type_value:
            raise TokeoAiError(f'ai {kind} "type" is missing or not a string')
        if '.' in type_value:
            module_path, _, attr = type_value.rpartition('.')
            if not module_path:
                raise TokeoAiError(f'ai {kind} type {type_value!r} is not a dotted path')
            try:
                return getattr(importlib.import_module(module_path), attr)
            except (ImportError, AttributeError) as err:
                raise TokeoAiError(f'cannot import ai {kind} type {type_value!r}: {err}')
        try:
            return self._registry[kind][type_value]
        except KeyError:
            known = ', '.join(sorted(self._registry.get(kind, {}))) or '(none)'
            raise TokeoAiError(f'unknown ai {kind} type {type_value!r}; known: {known}')

    def registry(self, kind=None):
        """
        Inspect the ai component registry through ``app.ai``.

        ### Args

        - **kind** (str | None): A single kind (``provider``, ``tool`` ...),
            or ``None`` for the whole registry

        ### Returns

        - **dict**: A deep copy; ``{name: class}`` for one kind, or
            ``{kind: {name: class}}`` for all kinds, so callers cannot mutate
            the registry (classes are atomic to ``deepcopy``, values stay
            shared)

        """
        if kind is None:
            return deepcopy(self._registry)
        return deepcopy(self._registry.get(kind, {}))

    def _resolve(self, profile=None, model=None, purpose=None):
        # at most one selector may be given; with none, use the configured
        # default (the built-in mock profile via the Meta config_defaults);
        # with no default at all this raises, there is no code fallback
        keys = {'profile': profile, 'model': model, 'purpose': purpose}
        active = {k: v for k, v in keys.items() if v is not None}
        if len(active) > 1:
            raise TokeoAiError('select a profile by only one of profile, model or purpose')
        if active:
            key, value = next(iter(active.items()))
            return find_profile(self.app, key, value)
        # no selector: use the configured default (which defaults to the mock
        # profile via the Meta config_defaults)
        default = self._config('default')
        if not default:
            raise TokeoAiError('no ai profile selected and no ai.default configured')
        return find_profile(self.app, 'profile', default)

    def _provider(self, provider_type):
        # instantiate the registered provider class with the application once
        # and reuse it; providers are stateless, so a racing double build is
        # harmless and needs no lock
        obj = self._provider_objs.get(provider_type)
        if obj is None:
            obj = self.resolve('provider', provider_type)(self.app)
            obj._setup(self.app)
            self._provider_objs[provider_type] = obj
        return obj

    def _tool(self, name):
        # instantiate the tool configured under ``ai.tools[name]`` once and
        # reuse it; its ``type`` (a built-in short name or a full dotted path)
        # resolves to a class. the same statelessness argument as for
        # providers applies
        obj = self._tool_objs.get(name)
        if obj is None:
            items, _ = self._tools_config()
            item = items.get(name)
            if not item or not item.get('type'):
                raise TokeoAiError(f'ai tool {name!r} is not configured under ai.tools')
            obj = self.resolve('tool', item['type'])(self.app)
            obj._setup(self.app)
            self._tool_objs[name] = obj
        return obj

    def _tools_config(self):
        # read the ``ai.tools`` map in the uniform config form: a value that
        # is a dict is an item ({type, options}); a value that is a list is a
        # named group of member names. return both maps
        try:
            section = self._config('tools')
        except Exception:
            section = None
        items, groups = {}, {}
        for name, value in (section or {}).items():
            if isinstance(value, list):
                groups[name] = list(value)
            elif isinstance(value, dict):
                items[name] = value
        return items, groups

    def _resolve_tools(self, names):
        # expand group names (lists under ``ai.tools``) to their member items;
        # an item name passes through. recursion lets a group contain groups;
        # the path set guards against cycles; order is preserved and
        # duplicates dropped
        _, groups = self._tools_config()
        resolved = []
        seen = set()

        def add(name, path):
            if name in groups:
                if name in path:
                    return
                for member in groups[name]:
                    add(member, path | {name})
            elif name not in seen:
                seen.add(name)
                resolved.append(name)

        for name in names or []:
            add(name, set())
        return resolved

    def _tool_specs(self, names):
        # build openai-style function specs from the tools' Meta; unknown
        # names are skipped, an empty or missing list yields no specs
        specs = []
        for name in names or []:
            try:
                tool = self._tool(name)
            except TokeoAiError:
                continue
            specs.append(dict(
                type='function',
                function=dict(
                    name=name,
                    description=tool._meta.description,
                    parameters=tool._meta.parameters,
                ),
            ))
        return specs

    def chat(self, messages, tools=None, profile=None, model=None, purpose=None, max_steps=6):
        """
        Run the agent loop and return the final ``ChatResult``.

        Resolves a profile, then calls the provider. While the model asks for
        tool calls, the activated tools are executed and their results are fed
        back, until the model answers or ``max_steps`` is reached. With no
        activated tool the loop degrades to a single, plain call.

        ### Args

        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **tools** (list | None): Tool names to activate; defaults to the
            profile's ``tools`` list
        - **profile** (str | None): Select a profile by name
        - **model** (str | None): Select the first enabled profile by model
        - **purpose** (str | None): Select the first enabled profile by purpose
        - **max_steps** (int): Maximum model calls before giving up

        ### Returns

        - **ChatResult**: The final response (no pending tool calls)

        ### Raises

        - **TokeoAiError**: If no profile resolves or it carries no ``type``

        """
        name, profile = self._resolve(profile=profile, model=model, purpose=purpose)
        provider_type = profile.get('type')
        if not provider_type:
            raise TokeoAiError(f'ai profile {name!r} is missing a type')
        provider = self._provider(provider_type)
        # tools are declared as items under ``ai.tools`` and activated when
        # listed on the profile (or passed in); a listed name may be a tool
        # item or a group (expanded here). without any, this is a plain chat
        requested = tools if tools is not None else profile.get('tools')
        specs = self._tool_specs(self._resolve_tools(requested))
        messages = list(messages)
        result = provider.chat(profile, messages, tools=specs)
        for _ in range(max_steps):
            if not result.tool_calls:
                return result
            messages.append(self._assistant_turn(result))
            for call in result.tool_calls:
                output = self._tool(call.name).exec(**(call.arguments or {}))
                # a tool may return a ToolResult or a plain string; only the
                # model-facing text goes back into the message history
                content = output.text if isinstance(output, ToolResult) else str(output)
                messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': content})
            result = provider.chat(profile, messages, tools=specs)
        return result

    def _assistant_turn(self, result):
        # rebuild the assistant message that requested the tool calls, in the
        # openai shape, so a real provider keeps a valid conversation
        return {
            'role': 'assistant',
            'content': result.text,
            'tool_calls': [
                {
                    'id': call.id,
                    'type': 'function',
                    'function': {'name': call.name, 'arguments': json.dumps(call.arguments or {})},
                }
                for call in result.tool_calls
            ],
        }

    def ask(self, prompt, tools=None, profile=None, model=None, purpose=None):
        """
        Send a single user prompt through the loop and return the reply text.

        ### Args

        - **prompt** (str): The user prompt
        - **tools** (list | None): Tool names to activate; defaults to the
            profile's ``tools`` list
        - **profile** (str | None): Select a profile by name
        - **model** (str | None): Select the first enabled profile by model
        - **purpose** (str | None): Select the first enabled profile by purpose

        ### Returns

        - **str**: The assistant text

        """
        messages = [{'role': 'user', 'content': prompt}]
        result = self.chat(messages, tools=tools, profile=profile, model=model, purpose=purpose)
        return result.text


class AiController(Controller):
    """
    Ai command group for the agentic and ai-facing commands.

    """

    class Meta:
        label = 'ai'
        stacked_type = 'nested'
        stacked_on = 'base'
        description = 'talk to the configured model and run agentic tasks'
        help = 'ai and agentic commands'

    @ex(
        help='ask the configured model a single prompt',
        arguments=[
            (['prompt'], dict(help='the prompt text', nargs='*')),
            (['--profile'], dict(help='select an ai profile by name', dest='profile')),
            (['--model'], dict(help='select an ai profile by model', dest='model')),
            (['--purpose'], dict(help='select an ai profile by purpose', dest='purpose')),
            (['--json'], dict(help='print the full result as json', action='store_true', dest='as_json')),
        ],
    )
    def ask(self):
        # join the words back into a single prompt, so it can be given without
        # quotes (for example: ai ask calc 2 + 3)
        prompt = ' '.join(self.app.pargs.prompt or [])
        if not prompt:
            raise TokeoAiError('no prompt given; usage: ai ask "your question"')
        result = self.app.ai.chat(
            [{'role': 'user', 'content': prompt}],
            profile=self.app.pargs.profile,
            model=self.app.pargs.model,
            purpose=self.app.pargs.purpose,
        )
        if self.app.pargs.as_json:
            self.app.print(json.dumps(asdict(result), indent=2))
        else:
            self.app.print(result.text)

    @ex(
        help='start an interactive, multi-turn chat session',
        arguments=[
            (['--profile'], dict(help='select an ai profile by name', dest='profile')),
            (['--model'], dict(help='select an ai profile by model', dest='model')),
            (['--purpose'], dict(help='select an ai profile by purpose', dest='purpose')),
        ],
    )
    def chat(self):
        # keep the running conversation so each turn sees the earlier ones;
        # an empty line or "exit" ends the session
        messages = []
        self.app.print('ai chat - empty line or "exit" to quit')
        while True:
            try:
                line = input('> ')
            except EOFError:
                break
            if line.strip() in ('', 'exit', 'quit'):
                break
            messages.append({'role': 'user', 'content': line})
            result = self.app.ai.chat(
                messages,
                profile=self.app.pargs.profile,
                model=self.app.pargs.model,
                purpose=self.app.pargs.purpose,
            )
            messages.append({'role': 'assistant', 'content': result.text})
            self.app.print(result.text)

    @ex(
        help='check the ai configuration for typos and broken references',
        arguments=[
            (['--strict'], dict(help='treat warnings as failures too', action='store_true', dest='strict')),
        ],
    )
    def lint(self):
        # report every form and reference problem at once, each with its
        # ``ai.<section>.<name>`` path; exit non-zero on errors, and on
        # warnings too when --strict is given
        issues = TokeoAiLinter(self.app).lint()
        for issue in issues:
            self.app.print(f'{issue.level}: {issue.path}: {issue.message}')
        if not issues:
            self.app.print('ai config ok')
            return
        errors = [issue for issue in issues if issue.level == 'error']
        warnings = [issue for issue in issues if issue.level == 'warning']
        self.app.print(f'ai config: {len(errors)} error(s), {len(warnings)} warning(s)')
        if errors or (self.app.pargs.strict and warnings):
            self.app.exit_code = 1


def ai_extend_app(app):
    """
    Cement post-setup hook: create ``app.ai`` and register the built-ins.

    Extends the application with the ai handler, registers the built-in
    providers on it, and sets it up, once every extension has been loaded and
    the configuration is available. A project or third-party provider/tool is
    not registered here; it is named by a dotted ``type`` in the config and
    imported on demand.

    ### Args

    - **app**: The application instance

    """
    app.extend('ai', TokeoAi(app))
    # built-in providers, available by short name without any configuration:
    # mock is the neutral test double, fundi the application's own local model.
    # core ships no tools; a project names its own tools by a dotted ``type``
    app.ai.register('provider', 'mock', TokeoAiMockProvider)
    app.ai.register('provider', 'fundi', TokeoAiFundiProvider)
    app.ai._setup(app)


def ai_lint_on_run(app):
    """
    Cement pre-run hook: lint the ai configuration before an ai command.

    Runs inside ``app.run`` (unlike ``post_setup``), so a typo in the ai config
    surfaces as a clean error through the application's own handler instead of a
    traceback. It guards only ``ai`` commands and steps aside for ``ai lint``
    and ``--help``, so the report and the help text stay reachable.

    ### Args

    - **app**: The application instance

    ### Raises

    - **TokeoAiError**: If the configuration has an error-level problem; the
        message lists every issue (warnings included) and points at ``ai lint``

    """
    argv = list(app.argv or [])
    tokens = [arg for arg in argv if not arg.startswith('-')]
    # only guard ai commands; let --help and the lint command itself through
    if not tokens or tokens[0] != 'ai':
        return
    if '-h' in argv or '--help' in argv or (len(tokens) >= 2 and tokens[1] == 'lint'):
        return
    issues = TokeoAiLinter(app).lint()
    if any(issue.level == 'error' for issue in issues):
        report = '\n'.join(f'  {issue.level}: {issue.path}: {issue.message}' for issue in issues)
        raise TokeoAiError(f'invalid ai configuration (run "ai lint" for the full report):\n{report}')


def load(app):
    """
    Load the ai extension.

    ### Args

    - **app**: The application instance

    ### Notes

    - Registers a post_setup hook that creates ``app.ai``, registers the
        built-in providers on it, and sets it up once the configuration is
        available
    - Registers a pre_run hook that lints the ai configuration before an ai
        command, so a typo fails with a clean message rather than a traceback
    - A project or third-party provider/tool is named by a dotted ``type`` in
        the config and imported on demand, so it needs no registration and no
        entry in the application extensions

    """
    app.hook.register('post_setup', ai_extend_app)
    app.hook.register('pre_run', ai_lint_on_run)
    app.handler.register(AiController)
