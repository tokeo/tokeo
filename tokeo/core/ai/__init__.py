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
list of messages into a normalized ``ChatResult``. Providers, tools and the
other services are registered as classes; the ``app.ai`` handler instantiates
them with the application. They keep no mutable per-call state, so they are
safe to use from several threads at once (for example dramatiq workers or
scheduler jobs).

### Notes

    : The local-first case points ``base_url`` at a server the user runs
        themselves (Ollama, llama.cpp, vLLM, MLX). Tokeo talks to that server
        but does not start or manage it.

"""

from dataclasses import dataclass, field

from cement.core.meta import MetaMixin

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
class ToolResult:
    """
    Result returned by a tool.

    ### Args

    - **text** (str): The model-facing result; only this enters the message
        history (truncated for the token budget by the result stage)
    - **data** (object | None): Optional structured detail for the trace or a
        ui; kept out of the message history to avoid duplicating large blobs

    ### Notes

    : A tool may also return a plain string, which is treated as
        ``ToolResult(text=that_string)``.

    """

    text: str = ''
    data: object = None


@dataclass
class ChatResult:
    """
    Normalized result of a chat call, uniform across providers.

    ### Args

    - **text** (str): Assistant message content; may be empty on a turn that
        only requests tool calls
    - **reasoning** (str): The model's reasoning/thinking, when available;
        kept separate from the answer text
    - **tool_calls** (list): The ``ToolCall`` entries the model requested
    - **usage** (Usage | None): Token usage, when the provider reports it
    - **finish_reason** (str | None): Why the model stopped, when reported
    - **raw** (dict | None): The unmodified provider response, kept so a
        caller can always inspect exactly what came back

    """

    text: str = ''
    reasoning: str = ''
    tool_calls: list = field(default_factory=list)
    usage: Usage | None = None
    finish_reason: str | None = None
    raw: dict | None = None


class TokeoAiProvider:
    """
    Base class for ai providers.

    A provider receives an already-resolved profile and returns a
    ``ChatResult``. It is registered as a class and instantiated with the
    application by the ``app.ai`` handler. It must not keep mutable per-call
    state, so that it can be called concurrently without locking.

    """

    def __init__(self, app, *args, **kw):
        """
        Initialize the provider.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: Keyword arguments for the parent initializer

        """
        self.app = app

    def _setup(self, app):
        """
        Set up the provider after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass

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


# providers register their class under a ``type`` name; the registry is filled
# once at load time and only read afterwards, so concurrent reads need no lock
_providers = {}


def register_provider(name, provider_class):
    """
    Register a provider class under a ``type`` name.

    ### Args

    - **name** (str): The ``type`` value that selects this provider
    - **provider_class** (type): The provider class; the handler instantiates
        it with the application

    """
    _providers[name] = provider_class


def get_provider(name):
    """
    Return the registered provider class for a ``type`` name.

    ### Args

    - **name** (str): The ``type`` to look up

    ### Returns

    - **type**: The registered provider class

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


class TokeoAiTool(MetaMixin):
    """
    Base class for agent tools.

    A tool is registered as a class and instantiated with the application by
    the ``app.ai`` handler, so it can read configuration, use ``app.db``, the
    vault, and hold resources. ``Meta`` carries the ``description`` and the
    JSON-schema ``parameters`` sent to the model; ``run`` does the work.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = ''

        # JSON-schema object describing the arguments
        parameters = {}

    def __init__(self, app, *args, **kw):
        """
        Initialize the tool.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: Keyword arguments for the parent initializer

        """
        super(TokeoAiTool, self).__init__(*args, **kw)
        self.app = app

    def _setup(self, app):
        """
        Set up the tool after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass

    def run(self, **arguments):
        """
        Execute the tool and return its result.

        ### Args

        - ****arguments**: The parsed arguments for the call

        ### Returns

        - **ToolResult | str**: The result; a plain string is treated as the
            model-facing text

        """
        raise NotImplementedError


# tools register their class under their name; like providers, the registry is
# filled once at load time and only read afterwards
_tools = {}


def register_tool(name, tool_class):
    """
    Register a tool class under its name.

    ### Args

    - **name** (str): The tool name the model uses to call it
    - **tool_class** (type): A ``TokeoAiTool`` subclass; the handler
        instantiates it with the application

    """
    _tools[name] = tool_class


def get_tool(name):
    """
    Return the registered tool class for a name.

    ### Args

    - **name** (str): The tool name to look up

    ### Returns

    - **type**: The registered tool class

    ### Raises

    - **TokeoAiError**: If no tool is registered for the name

    """
    try:
        return _tools[name]
    except KeyError:
        raise TokeoAiError(f'unknown ai tool {name!r}')


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
