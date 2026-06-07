"""
Tokeo ai extension.

Wires the ai core into a Cement application: registers the built-in providers,
exposes the ``app.ai`` handler, and adds the ``ai`` command group for the
agentic and ai-facing side. An extension registers its own provider, tool,
agent, or guard via ``app.ai.register`` (for example in a ``post_setup`` hook).

The technical namespace and the command group are both ``ai`` (this module,
the ``tokeo.core.ai`` package, and the ``ai`` config section).

Every configured component is an item in the uniform form ``{type, options}``:
``type`` names the class (a built-in short name or a dotted path), ``options``
carries the component's own settings. Profiles add their documented top-level
params (purpose, tools, enabled) around that form.

```yaml
ai:
  defaults:
    profile: mock          # model used when a call names none
    agent: none
  tools:
    calc:  { type: myapp.core.ai.tools.calc.TokeoAiCalcTool }
    notes: { type: myapp.core.ai.tools.notes.TokeoAiNotesTool }
  guards:
    audit: { type: audit }
    safe:
      type: policy
      options:
        deny: [shell]
  agents:
    audited:
      type: default
      options:
        tools:
          - notes          # combined with the profile's own tools (calc)
        guards:
          - safe           # the tool-call pipeline of this agent
          - audit
  profiles:
    mock:
      type: mock
      purpose: mocking
      tools:
        - calc
```

### Notes

    : With no selector given, ``app.ai`` uses ``ai.defaults.profile``, which
        ships as the built-in ``mock`` profile, so ``ai ask`` answers out of
        the box without any model or server; there is no hard-coded code
        fallback.

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
    Invocation,
    TokeoAiAgent,
)
from tokeo.core.ai.audit import TokeoAiAuditGuard
from tokeo.core.ai.policy import TokeoAiPolicyGuard
from tokeo.core.ai.validate import TokeoAiValidateGuard
from tokeo.core.ai.linter import TokeoAiLinter
from tokeo.core.ai.mock import TokeoAiMockProvider


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

        # base budgets for the loop when neither the call nor the selected
        # agent sets one; the single home for these defaults. max_steps caps
        # the tool rounds of one request (0 = unlimited); max_loops caps the
        # consecutive rounds without one successful call (0 = unlimited), so
        # a model stuck on denied or failing calls is stopped
        max_steps = 0
        max_loops = 3

        # Default configuration settings
        config_defaults = dict(
            # the default profile (model) and agent used when a call names
            # none; the built-in mock profile lets a fresh app answer at once
            defaults=dict(profile='mock'),
            # named profiles; each binds a provider type to its details. the
            # built-in mock profile lets a fresh app answer without any setup.
            # core ships no tools, so the mock starts empty; a project adds
            # and activates its own tools on a profile or an agent
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
        self._agent_objs = {}
        self._guard_objs = {}

    def _setup(self, app):
        """
        Set up the ai handler.

        Called by the framework after the configuration has been loaded.
        Merges the default configuration so the ``ai`` section always exists,
        then reads the section into the handler once. After this the
        operational methods work off these attributes and never read the
        configuration again.

        ### Args

        - **app**: The Tokeo application instance

        """
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        # pull the ai configuration into the handler once, at setup time
        self._defaults = self._config('defaults', fallback={}) or {}
        self._profiles = self._config('profiles', fallback={}) or {}
        self._agents = self._config('agents', fallback={}) or {}
        self._guards = self._config('guards', fallback={}) or {}
        # the ``ai.tools`` map uses the uniform form: a dict value is an item
        # ({type, options}), a list value is a named group of member names.
        # split it once into the two maps the loop works with
        self._tool_items, self._tool_groups = {}, {}
        for name, value in (self._config('tools', fallback={}) or {}).items():
            if isinstance(value, list):
                self._tool_groups[name] = list(value)
            elif isinstance(value, dict):
                self._tool_items[name] = value

    def _config(self, key, **kwargs):
        """
        Get a configuration value from the extension's config section.

        A simple wrapper around the application's ``config.get`` that uses the
        correct configuration section. Used only at setup time to read the
        configuration into the handler.

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
        # default profile (the built-in mock profile via the config_defaults);
        # with no default at all this raises, there is no code fallback
        keys = {'profile': profile, 'model': model, 'purpose': purpose}
        active = {k: v for k, v in keys.items() if v is not None}
        if len(active) > 1:
            raise TokeoAiError('select a profile by only one of profile, model or purpose')
        if active:
            key, value = next(iter(active.items()))
            return self._find_profile(key, value)
        # no selector: use the configured default profile
        default = self._defaults.get('profile')
        if not default:
            raise TokeoAiError('no ai profile selected and no ai.defaults.profile configured')
        return self._find_profile('profile', default)

    def _find_profile(self, key, value):
        """
        Resolve a single enabled profile by name or by a field value.

        ### Args

        - **key** (str): ``profile`` or ``name`` to match the profile name;
            any other key matches that field at the profile top level or in
            its ``options``
        - **value**: The value the key must equal

        ### Returns

        - **tuple**: ``(name, profile)`` of the matching profile

        ### Raises

        - **TokeoAiError**: If no enabled profile matches

        ### Notes

        - On a field match the first enabled profile in config order wins
        - A disabled profile (``enabled: false``) is skipped, so it is also
            not found by its name

        """
        if key in ('profile', 'name'):
            profile = self._profiles.get(value)
            if isinstance(profile, dict) and bool(profile.get('enabled', True)):
                return value, profile
            raise TokeoAiError(f'no enabled ai profile named {value!r}')
        for name, profile in self._profiles.items():
            if not (isinstance(profile, dict) and bool(profile.get('enabled', True))):
                continue
            # a selector is either a top-level field (purpose ...) or lives in
            # the provider options (model, base_url ...)
            field = profile[key] if key in profile else (profile.get('options') or {}).get(key)
            if field == value:
                return name, profile
        raise TokeoAiError(f'no enabled ai profile with {key}={value!r}')

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
        # reuse it; the item has the uniform form: ``type`` (a built-in short
        # name or a full dotted path) resolves to a class, built with the
        # application and the item's ``options`` as keyword arguments (the
        # keys override the tool's Meta defaults, like an agent or a guard).
        # the same statelessness argument as for providers applies
        obj = self._tool_objs.get(name)
        if obj is None:
            item = self._tool_items.get(name)
            if not item or not item.get('type'):
                raise TokeoAiError(f'ai tool {name!r} is not configured under ai.tools')
            settings = item.get('options') or {}
            obj = self.resolve('tool', item['type'])(self.app, **settings)
            obj._setup(self.app)
            self._tool_objs[name] = obj
        return obj

    def _resolve_tools(self, names):
        # expand group names (lists under ``ai.tools``) to their member items;
        # an item name passes through. recursion lets a group contain groups;
        # the path set guards against cycles; order is preserved and
        # duplicates dropped
        groups = self._tool_groups
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
        # build openai-style function specs from the tools' merged Meta;
        # unknown names are skipped, an empty or missing list yields no specs
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

    def _agent(self, name):
        # build the agent configured under ``ai.agents[name]`` once and reuse
        # it; the entry has the uniform item form: ``type`` (a built-in short
        # name or a dotted path) resolves to an agent class, built with the
        # application and the entry's ``options`` as keyword arguments (the
        # keys override the agent's Meta defaults)
        obj = self._agent_objs.get(name)
        if obj is None:
            config = self._agents.get(name)
            if not isinstance(config, dict) or not config.get('type'):
                raise TokeoAiError(f'ai agent {name!r} is not configured under ai.agents')
            settings = config.get('options') or {}
            obj = self.resolve('agent', config['type'])(self.app, **settings)
            obj._setup(self.app)
            self._agent_objs[name] = obj
        return obj

    def _agent_or_default(self, agent):
        # resolve the agent to run: an explicit name wins, else the configured
        # ``ai.defaults.agent`` (optional). with neither there is no agent, and
        # the caller uses only the profile's own tools
        if agent is None:
            agent = self._defaults.get('agent')
        if not agent:
            return None
        return self._agent(agent)

    def _guard(self, name):
        # build the guard configured under ``ai.guards[name]`` once and reuse
        # it; the entry has the uniform item form: ``type`` (a built-in short
        # name or a dotted path) resolves to a guard class, built with the
        # application and the entry's ``options`` as keyword arguments (the
        # keys override the guard's Meta defaults, like an agent). guards hold
        # no per-call state, so one cached instance is fine
        obj = self._guard_objs.get(name)
        if obj is None:
            item = self._guards.get(name)
            if not isinstance(item, dict) or not item.get('type'):
                raise TokeoAiError(f'ai guard {name!r} is not configured under ai.guards')
            settings = item.get('options') or {}
            obj = self.resolve('guard', item['type'])(self.app, **settings)
            obj._setup(self.app)
            self._guard_objs[name] = obj
        return obj

    def _resolve_guards(self, agent_obj):
        # the guards for the tool-call pipeline are selected on the agent;
        # with no agent (or none selected) there is no pipeline
        if agent_obj is None:
            return []
        return [self._guard(name) for name in agent_obj._meta.guards]

    def _run_guarded(self, call, before_guards, after_guards, trace):
        # run one tool call through the guard pipeline: the before guards may
        # deny it, the tool runs unless denied, and the after guards always run
        # (so a denial is recorded too). every outcome is appended to the trace
        invocation = Invocation(id=call.id, name=call.name, arguments=dict(call.arguments or {}))
        # attach the tool's declared schema so a before guard can validate
        # the arguments; an unknown tool leaves it None and still errors at
        # exec below, exactly as without guards
        try:
            invocation.parameters = self._tool(invocation.name)._meta.parameters
        except TokeoAiError:
            pass
        for guard in before_guards:
            guard.check(invocation)
            if invocation.decision == 'deny':
                break
        if invocation.decision != 'deny':
            try:
                output = self._tool(invocation.name).exec(**invocation.arguments)
                invocation.result = output if isinstance(output, ToolResult) else ToolResult(text=str(output))
            except Exception as err:
                # the pipeline is resilient: a failing tool is recorded and the
                # loop continues, instead of crashing the whole call
                invocation.error = f'{type(err).__name__}: {err}'
        for guard in after_guards:
            guard.check(invocation)
        trace.append(invocation)
        # the text fed back to the model for this call
        if invocation.decision == 'deny':
            return f'denied: {invocation.reason or "blocked by a guard"}'
        if invocation.error is not None:
            return f'error: {invocation.error}'
        return invocation.result.text if invocation.result is not None else ''

    def chat(self, messages, tools=None, profile=None, model=None, purpose=None, agent=None, max_steps=None, max_loops=None):
        """
        Run the agent loop and return the final ``ChatResult``.

        Resolves a profile (the model), then calls the provider. The active
        tools are the union of the profile's own tools and the agent's tools
        (the agent, the composition root, also sets the budgets). While the
        model asks for tool calls, the activated tools are executed and their
        results are fed back, until the model answers. With no activated tool
        the loop degrades to a single, plain call.

        Two budgets bound the loop and abort it with an error when reached:
        ``max_steps`` caps the tool rounds of one request, ``max_loops`` caps
        the consecutive rounds without one successful call (a model stuck on
        denied or failing calls). ``0`` means unlimited.

        ### Args

        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **tools** (list | None): Tool names to activate; the hard override
            for the profile-and-agent union when given
        - **profile** (str | None): Select a profile by name
        - **model** (str | None): Select the first enabled profile by model
        - **purpose** (str | None): Select the first enabled profile by purpose
        - **agent** (str | None): Select an agent by name; defaults to the
            configured ``ai.defaults.agent`` when one is set
        - **max_steps** (int | None): Maximum tool rounds, 0 for unlimited;
            defaults to the agent's budget, otherwise the framework default
        - **max_loops** (int | None): Maximum consecutive rounds without one
            successful call, 0 for unlimited; defaults to the agent's budget,
            otherwise the framework default

        ### Returns

        - **ChatResult**: The final response (no pending tool calls)

        ### Raises

        - **TokeoAiError**: If no profile resolves or it carries no ``type``,
            or when a budget aborts the execution

        """
        name, profile = self._resolve(profile=profile, model=model, purpose=purpose)
        provider_type = profile.get('type')
        if not provider_type:
            raise TokeoAiError(f'ai profile {name!r} is missing a type')
        provider = self._provider(provider_type)
        # the agent is the composition root: it adds tools and sets the step
        # budget, while the profile selects only the model. tools are declared
        # as items under ``ai.tools`` and a listed name may be a tool item or a
        # group. for the active tools an explicit tools= argument is the hard
        # override; otherwise the profile's own tools and the agent's tools
        # combine (``_resolve_tools`` keeps order and drops duplicates)
        agent_obj = self._agent_or_default(agent)
        if tools is not None:
            requested = tools
        else:
            requested = list(profile.get('tools') or [])
            if agent_obj is not None:
                requested = requested + list(agent_obj._meta.tools)
        if max_steps is None:
            # the agent's own budgets win; without one (None) the handler's
            # base defaults (Meta.max_steps / Meta.max_loops) apply
            budget = agent_obj._meta.max_steps if agent_obj is not None else None
            max_steps = budget if budget is not None else self._meta.max_steps
        if max_loops is None:
            budget = agent_obj._meta.max_loops if agent_obj is not None else None
            max_loops = budget if budget is not None else self._meta.max_loops
        specs = self._tool_specs(self._resolve_tools(requested))
        # guards (selected on the agent) wrap each tool call; with none, the
        # loop calls the tool directly, exactly as before, and collects no
        # trace. partition once: before guards may deny, after guards observe
        guards = self._resolve_guards(agent_obj)
        before_guards = [guard for guard in guards if guard._meta.phase == 'before']
        after_guards = [guard for guard in guards if guard._meta.phase == 'after']
        trace = []
        messages = list(messages)
        steps = 0
        failed_loops = 0
        result = provider.chat(profile, messages, tools=specs)
        while result.tool_calls:
            # max_steps caps the tool rounds of one request; 0 is unlimited.
            # reaching it aborts loudly: a silent empty answer hides the cause
            if max_steps and steps >= max_steps:
                raise TokeoAiError(f'ai max_steps ({max_steps}) reached, execution aborted')
            messages.append(self._assistant_turn(result))
            succeeded = False
            for call in result.tool_calls:
                if guards:
                    content = self._run_guarded(call, before_guards, after_guards, trace)
                    last = trace[-1]
                    ok = last.decision != 'deny' and last.error is None
                else:
                    # the lean path is as resilient as the guard pipeline: an
                    # unknown or failing tool becomes feedback the model may
                    # correct itself on, instead of an exception killing the
                    # whole loop
                    try:
                        output = self._tool(call.name).exec(**(call.arguments or {}))
                        # a tool may return a ToolResult or a plain string;
                        # only the model-facing text goes back into the history
                        content = output.text if isinstance(output, ToolResult) else str(output)
                        ok = True
                    except Exception as err:
                        content = f'error: {type(err).__name__}: {err}'
                        ok = False
                succeeded = succeeded or ok
                messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': content})
            steps += 1
            # max_loops caps the consecutive rounds without one successful
            # call (every call denied or failing); 0 is unlimited. this stops
            # a model stuck repeating broken calls, while any successful call
            # resets the counter and lets honest work continue
            failed_loops = 0 if succeeded else failed_loops + 1
            if max_loops and failed_loops >= max_loops:
                raise TokeoAiError(f'ai max_loops ({max_loops}) reached, execution aborted')
            result = provider.chat(profile, messages, tools=specs)
        result.trace = trace
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

    def ask(self, prompt, tools=None, profile=None, model=None, purpose=None, agent=None):
        """
        Send a single user prompt through the loop and return the reply text.

        ### Args

        - **prompt** (str): The user prompt
        - **tools** (list | None): Tool names to activate; the hard override
            for the profile-and-agent union when given
        - **profile** (str | None): Select a profile by name
        - **model** (str | None): Select the first enabled profile by model
        - **purpose** (str | None): Select the first enabled profile by purpose
        - **agent** (str | None): Select an agent by name; defaults to the
            configured ``ai.defaults.agent`` when one is set

        ### Returns

        - **str**: The assistant text

        """
        messages = [{'role': 'user', 'content': prompt}]
        result = self.chat(messages, tools=tools, profile=profile, model=model, purpose=purpose, agent=agent)
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
            (['--agent'], dict(help='select an ai agent by name', dest='agent')),
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
            agent=self.app.pargs.agent,
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
            (['--agent'], dict(help='select an ai agent by name', dest='agent')),
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
                agent=self.app.pargs.agent,
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
    # built-in provider, available by short name without any configuration:
    # mock is the neutral test double the framework needs for its own loop.
    # core ships no tools and no domain models; a project names its own
    # tools and providers by a dotted ``type``
    app.ai.register('provider', 'mock', TokeoAiMockProvider)
    # built-in agent: the default composition root; a project configures its
    # agents under ``ai.agents`` and selects one per call or via defaults.agent
    app.ai.register('agent', 'default', TokeoAiAgent)
    # built-in guard: the baseline audit guard; agents opt in via agent.guards
    app.ai.register('guard', 'audit', TokeoAiAuditGuard)
    # built-in guard: the baseline policy guard (allow/deny by tool name)
    app.ai.register('guard', 'policy', TokeoAiPolicyGuard)
    # built-in guard: the argument-schema check before a tool runs
    app.ai.register('guard', 'validate', TokeoAiValidateGuard)
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
