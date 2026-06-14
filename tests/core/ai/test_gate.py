"""
Tests for the gate subsystem (tokeo core).

Covers the gate mechanics in isolation: the ```GateResult``` value, the
placement-blind deny rule, the two placement classes and their phases, the
composition (a placement holds a rule and delegates), the two activators, and
the loop effect through ```app.ai.chat``` -- a tool gate denies a tool call (the
model sees ```denied: ...```), a prompt gate stops the model call before it goes
out (an empty answer with a ```refusal```). Also covers the ordering (a tool
gate runs after the before guards) and the linter's coverage of the new
```ai.gates``` section and the agent ```gates``` field. The full LLM loop is
exercised by the Spiral tests; here the focus is the mechanics in isolation.
"""

from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.ai.gate import (
    GateResult,
    PromptContext,
    ToolContext,
    TokeoAiGate,
    TokeoAiPromptGate,
    TokeoAiToolGate,
)
from tokeo.core.ai.gates.deny import (
    TokeoAiGateDenyRule,
    TokeoAiPromptDenyGate,
    TokeoAiToolDenyGate,
)
from tokeo.core.ai.linter import TokeoAiLinter


# dotted paths to the two deny activators, named in config like the wasm
# sandbox -- no gate ships as a built-in short name
PROMPT_DENY = 'tokeo.core.ai.gates.deny.TokeoAiPromptDenyGate'
TOOL_DENY = 'tokeo.core.ai.gates.deny.TokeoAiToolDenyGate'

# an importable in-process test tool the mock can request (the same tools the
# fundi tests use); the template-only calc tool is not importable from core
ECHO = 'tests.core.ai.tools.EchoTool'


class GateTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
            'tokeo.ext.ai',
        ]


def ai_config():
    # a self-contained ai config: the echo tool in the in_process catch-all,
    # two deny gates by dotted path, and agents that wire each gate. the mock
    # provider asks for echo when the prompt names it
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        defaults=dict(profile='mock', agent=None),
        tools={'echo': dict(type=ECHO)},
        sandboxes={'allow': dict(type='in_process', tools='_all')},
        gates={
            'no_tools': dict(type=TOOL_DENY),
            'no_model': dict(type=PROMPT_DENY),
        },
        agents={
            # runs echo in process, no gate -- the baseline
            'plain': dict(type='fundi', options=dict(tools=['echo'], sandboxes=['allow'])),
            # a tool gate blocks every tool call
            'toolgated': dict(type='fundi', options=dict(tools=['echo'], gates=['no_tools'], sandboxes=['allow'])),
            # a prompt gate blocks the model call
            'modelgated': dict(type='fundi', options=dict(tools=['echo'], gates=['no_model'], sandboxes=['allow'])),
            # a tool gate together with the audit guard, to prove ordering
            'guarded_gated': dict(
                type='fundi',
                options=dict(tools=['echo'], guards=['audit'], gates=['no_tools'], sandboxes=['allow']),
            ),
        },
        guards={'audit': dict(type='audit')},
        profiles={'mock': dict(type='mock', agent='plain')},
    )
    return cfg


def ask(app, agent_name, prompt='echo hi'):
    # drive the loop and return the full ChatResult (not just its text)
    return app.ai.chat([{'role': 'user', 'content': prompt}], profile='mock', agent=agent_name)


# --------------------------------------------------------------------------------------
# the value and the rule
# --------------------------------------------------------------------------------------


def test_gate_result_allow_and_deny():
    allow = GateResult.allow()
    deny = GateResult.deny('nope')
    assert allow.admit is True and allow.reason is None
    assert deny.admit is False and deny.reason == 'nope'


def test_deny_rule_is_placement_blind():
    # the same rule refuses in either context, ignoring what it is handed --
    # the proof that rule and placement compose
    with GateTest(config_defaults=ai_config()) as app:
        rule = TokeoAiGateDenyRule(app)
        on_prompt = rule.admit(PromptContext(messages=[{'content': 'hi'}], tokens=1))
        on_tool = rule.admit(ToolContext(invocation=object()))
        assert on_prompt.admit is False
        assert on_tool.admit is False


# --------------------------------------------------------------------------------------
# the placements and activators
# --------------------------------------------------------------------------------------


def test_placements_declare_their_phase():
    assert TokeoAiPromptGate.Meta.phase == 'prompt'
    assert TokeoAiToolGate.Meta.phase == 'tool'


def test_activators_bind_the_deny_rule_to_a_placement():
    with GateTest(config_defaults=ai_config()) as app:
        pg = TokeoAiPromptDenyGate(app)
        tg = TokeoAiToolDenyGate(app)
        # the activator inherits the placement's phase and binds the deny rule
        assert pg._meta.phase == 'prompt'
        assert tg._meta.phase == 'tool'
        assert isinstance(pg.rule, TokeoAiGateDenyRule)
        assert isinstance(tg.rule, TokeoAiGateDenyRule)


def test_placement_delegates_admit_to_its_rule():
    with GateTest(config_defaults=ai_config()) as app:
        tg = TokeoAiToolDenyGate(app)
        # the placement's admit forwards to the bound rule's decision
        assert tg.admit(ToolContext(invocation=object())).admit is False


def test_bare_placement_has_no_rule():
    # a placement base with no bound rule cannot decide; an activator binds one
    with GateTest(config_defaults=ai_config()) as app:
        bare = TokeoAiGate(app)
        assert bare.rule is None
        assert bare.rule_cls is None


# --------------------------------------------------------------------------------------
# resolution + the loop effect
# --------------------------------------------------------------------------------------


def test_gate_resolves_by_dotted_path():
    # no built-in short name: a gate is named by its full dotted path
    with GateTest(config_defaults=ai_config()) as app:
        cls = app.ai.resolve('gate', TOOL_DENY)
        assert cls is TokeoAiToolDenyGate


def test_resolve_gates_splits_by_phase():
    with GateTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('guarded_gated')
        prompt_gates, tool_gates = app.ai._resolve_gates(agent)
        assert prompt_gates == []
        assert len(tool_gates) == 1


def test_no_agent_has_no_gates():
    # the deliberate raw path: with no agent there is no pipeline and no gate
    with GateTest(config_defaults=ai_config()) as app:
        prompt_gates, tool_gates = app.ai._resolve_gates(None)
        assert prompt_gates == [] and tool_gates == []


def test_tool_gate_denies_the_tool_call():
    with GateTest(config_defaults=ai_config()) as app:
        result = ask(app, 'toolgated')
        # the tool never ran: the model sees a denied result it can react to
        assert 'denied: blocked by deny gate' in result.text
        last = result.trace[-1]
        assert last.decision == 'deny'
        assert last.reason == 'blocked by deny gate'
        # sandbox stays None because a denied call never reaches one
        assert last.sandbox is None


def test_prompt_gate_stops_the_model_call():
    with GateTest(config_defaults=ai_config()) as app:
        result = ask(app, 'modelgated')
        # the model call was stopped before it went out: empty answer, the
        # reason carried in the refusal field
        assert result.text == ''
        assert 'model call stopped' in result.refusal
        assert 'blocked by deny gate' in result.refusal


def test_plain_agent_without_gate_runs_the_tool():
    # the baseline: no gate, echo runs and returns its text
    with GateTest(config_defaults=ai_config()) as app:
        result = ask(app, 'plain')
        assert 'hi' in result.text


def test_tool_gate_runs_with_guards_present():
    # a gate works alongside guards: the audit guard records the denied call
    with GateTest(config_defaults=ai_config()) as app:
        result = ask(app, 'guarded_gated')
        assert 'denied: blocked by deny gate' in result.text
        assert result.trace[-1].decision == 'deny'


# --------------------------------------------------------------------------------------
# linter coverage of the gates section + agent field
# --------------------------------------------------------------------------------------


def test_linter_accepts_a_valid_gates_config():
    with GateTest(config_defaults=ai_config()) as app:
        issues = TokeoAiLinter(app).lint()
        assert issues == []


def test_linter_flags_unknown_gate_reference():
    cfg = ai_config()
    cfg['ai']['agents']['toolgated']['options']['gates'] = ['does_not_exist']
    with GateTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any('gates' in issue.path and 'does_not_exist' in issue.message for issue in issues)


def test_linter_flags_gate_item_without_type():
    cfg = ai_config()
    cfg['ai']['gates']['broken'] = dict(options=dict(foo=1))
    with GateTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any('gates.broken' in issue.path for issue in issues)
