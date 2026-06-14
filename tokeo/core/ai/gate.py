"""
The gate base classes: a checkpoint that may halt the loop and decide.

A guard is automatic policy around a tool call; a sandbox is where a tool runs.
A gate is a different thing: a human-in-the-loop checkpoint that can stop the
loop before something consequential happens, with a default, a timeout and a
notion of learned consent. It is built from two orthogonal axes composed
together, so each axis stays small and any rule pairs with any placement:

- the RULE (```TokeoAiGateRule```) decides, placement-independent: given a
    context it returns a ```GateResult``` (admit or not, with a reason). The
    deny rule always refuses; later rules ask the user, remember consent, etc.
- the PLACEMENT (```TokeoAiGate``` and its two derivations) knows WHERE in the
    loop it sits and WHAT context it is fed: ```TokeoAiPromptGate``` runs before
    a model call and sees the messages; ```TokeoAiToolGate``` runs before a tool
    runs and sees the tool call. A placement holds a rule and delegates to it.

A concrete, configurable gate is a thin activator that fixes a placement and a
rule together (e.g. ```TokeoAiToolDenyGate``` = tool placement + deny rule).
Gates are selected per agent (```agent.gates```), exactly like guards; with no
agent there is no pipeline and no gate -- talking to a model with ```agent:
null``` is the deliberate raw path, with no validation of inputs or outputs and
so no confirm either.
"""

from dataclasses import dataclass

from cement.core.meta import MetaMixin


@dataclass
class PromptContext:
    """
    The context a prompt gate feeds its rule: the pending model request.

    Built before a ```provider.chat``` call, so it carries the messages about to
    be sent and an estimated token count -- enough for a rule to decide on the
    outbound data (what leaves the machine, and what it will cost). There is no
    tool call yet; that is why a gate context is not an ```Invocation```.

    ### Args

    - **messages** (list): The messages about to be sent to the model
    - **tokens** (int | None): An estimated token count for the request, or
        ```None``` when no estimate is available

    """

    messages: list
    tokens: int | None = None


@dataclass
class ToolContext:
    """
    The context a tool gate feeds its rule: the concrete tool call.

    Built inside the tool-call pipeline, after the before guards and before the
    sandbox runs the call, so it carries the invocation (the tool name and the
    parsed arguments). A rule decides on what will actually run.

    ### Args

    - **invocation** (Invocation): The tool call about to run

    """

    invocation: object


@dataclass
class GateResult:
    """
    The outcome of a gate's decision: admit the step, or stop it.

    A gate cannot mutate an ```Invocation``` the way a guard does, because a
    prompt gate runs before any tool call exists -- there is only the pending
    model request. So a gate returns this small value instead, the same shape
    in both placements: ```admit``` says whether the step may proceed, and
    ```reason``` carries why it was stopped (for the audit trail and, for a tool
    gate, the ```denied: ...``` text fed back to the model).

    ### Args

    - **admit** (bool): ```True``` lets the step proceed, ```False``` stops it
    - **reason** (str | None): Why the step was stopped, when it was

    """

    admit: bool
    reason: str | None = None

    @classmethod
    def allow(cls):
        """Return an admitting result (the step may proceed)."""
        return cls(admit=True)

    @classmethod
    def deny(cls, reason):
        """
        Return a stopping result with a reason.

        ### Args

        - **reason** (str): Why the step is stopped

        """
        return cls(admit=False, reason=reason)


class TokeoAiGateRule(MetaMixin):
    """
    Base class for gate rules: the decision, independent of placement.

    A rule answers one question -- given a context, may this step proceed? --
    and returns a ```GateResult```. The same rule works in either placement
    because it never inspects the loop; it is handed a context and judges it.
    The deny rule ignores the context and always refuses; a later confirm rule
    asks the user and may remember the answer; a heuristic rule recognises a
    pattern the user has already approved. The classification axis (none, tool
    level, exact pattern, heuristic, pattern) is a map of future rules, each its
    own class -- only the deny rule ships now.

    Do not instantiate this base directly; it has no decision of its own.
    Instantiated with the application and the activator's ```options``` as Meta
    overrides by the ```app.ai``` handler, like a guard or a sandbox; it holds
    no mutable per-call state.

    """

    class Meta:
        """Rule meta-data; a rule defines its own option keys (none here)."""

        pass

    def __init__(self, app, *args, **kw):
        """
        Initialize the rule.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: The activator's ```options``` as keyword arguments; keys
            matching ```Meta``` override its defaults

        """
        super(TokeoAiGateRule, self).__init__(*args, **kw)
        self.app = app

    def _setup(self, app):
        """
        Set up the rule after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass

    def admit(self, ctx):
        """
        Decide whether the step described by ```ctx``` may proceed.

        The single method a rule implements. It reads the context (the messages
        for a prompt gate, the tool call for a tool gate) and returns a
        ```GateResult``` -- ```allow()``` to let the step run, ```deny(reason)```
        to stop it. A rule that asks the user blocks here until an answer (or a
        timeout) arrives; the deny rule returns at once without asking.

        ### Args

        - **ctx**: The context for this checkpoint; its fields depend on the
            placement that built it (see ```TokeoAiPromptGate``` /
            ```TokeoAiToolGate```)

        ### Returns

        - **GateResult**: Whether the step may proceed, and why not if stopped

        """
        raise NotImplementedError

    def validate_options(self, options):
        """
        Validate the activator's ```options``` for the linter.

        The linter does not know a rule's allowed keys; it asks the class. The
        base accepts anything (a permissive default); a rule that wants strict
        checking overrides this and returns error strings.

        ### Args

        - **options** (dict): The activator's ```options``` block as configured

        ### Returns

        - **list[str] | None**: Error messages, or ```None```/empty when valid

        """
        return None


class TokeoAiGate(MetaMixin):
    """
    Base class for gate placements: WHERE the checkpoint sits in the loop.

    A placement holds one rule and delegates the decision to it via
    ```admit(ctx)```; it owns only the loop position and the context shape, not
    the decision. ```Meta.phase``` tells the handler where to call it -- a
    derivation sets it to ```prompt``` (before a model call) or ```tool```
    (before a tool runs). The handler partitions the agent's gates by phase and
    calls each at its site, the same way it partitions guards into before/after.

    Do not instantiate this base directly; it sets no phase and binds no rule.
    A concrete activator subclasses one of the two placement derivations and
    binds a rule. Instantiated with the application and the item's ```options```
    by the ```app.ai``` handler; the options flow on to the bound rule. Holds no
    mutable per-call state.

    """

    class Meta:
        """Gate meta-data; a placement derivation sets its phase."""

        # 'prompt' runs before a model call, 'tool' before a tool runs; the
        # base sets none, so the placement derivations must
        phase = None

    # the rule class an activator binds; the base binds none, so a bare
    # placement cannot decide -- an activator sets this to a concrete rule
    rule_cls = None

    def __init__(self, app, *args, **kw):
        """
        Initialize the gate and its bound rule.

        The placement builds its rule with the same ```options``` it received,
        so a configured option (a timeout, a default) reaches the rule that uses
        it. The base binds no rule; an activator sets ```rule_cls```.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: The item's ```options``` as keyword arguments; keys matching
            ```Meta``` override its defaults, and the same kwargs build the rule

        """
        super(TokeoAiGate, self).__init__(*args, **kw)
        self.app = app
        # the placement owns the rule instance and passes the options through,
        # so the activator names only the two classes, not the wiring
        self.rule = self.rule_cls(app, **kw) if self.rule_cls is not None else None

    def _setup(self, app):
        """
        Set up the gate and its rule after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        if self.rule is not None:
            self.rule._setup(app)

    def admit(self, ctx):
        """
        Delegate the decision for ```ctx``` to the bound rule.

        ### Args

        - **ctx**: The context this placement feeds the rule

        ### Returns

        - **GateResult**: The rule's decision

        """
        return self.rule.admit(ctx)

    def validate_options(self, options):
        """
        Validate the item's ```options``` for the linter, via the rule.

        The decision (and so the options) belong to the rule, so the placement
        forwards the check to it. A placement with no bound rule accepts
        anything.

        ### Args

        - **options** (dict): The item's ```options``` block as configured

        ### Returns

        - **list[str] | None**: Error messages, or ```None```/empty when valid

        """
        if self.rule is None:
            return None
        return self.rule.validate_options(options)


class TokeoAiPromptGate(TokeoAiGate):
    """
    The pre-model placement: a gate that runs before a model call.

    It sits before each ```provider.chat``` (the first call and every follow-up
    in a tool loop), so its rule decides on the OUTBOUND data -- the messages
    about to be sent and an estimated token count. A deny here stops the loop
    before the request goes out, with no model to react (unlike a tool deny);
    the handler ends the turn and reports the reason.

    The context handed to the rule carries the messages and the token estimate.
    A bare placement is not usable on its own; an activator binds a rule.

    """

    class Meta:
        """Run before a model call, so the rule sees the outbound request."""

        # before a provider.chat: the rule judges the messages, not a tool call
        phase = 'prompt'


class TokeoAiToolGate(TokeoAiGate):
    """
    The pre-tool placement: a gate that runs before a tool executes.

    It sits inside the tool-call pipeline, AFTER the before guards and BEFORE
    the sandbox runs the call -- so the cheap automatic policy decides first and
    the user is not asked about a call the policy already denied. Its rule
    decides on the concrete tool call (the tool name and the parsed arguments).
    A deny here behaves like a guard deny: the tool does not run and the model
    sees a ```denied: ...``` result it can react to.

    The context handed to the rule carries the invocation. A bare placement is
    not usable on its own; an activator binds a rule.

    """

    class Meta:
        """Run before a tool runs, so the rule sees the concrete call."""

        # before the sandbox exec: the rule judges the tool call
        phase = 'tool'
