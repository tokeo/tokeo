"""
Data shapes of the ai subsystem: typed results and the invocation that
travels the guard pipeline. Messages go in as plain OpenAI-style dicts;
these classes are what comes back out.
"""

from dataclasses import dataclass, field


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
        ```ToolResult(text=that_string)```.

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
        kept separate from the answer text. A non-standard field that only some
        endpoints (DeepSeek-R1, QwQ and similar local reasoning models) report
        under ```reasoning```/```reasoning_content```; empty when absent, so it
        is harvested when present, never required
    - **refusal** (str): The model's explicit refusal message, when it declines
        rather than answers (structured-outputs ```refusal``` field); empty
        otherwise. Distinct from an empty ```text```, so a caller can tell
        "declined" from "no content"
    - **tool_calls** (list): The ```ToolCall``` entries the model requested
    - **usage** (Usage | None): Token usage, when the provider reports it
    - **finish_reason** (str | None): Why the model stopped, when reported
    - **system_fingerprint** (str | None): The backend configuration fingerprint
        the endpoint reports, when present; with a fixed seed it identifies the
        exact backend state, so a changed value explains differing outputs
    - **raw** (dict | None): The unmodified provider response, kept so a
        caller can always inspect exactly what came back
    - **trace** (list): The ```Invocation``` records of the tool calls the loop
        ran, in order; empty when no guard pipeline was active

    """

    text: str = ''
    reasoning: str = ''
    refusal: str = ''
    tool_calls: list = field(default_factory=list)
    usage: Usage | None = None
    finish_reason: str | None = None
    system_fingerprint: str | None = None
    raw: dict | None = None
    trace: list = field(default_factory=list)


@dataclass
class Invocation:
    """
    A single tool call as it travels through the guard pipeline.

    Built by the handler for each requested tool call, then passed to the
    guards: the before-phase guards may set ```decision```/```reason``` to block
    it, the tool runs unless denied, and the after-phase guards see the
    ```result``` or ```error```. The object is mutable on purpose, so a guard can
    adjust the outcome (a later redact/truncate guard rewrites ```result```).

    ### Args

    - **id** (str): The provider-assigned tool-call id, echoed in the result
    - **name** (str): The tool the model wants to call
    - **arguments** (dict): The parsed arguments for the call
    - **parameters** (dict | None): The called tool's declared parameters
        schema, attached by the handler so a before guard can validate the
        arguments; ```None``` when the tool is unknown
    - **decision** (str): ```allow``` or ```deny```; a before guard may set it
    - **reason** (str | None): Why a guard denied or flagged the call
    - **result** (ToolResult | None): The tool's result when it ran
    - **error** (str | None): The error text when the tool raised
    - **sandbox** (str | None): The configured name of the sandbox the tool
        ran in (e.g. ```in_process```, ```jailed```, ```wasm_untrusted```), so
        the trace shows WHERE each call executed -- the honest-tier record.
        ```None``` until the call reaches a sandbox (a denied call never does)

    """

    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    parameters: dict | None = None
    decision: str = 'allow'
    reason: str | None = None
    result: ToolResult | None = None
    error: str | None = None
    sandbox: str | None = None
