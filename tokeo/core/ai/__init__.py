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
      options:
        model: qwen2.5
        base_url: http://localhost:11434/v1
      purpose: general
```

A provider is a dumb transport: given an already-resolved profile it turns a
list of messages into a normalized ``ChatResult``. Providers, tools and the
other services are registered as classes; the ``app.ai`` handler instantiates
them with the application. A ``type`` is either a short name from tokeo's
registry or a dotted ``module.Class`` path imported on demand; the registry is
reachable for inspection via ``app.ai.registry``. They keep no mutable per-call
state, so they are safe to use from several threads at once (for example
dramatiq workers or scheduler jobs).

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
    - **trace** (list): The ``Invocation`` records of the tool calls the loop
        ran, in order; empty when no guard pipeline was active

    """

    text: str = ''
    reasoning: str = ''
    tool_calls: list = field(default_factory=list)
    usage: Usage | None = None
    finish_reason: str | None = None
    raw: dict | None = None
    trace: list = field(default_factory=list)


@dataclass
class Invocation:
    """
    A single tool call as it travels through the guard pipeline.

    Built by the handler for each requested tool call, then passed to the
    guards: the before-phase guards may set ``decision``/``reason`` to block
    it, the tool runs unless denied, and the after-phase guards see the
    ``result`` or ``error``. The object is mutable on purpose, so a guard can
    adjust the outcome (a later redact/truncate guard rewrites ``result``).

    ### Args

    - **id** (str): The provider-assigned tool-call id, echoed in the result
    - **name** (str): The tool the model wants to call
    - **arguments** (dict): The parsed arguments for the call
    - **parameters** (dict | None): The called tool's declared parameters
        schema, attached by the handler so a before guard can validate the
        arguments; ``None`` when the tool is unknown
    - **decision** (str): ``allow`` or ``deny``; a before guard may set it
    - **reason** (str | None): Why a guard denied or flagged the call
    - **result** (ToolResult | None): The tool's result when it ran
    - **error** (str | None): The error text when the tool raised

    """

    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    parameters: dict | None = None
    decision: str = 'allow'
    reason: str | None = None
    result: ToolResult | None = None
    error: str | None = None


class TokeoAiProvider:
    """
    Base class for ai providers.

    A provider receives an already-resolved profile and returns a
    ``ChatResult``. Its class is resolved from the profile ``type`` (a built-in
    short name or a dotted path) and instantiated with the application by the
    ``app.ai`` handler. It must not keep mutable per-call state, so that it can
    be called concurrently without locking.

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


class TokeoAiTool(MetaMixin):
    """
    Base class for agent tools.

    A tool's class is resolved from its ``ai.tools`` item ``type`` (a built-in
    short name or a dotted path) and instantiated with the application by the
    ``app.ai`` handler, so it can read configuration, use ``app.db``, the
    vault, and hold resources. ``Meta`` declares the ``description`` and the
    JSON-schema ``parameters`` sent to the model; a subclass overrides those
    keys and ``exec`` does the work. The handler reads them from ``_meta``.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # short description the model sees
        description = ''

        # json-schema object describing the arguments the model may pass
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

    def exec(self, **arguments):
        """
        Execute the tool and return its result.

        ### Args

        - ****arguments**: The parsed arguments for the call

        ### Returns

        - **ToolResult | str**: The result; a plain string is treated as the
            model-facing text

        """
        raise NotImplementedError


class TokeoAiAgent(MetaMixin):
    """
    Base class for agents, the composition root of an ai call.

    An agent binds the building blocks of a task together: which tools are
    active and how many model calls the loop may take. The model itself is
    not part of the agent; it is bound late through the selected profile, so
    the same agent can run against the mock, a local model, or a hosted one.
    Its class is resolved from the ``ai.agents`` item ``type`` (a built-in
    short name or a dotted path) by the ``app.ai`` handler, which passes the
    agent's configuration entry as keyword arguments.

    ### Notes

    : ``Meta`` declares the configurable keys (``tools``, ``guards``,
        ``max_steps``) with neutral defaults; the ``options`` of the
        ``ai.agents`` entry override them at build time (the cement Meta
        keyword override), and they are read from ``_meta``.

    """

    class Meta:
        """Agent composition, overridden per agent by its entry's options."""

        # the tool selection (item or group names); merged with the profile's
        tools = []

        # the guard selection (guard names) for the tool-call pipeline
        guards = []

        # per-agent step budget; None means use the handler's base default
        max_steps = None

    def __init__(self, app, *args, **kw):
        """
        Initialize the agent.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: The agent's config entry; keys matching ``Meta`` override
            its defaults

        """
        super(TokeoAiAgent, self).__init__(*args, **kw)
        self.app = app

    def _setup(self, app):
        """
        Set up the agent after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass


class TokeoAiGuard(MetaMixin):
    """
    Base class for guards in the tool-call pipeline.

    A guard inspects, and may shape, a tool call as an ``Invocation`` travels
    through the pipeline. ``Meta.phase`` decides when it runs: a ``before``
    guard runs pre-exec and may deny the call (set ``decision``/``reason``); an
    ``after`` guard runs post-exec and sees the ``result`` or ``error`` (and
    always runs, so it records a denial too). Guards are selected per agent
    (``agent.guards``); with none selected the loop calls the tool directly.

    Its class is resolved from the ``ai.guards`` item ``type`` (a built-in
    short name or a dotted path) and instantiated with the application and the
    item's ``options`` as Meta overrides by the ``app.ai`` handler. Like a
    provider, it holds no mutable per-call state.

    """

    class Meta:
        """Guard meta-data."""

        # 'before' runs pre-exec and may deny; 'after' runs post-exec
        phase = 'after'

    def __init__(self, app, *args, **kw):
        """
        Initialize the guard.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: Keyword arguments for the parent initializer

        """
        super(TokeoAiGuard, self).__init__(*args, **kw)
        self.app = app

    def _setup(self, app):
        """
        Set up the guard after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass

    def check(self, invocation):
        """
        Inspect and possibly shape an invocation.

        ### Args

        - **invocation** (Invocation): The tool call passing through the
            pipeline; a before guard may set ``decision``/``reason`` to deny
            it, an after guard may read or adjust ``result``/``error``

        """
        raise NotImplementedError
