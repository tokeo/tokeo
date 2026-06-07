"""
Linter for the ai extension configuration.

Lints the ``ai`` configuration in one place, in two passes: a form pass
(allowed keys and value kinds) and a reference pass (every ``type`` resolves on
``app.ai``, and every name a profile or group points at exists). Nothing is
raised; the caller decides what to do with the reported issues.

It runs automatically when ``app.ai`` is set up, so a typo (a missing tool, an
unresolved ``type``) fails fast at startup, and is also exposed as the
``ai lint`` command, where ``--strict`` turns warnings into failures.
"""

import difflib
from dataclasses import dataclass

from tokeo.core.ai import TokeoAiError


# allowed keys per section, so an unknown key (a typo such as ``toolss``) is
# reported instead of being silently ignored. tools, agents, and guards share
# the uniform item form ({type, options}); the ``defaults`` block is checked
# here, agent option contents by the built-in element validators below
_AI_KEYS = {'defaults', 'profiles', 'tools', 'agents', 'guards'}
_DEFAULTS_KEYS = {'profile', 'agent'}
_PROFILE_KEYS = {
    'type', 'purpose', 'tools', 'enabled',
    'native_tools_call', 'tools_parser', 'options',
}
_ITEM_KEYS = {'type', 'options'}


@dataclass
class AiLintIssue:
    """
    A single ai configuration problem found by the linter.

    ### Notes

    - **path** (str): The ``ai.<section>.<name>`` location of the problem
    - **message** (str): What is wrong, with a hint where one applies
    - **level** (str): ``error`` (breaks resolution) or ``warning`` (an
        ignored value, usually a typo)

    """

    path: str
    message: str
    level: str = 'error'


class TokeoAiLinter:
    """
    Lints the ``ai`` configuration against the live registries.

    ### Notes

    - Construct it with the application, then call ``lint`` to get the issues
    - Every ``type`` is resolved through ``app.ai``, so a broken reference is
        caught here rather than on first use

    """

    def __init__(self, app):
        """
        Bind the linter to an application.

        ### Args

        - **app**: The application instance; its ``app.ai`` registries resolve
            every ``type``

        """
        self.app = app
        self.issues = []
        self._validators = {}
        # built-in element validations; a project or a later derivation adds
        # its own checks the same way (for example for a custom guard's
        # options)
        self.add_validator('agents', self._validate_item_form)
        self.add_validator('agents', self._validate_agent_options)
        self.add_validator('guards', self._validate_item_form)

    def add_validator(self, section, validator):
        """
        Register a validation call for the elements of one ``ai`` section.

        Every registered validator runs once per element of the section when
        ``lint`` walks it, so a subclass or a project extends the linter
        without touching its internals.

        ### Args

        - **section** (str): The ``ai`` section to validate (for example
            ``agents`` or ``guards``)
        - **validator** (callable): Called as ``validator(section, name,
            value)`` per element; it returns an iterable of ``AiLintIssue``
            entries (or ``None`` when it reports through the linter itself).
            It may also raise: a ``TokeoAiError`` becomes an error issue with
            its message at the element, any other exception is reported as a
            failed validator -- the lint run itself never crashes

        """
        self._validators.setdefault(section, []).append(validator)

    def lint(self):
        """
        Lint the ``ai`` configuration and return the issues found.

        ### Returns

        - **list**: ``AiLintIssue`` entries; an empty list means the
            configuration is sound

        """
        self.issues = []
        tools = self._value('tools') or {}
        profiles = self._value('profiles') or {}
        defaults = self._value('defaults')
        agents = self._value('agents') or {}
        self._lint_keys('ai', self._section_keys(), _AI_KEYS)
        self._lint_tools(tools)
        self._lint_profiles(profiles, tools)
        self._lint_defaults(defaults, profiles, agents)
        for section in self._validators:
            self._run_validators(section)
        return self.issues

    def _run_validators(self, section):
        # run the registered validators over every element of one section; a
        # non-mapping section is reported once instead of being walked
        elements = self._value(section) or {}
        if not isinstance(elements, dict):
            self._add(f'ai.{section}', 'must be a mapping of name to item')
            return
        for name, value in elements.items():
            for validator in self._validators.get(section, []):
                # a raising validator never crashes the lint run: a
                # TokeoAiError is a deliberate verdict and becomes an error
                # issue with its message, any other exception is a fault in
                # the validator itself and is reported as such
                try:
                    issues = validator(section, name, value)
                except TokeoAiError as err:
                    self._add(f'ai.{section}.{name}', str(err))
                    continue
                except Exception as err:
                    self._add(f'ai.{section}.{name}', f'validator failed: {type(err).__name__}: {err}')
                    continue
                self.issues.extend(issues or [])

    def _validate_item_form(self, section, name, item):
        # the uniform item form shared by agents and guards: a mapping with a
        # resolvable ``type`` and the component's own settings under
        # ``options``; settings keys inside options are the component's Meta
        # keys and stay unchecked here (a custom class declares its own)
        path = f'ai.{section}.{name}'
        if not isinstance(item, dict):
            self._add(path, 'must be an item (mapping with a "type")')
            return
        self._lint_keys(path, item, _ITEM_KEYS)
        self._lint_options(path, item)
        # the registry kind is the singular section name (agents -> agent)
        self._lint_type(section.rstrip('s'), path, item)

    def _validate_agent_options(self, section, name, item):
        # the base agent's known option keys: the tools selection points into
        # ``ai.tools``, the guards selection into ``ai.guards``, and the step
        # budget is a number; other keys may be a custom agent's own Meta keys
        if not isinstance(item, dict):
            return
        options = item.get('options')
        if not isinstance(options, dict):
            return
        path = f'ai.{section}.{name}'
        tool_names = set(self._value('tools') or {})
        self._lint_selection(path, options.get('tools'), tool_names)
        guards = options.get('guards')
        if guards is not None:
            if not isinstance(guards, list):
                self._add(f'{path}.guards', 'must be a list of guard names')
            else:
                known = set(self._value('guards') or {})
                for entry in guards:
                    if entry not in known:
                        self._add(f'{path}.guards', _unknown('guard', entry, known))
        max_steps = options.get('max_steps')
        if max_steps is not None and not isinstance(max_steps, int):
            self._add(f'{path}.max_steps', 'must be a number of model calls')

    def _add(self, path, message, level='error'):
        self.issues.append(AiLintIssue(path, message, level))

    def _value(self, key):
        # read one ``ai`` config value; a missing key raises in cement, so
        # swallow that and treat it as unset
        try:
            return self.app.config.get('ai', key)
        except Exception:
            return None

    def _section_keys(self):
        # the top-level keys present in the ``ai`` section, to spot typos
        try:
            return list(self.app.config.keys('ai'))
        except Exception:
            return []

    def _lint_keys(self, path, keys, allowed, level='warning'):
        # report any key outside the allowed set, with a closest-match hint
        for key in keys:
            if key not in allowed:
                self._add(f'{path}.{key}', _unknown('key', key, allowed), level)

    def _lint_options(self, path, item):
        # ``options`` is optional, but when present it carries the component's
        # own settings and must be a mapping
        options = item.get('options')
        if options is not None and not isinstance(options, dict):
            self._add(f'{path}.options', 'must be a mapping')

    def _lint_type(self, kind, path, item):
        # every item names a class by ``type``; resolving it on ``app.ai``
        # imports a dotted path or looks up a built-in short name
        type_value = item.get('type')
        if not type_value:
            self._add(path, f'missing {kind} "type"')
            return
        if not isinstance(type_value, str):
            self._add(f'{path}.type', 'must be a short name or a dotted path')
            return
        try:
            self.app.ai.resolve(kind, type_value)
        except TokeoAiError as err:
            self._add(f'{path}.type', str(err))

    def _lint_tools(self, tools):
        if not isinstance(tools, dict):
            if tools:
                self._add('ai.tools', 'must be a mapping of name to item or group')
            return
        # a dict value is an item, a list value is a group; else it is wrong
        items, groups = {}, {}
        for name, value in tools.items():
            if isinstance(value, list):
                groups[name] = value
            elif isinstance(value, dict):
                items[name] = value
            else:
                self._add(f'ai.tools.{name}', 'must be an item (mapping) or a group (list)')
        for name, item in items.items():
            path = f'ai.tools.{name}'
            self._lint_keys(path, item, _ITEM_KEYS)
            self._lint_options(path, item)
            self._lint_type('tool', path, item)
        known = set(items) | set(groups)
        for name, members in groups.items():
            path = f'ai.tools.{name}'
            if not all(isinstance(member, str) for member in members):
                self._add(path, 'a group must be a list of tool or group names')
                continue
            for member in members:
                if member not in known:
                    self._add(path, _unknown('tool or group', member, known))
        self._lint_cycles(groups)

    def _lint_cycles(self, groups):
        # report a group that transitively contains itself; without this a
        # cycle would just be silently broken at resolve time
        reported = set()

        def walk(name, chain):
            if name in chain:
                for member in chain[chain.index(name):]:
                    if member not in reported:
                        reported.add(member)
                        self._add(f'ai.tools.{member}', 'group has a cyclic membership')
                return
            for member in groups.get(name, []):
                if member in groups:
                    walk(member, chain + [name])

        for name in groups:
            walk(name, [])

    def _lint_profiles(self, profiles, tools):
        if not isinstance(profiles, dict):
            if profiles:
                self._add('ai.profiles', 'must be a mapping of name to profile')
            return
        tool_names = set(tools) if isinstance(tools, dict) else set()
        for name, profile in profiles.items():
            path = f'ai.profiles.{name}'
            if not isinstance(profile, dict):
                self._add(path, 'a profile must be a mapping')
                continue
            self._lint_keys(path, profile, _PROFILE_KEYS)
            self._lint_options(path, profile)
            self._lint_type('provider', path, profile)
            if 'enabled' in profile and not isinstance(profile['enabled'], bool):
                self._add(f'{path}.enabled', 'must be true or false')
            self._lint_selection(path, profile.get('tools'), tool_names)

    def _lint_selection(self, path, selection, tool_names):
        # a profile's ``tools`` is a selection list of item or group names from
        # ``ai.tools``, never a section of its own
        if selection is None:
            return
        if not isinstance(selection, list):
            self._add(f'{path}.tools', 'must be a list of tool or group names')
            return
        for entry in selection:
            if entry not in tool_names:
                self._add(f'{path}.tools', _unknown('tool or group', entry, tool_names))

    def _lint_defaults(self, defaults, profiles, agents):
        # the ``defaults`` block names the profile (model) and the agent
        # (composition) used when a call selects none. both are optional, but
        # when set must name an existing entry; a missing block is a warning
        if not defaults:
            self._add('ai.defaults', 'no default profile or agent configured', 'warning')
            return
        if not isinstance(defaults, dict):
            self._add('ai.defaults', 'must be a mapping')
            return
        self._lint_keys('ai.defaults', defaults, _DEFAULTS_KEYS)
        profile = defaults.get('profile')
        if profile:
            known = set(profiles) if isinstance(profiles, dict) else set()
            if profile not in known:
                self._add('ai.defaults.profile', _unknown('profile', profile, known))
        agent = defaults.get('agent')
        if agent:
            known = set(agents) if isinstance(agents, dict) else set()
            if agent not in known:
                self._add('ai.defaults.agent', _unknown('agent', agent, known))


def _unknown(what, name, known):
    # build an "unknown X 'name'; did you mean 'closest'?" message
    match = difflib.get_close_matches(str(name), [str(k) for k in known], n=1)
    suggestion = f'; did you mean {match[0]!r}?' if match else ''
    return f'unknown {what} {name!r}{suggestion}'
