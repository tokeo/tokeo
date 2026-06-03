"""
AI provider core for Tokeo applications.

A small, dependency-light layer for talking to chat-completion LLMs. The
design mirrors the vault: named profiles live in the ``ai`` config section,
each profile selects a registered provider through its ``type``, and the
remaining keys are provider specific.

```yaml
ai:
  default: assistant
  profiles:
    assistant:
      type: openai
      model: qwen2.5
      base_url: http://localhost:11434/v1
      purpose: general
```

A provider is a dumb transport: given an already-resolved profile it turns a
list of messages into a normalized ``ChatResult``. Providers keep no mutable
per-call state, so they are safe to use from several threads at once (for
example dramatiq workers or scheduler jobs).

### Notes

    : The local-first case points ``base_url`` at a server the user runs
        themselves (Ollama, llama.cpp, vLLM, MLX). Tokeo talks to that server
        but does not start or manage it.

"""

from dataclasses import dataclass, field

from tokeo.core.exc import TokeoError


class TokeoAiError(TokeoError):
    """Raised when an ai profile or provider cannot be resolved."""


@dataclass
class Usage:
    """
    Token usage reported for a single chat call.

    ### Args

    - **prompt_tokens** (int): Tokens consumed by the prompt
    - **completion_tokens** (int): Tokens produced in the completion
    - **total_tokens** (int): Total as reported by the provider

    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ToolCall:
    """
    A single tool call requested by the model.

    ### Args

    - **id** (str): Provider-assigned id, echoed back with the tool result
    - **name** (str): Name of the tool the model wants to call
    - **arguments** (dict): Parsed arguments for the call

    """

    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ChatResult:
    """
    Normalized result of a chat call, uniform across providers.

    ### Args

    - **text** (str): Assistant message content; may be empty on a turn that
        only requests tool calls
    - **tool_calls** (list): The ``ToolCall`` entries the model requested
    - **usage** (Usage | None): Token usage, when the provider reports it
    - **finish_reason** (str | None): Why the model stopped, when reported
    - **raw** (dict | None): The unmodified provider response, kept so a
        caller can always inspect exactly what came back

    """

    text: str = ''
    tool_calls: list = field(default_factory=list)
    usage: Usage | None = None
    finish_reason: str | None = None
    raw: dict | None = None


class Provider:
    """
    Base class for ai providers.

    A provider receives an already-resolved profile and returns a
    ``ChatResult``. It must not keep mutable per-call state, so that it can
    be called concurrently without locking.

    """

    def chat(self, profile, messages, tools=None):
        """
        Send messages to the model and return a normalized result.

        ### Args

        - **profile** (dict): The resolved profile; carries ``model`` and any
            provider-specific keys (such as ``base_url`` and ``key``)
        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **tools** (list|None): Optional tool definitions for the call

        ### Returns

        - **ChatResult**: The normalized response

        """
        raise NotImplementedError


# providers register under their ``type`` name; the registry is filled once at
# load time and only read afterwards, so concurrent reads need no locking
_providers = {}


def register_provider(name, provider):
    """
    Register a provider instance under a ``type`` name.

    ### Args

    - **name** (str): The ``type`` value that selects this provider
    - **provider** (Provider): The provider instance

    """
    _providers[name] = provider


def get_provider(name):
    """
    Return the registered provider for a ``type`` name.

    ### Args

    - **name** (str): The ``type`` to look up

    ### Returns

    - **Provider**: The registered provider instance

    ### Raises

    - **TokeoAiError**: If no provider is registered for the name

    """
    try:
        return _providers[name]
    except KeyError:
        raise TokeoAiError(f'unknown ai provider {name!r}')


def _enabled(profile):
    # a profile is available unless it explicitly turns itself off; this lets
    # a single session drop a profile via config or an env override
    return bool(profile.get('enabled', True))


def find_profile(app, key, value):
    """
    Resolve a single enabled profile by name or by a field value.

    ### Args

    - **app**: The application instance
    - **key** (str): ``profile`` or ``name`` to match the profile name; any
        other key matches that field within the profile
    - **value**: The value the key must equal

    ### Returns

    - **tuple**: ``(name, profile)`` of the matching profile

    ### Raises

    - **TokeoAiError**: If no enabled profile matches

    ### Notes

    - On a field match the first enabled profile in configuration order wins
    - A disabled profile (``enabled: false``) is skipped, so it is also not
        found by its name

    """
    try:
        profiles = app.config.get('ai', 'profiles') or {}
    except Exception:
        profiles = {}
    if key in ('profile', 'name'):
        profile = profiles.get(value)
        if isinstance(profile, dict) and _enabled(profile):
            return value, profile
        raise TokeoAiError(f'no enabled ai profile named {value!r}')
    for name, profile in profiles.items():
        if isinstance(profile, dict) and _enabled(profile) and profile.get(key) == value:
            return name, profile
    raise TokeoAiError(f'no enabled ai profile with {key}={value!r}')
