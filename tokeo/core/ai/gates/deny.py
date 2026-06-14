"""
The deny gate: a rule that always refuses, plus its two activators.

This is the base of the gate subsystem -- the simplest possible rule. It admits
nothing and asks nothing, so it needs no input channel, no timeout and no
learning; it lays the whole wiring bare (placement, agent selection, trace and
the stopped-step path) without the hard interactivity problem the confirm rule
will bring. The two activators bind the deny rule to each placement:

- ```TokeoAiPromptDenyGate``` blocks every model call (an agent that can never
    send a prompt -- mostly a hard off switch and a wiring check)
- ```TokeoAiToolDenyGate``` blocks every tool call (an agent that may talk but
    never act -- the useful baseline)

Neither activator is a built-in: like the wasm sandbox and the untrusted-exec
tool, a deny gate is named in config by its full dotted path, never a short
name. Refusing everything should be a deliberate choice, not something reached
by a convenient built-in.
"""

from tokeo.core.ai.gate import (
    GateResult,
    TokeoAiGateRule,
    TokeoAiPromptGate,
    TokeoAiToolGate,
)


class TokeoAiGateDenyRule(TokeoAiGateRule):
    """
    A gate rule that admits nothing, in either placement.

    It ignores the context entirely -- there is no decision to make, the answer
    is always no -- so the same rule serves the prompt and the tool placement
    unchanged. This is what proves the composition: a single placement-blind
    rule, reused by both activators.

    """

    def admit(self, ctx):
        """
        Refuse the step, whatever the context.

        ### Args

        - **ctx**: The context (unused; the answer does not depend on it)

        ### Returns

        - **GateResult**: Always a stopping result

        """
        # WHY context-blind: deny is the one rule that needs no input; reusing
        # it in both placements is the proof that rule and placement compose
        return GateResult.deny('blocked by deny gate')


class TokeoAiPromptDenyGate(TokeoAiPromptGate):
    """
    Activator: the deny rule at the pre-model placement (```prompt_deny```).

    Binds ```TokeoAiGateDenyRule``` to ```TokeoAiPromptGate```, so every model
    call is stopped before it goes out. Named in config by its dotted path:
    ```type: tokeo.core.ai.gates.deny.TokeoAiPromptDenyGate```.

    """

    rule_cls = TokeoAiGateDenyRule


class TokeoAiToolDenyGate(TokeoAiToolGate):
    """
    Activator: the deny rule at the pre-tool placement (```tool_deny```).

    Binds ```TokeoAiGateDenyRule``` to ```TokeoAiToolGate```, so every tool call
    is stopped before it runs and the model sees a ```denied: ...``` result.
    Named in config by its dotted path:
    ```type: tokeo.core.ai.gates.deny.TokeoAiToolDenyGate```.

    """

    rule_cls = TokeoAiGateDenyRule
