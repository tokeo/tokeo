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
# reported instead of being silently ignored
_AI_KEYS = {'default', 'profiles', 'tools'}
_PROFILE_KEYS = {
    'type', 'purpose', 'tools', 'enabled',
    'native_tools_call', 'tools_parser', 'options',
}
_TOOL_ITEM_KEYS = {'type', 'options'}


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
        default = self._value('default')
        self._lint_keys('ai', self._section_keys(), _AI_KEYS)
        self._lint_tools(tools)
        self._lint_profiles(profiles, tools)
        self._lint_default(default, profiles)
        return self.issues

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
            self._lint_keys(path, item, _TOOL_ITEM_KEYS)
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

    def _lint_default(self, default, profiles):
        # the default may be omitted if every call selects a profile explicitly,
        # so a missing default is a warning; a default naming nothing is an error
        if not default:
            self._add('ai.default', 'no default profile configured', 'warning')
            return
        if isinstance(profiles, dict) and default not in profiles:
            self._add('ai.default', _unknown('profile', default, set(profiles)))


def _unknown(what, name, known):
    # build an "unknown X 'name'; did you mean 'closest'?" message
    match = difflib.get_close_matches(str(name), [str(k) for k in known], n=1)
    suggestion = f'; did you mean {match[0]!r}?' if match else ''
    return f'unknown {what} {name!r}{suggestion}'
