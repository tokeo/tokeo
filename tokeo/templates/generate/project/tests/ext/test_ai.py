"""
Real test cases for the {{ app_name }} ai setup.

Verifies the project's own tools, the ```guarded``` agent, and the guard
pipeline against the real shipped configuration (the testing environment
merges ```testing.d/ai.yaml``` over ```base.d/ai.yaml```, pointing the file tools
below ```tmp/tests```). Run from the project root, for example with
```pytest tests/ext/ai/test_{{ app_label }}_ai.py```.
"""

import logging
from pathlib import Path

import pytest
from tokeo.core.ai import Invocation, ChatResult, ChatMessage, TraceStep, TokeoAiError
from tokeo.core.ai.tool import create_tool_result
from tokeo.core.ai.linter import TokeoAiLinter
from tokeo.core.ai.guards.validate import TokeoAiToolSchemaValidator
from tokeo.core.ai.guards.redact import TokeoAiRegexRedactGuard, TokeoAiRedactGuardError
from {{ app_label }}.core.ai.guards.truncate import {{ app_class_name }}AiTruncateGuard
from {{ app_label }}.core.ai.tools.calc import TokeoAiCalcTool
from {{ app_label }}.main import {{ app_class_name }}Test


class {{ app_class_name }}AiTestApp({{ app_class_name }}Test):

    class Meta:
        # set framework extensions
        extensions = [
            'colorlog',
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
            'tokeo.ext.ai',
        ]


# the file tools' scratch area in the testing environment (testing.d/ai.yaml)
TESTS_DIR = Path('tmp/tests')


@pytest.fixture
def scratch():
    # seed the scratch area and clean up the files a test may create
    TESTS_DIR.mkdir(parents=True, exist_ok=True)
    sample = TESTS_DIR / 'sample.txt'
    sample.write_text('buy milk\n')
    yield TESTS_DIR
    for name in ('sample.txt', 'notes.txt'):
        (TESTS_DIR / name).unlink(missing_ok=True)


def test_{{ app_label }}_ai_config_lints_clean():
    # the shipped ai configuration is sound (types resolve, references exist)
    with {{ app_class_name }}AiTestApp() as app:
        assert TokeoAiLinter(app).lint() == []


def test_{{ app_label }}_ai_tools_exec(scratch):
    # the project tools work when called directly, and the file tools stay
    # strictly below their configured base directory
    with {{ app_class_name }}AiTestApp() as app:
        # calc and read_file hand back a raw value; the date tools and the file
        # writer hand back a ToolResult whose as_str the model would see, so the
        # test reads each according to what the tool returns
        assert app.ai._tool('calc').exec(input='2 + 3') == 5
        assert len(app.ai._tool('current').exec().value.as_str) == 24  # YYYY-mm-dd HH:MM:SS.MMMZ
        assert app.ai._tool('read_file').exec(path='sample.txt') == 'buy milk\n'
        appended = app.ai._tool('append_file').exec(text='hello')
        assert appended.value.as_str == 'true' and appended.value.as_data['file'] == 'notes.txt'
        assert app.ai._tool('add_months').exec(date='2026-01-31', months=1).value.as_str == '2026-02-28'
        assert app.ai._tool('add_months').exec(date='2026-06-08', months=-4).value.as_str == '2026-02-08'
        assert app.ai._tool('add_years').exec(date='2024-02-29', years=1).value.as_str == '2025-02-28'
        assert (scratch / 'notes.txt').read_text() == 'hello\n'
        with pytest.raises(TokeoAiError, match='escapes the tool base directory'):
            app.ai._tool('read_file').exec(path='../../setup.py')
        with pytest.raises(TokeoAiError, match='no such file'):
            app.ai._tool('read_file').exec(path='missing.txt')


def test_{{ app_label }}_ai_agent_guarded_runs_tools(scratch):
    # the guarded agent answers through the loop: calculate, tell the time,
    # and read a file -- driven by the built-in mock model
    with {{ app_class_name }}AiTestApp() as app:
        assert app.ai.ask('calc 2 + 3', agent='guarded', profile='mock') == 'Done. The tool returned: 5'
        assert app.ai.ask('current', agent='guarded', profile='mock').startswith('Done. The tool returned: 2')
        assert app.ai.ask('read_file sample.txt', agent='guarded', profile='mock') == 'Done. The tool returned: buy milk\n'


def test_{{ app_label }}_ai_trace_records_the_whole_run(scratch):
    # the trace is the run's full, ordered history as a list of steps: each
    # step carries an origin and the object it left in hand. one tool call
    # appears in several steps (one per guard that ran), but they are the same
    # object -- the guards refined it in place, so each is an unchanged step
    with {{ app_class_name }}AiTestApp() as app:
        result = app.ai.chat([{'role': 'user', 'content': 'calc 2 + 3'}], agent='guarded', profile='mock')
        assert result.answer.text == 'Done. The tool returned: 5'
        assert all(isinstance(step, TraceStep) for step in result.trace)
        # the conversation turns and the model rounds are on the trace
        messages = [step.object for step in result.trace if isinstance(step.object, ChatMessage)]
        assert any(message.get('role') == 'user' for message in messages)
        assert any(message.get('role') == 'tool' for message in messages)
        # the one tool call: many invocation steps, but a single object
        invocation_steps = [step for step in result.trace if isinstance(step.object, Invocation)]
        distinct = {id(step.object) for step in invocation_steps}
        assert len(distinct) == 1
        invocation = invocation_steps[0].object
        assert invocation.name == 'calc'
        assert invocation.decision == Invocation.ALLOW
        # each guard that ran is an attributable step (the loop tracked the call,
        # then policy/validate/redact/truncate/audit each left their step)
        guard_steps = [step for step in invocation_steps if step.origin is not None and step.changed is False]
        assert len(guard_steps) >= 1
        # the trace is chronological: the user turn's step precedes the call's
        objects = [step.object for step in result.trace]
        assert objects.index(messages[0]) < objects.index(invocation)


def test_{{ app_label }}_ai_agent_guarded_denies_writing(scratch):
    # the readonly policy denies the writing tool by name: the call never
    # runs, the loop continues, and the audit guard records the denial
    logs = []
    handler = logging.Handler()
    handler.emit = lambda record: logs.append(record.getMessage())
    with {{ app_class_name }}AiTestApp() as app:
        app.log.backend.addHandler(handler)
        app.log.backend.setLevel(logging.INFO)
        result = app.ai.chat([{'role': 'user', 'content': 'append_file hello'}], agent='guarded', profile='mock')
        assert "denied: tool 'append_file' is not permitted by policy" in result.answer.text
        # the trace records steps; pick the tool call out by the step's object
        invocation_steps = [step for step in result.trace if isinstance(step.object, Invocation)]
        distinct = {id(step.object) for step in invocation_steps}
        assert len(distinct) == 1
        invocation = invocation_steps[0].object
        assert invocation.decision == 'deny'
        # cross-check via the constant: literal and constant must agree
        assert invocation.decision == Invocation.DENY
        assert invocation.result is None
        assert not (scratch / 'notes.txt').exists()
        assert any('ai trace' in message and 'denied' in message for message in logs)


def test_{{ app_label }}_ai_tool_schema_validator_denies_when_strict():
    # strict mode denies a malformed call against the tool's real schema before
    # anything runs (here: the required "input" is missing)
    with {{ app_class_name }}AiTestApp() as app:
        guard = TokeoAiToolSchemaValidator(app)
        # strict comes from the declaration's options, as the handler sets it
        guard._declaration = {'options': {'strict': True}}
        invocation = Invocation(id='t1', name='calc', arguments={}, parameters=TokeoAiCalcTool.Meta.parameters)
        guard.on_call(None, invocation)
        assert invocation.decision == 'deny'
        assert invocation.decision == Invocation.DENY
        assert "missing required argument 'input'" in invocation.reason


def test_{{ app_label }}_ai_tool_schema_validator_flags_when_not_strict():
    # the default (strict false) only flags a malformed call: the reason is set
    # (so it shows on the trace) and a warning is logged, but the call still runs
    logs = []
    handler = logging.Handler()
    handler.emit = lambda record: logs.append(record.getMessage())
    with {{ app_class_name }}AiTestApp() as app:
        app.log.backend.addHandler(handler)
        app.log.backend.setLevel(logging.WARNING)
        guard = TokeoAiToolSchemaValidator(app)
        invocation = Invocation(id='t1', name='calc', arguments={}, parameters=TokeoAiCalcTool.Meta.parameters)
        guard.on_call(None, invocation)
        # not denied: the call is allowed to run
        assert invocation.decision == Invocation.ALLOW
        # but flagged: the reason carries the problem, on the trace and in the log
        assert "missing required argument 'input'" in invocation.reason
        assert any("missing required argument 'input'" in line for line in logs)


def test_{{ app_label }}_ai_redact_guard_masks_secrets():
    # the regex redact guard masks secret-looking spans at both tool stages: a
    # result text at on_return, and the call's string arguments at on_call, so a
    # leaked token never reaches history/trace/log
    with {{ app_class_name }}AiTestApp() as app:
        guard = TokeoAiRegexRedactGuard(app)
        # patterns are required (no built-in list); supply them as the handler would
        guard._declaration = {
            'options': {
                'patterns': [
                    r'(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}',
                    r'(?i)\b(?:api[_-]?key|secret|password|token)\b\s*[:=]\s*\S+',
                ],
            },
        }
        guard._setup(app)
        # on_return: a secret in the returned text is masked
        invocation = Invocation(id='t1', name='read_file', arguments={})
        invocation.result = create_tool_result('the page said Bearer abc123DEF456ghi789 here')
        guard.on_return(None, invocation)
        assert 'abc123DEF456ghi789' not in invocation.result.value.as_str
        assert '[redacted]' in invocation.result.value.as_str
        assert 'redacted' in (invocation.reason or '')
        assert invocation.decision == 'allow'
        # on_call: a secret in a string argument is masked in place
        call = Invocation(id='t2', name='read_file', arguments={'q': 'token=swordfish123', 'n': 5})
        guard.on_call(None, call)
        assert 'swordfish123' not in call.arguments['q']
        assert '[redacted]' in call.arguments['q']
        # a non-string argument is left untouched
        assert call.arguments['n'] == 5
        assert call.decision == 'allow'
        assert invocation.decision == Invocation.ALLOW


def test_{{ app_label }}_ai_redact_guard_raises_on_missing_pattern():
    # with no patterns configured the guard must abort the run on the first call
    # that reaches a masking stage, rather than silently mask nothing and let a
    # secret through
    with {{ app_class_name }}AiTestApp() as app:
        guard = TokeoAiRegexRedactGuard(app)
        guard._setup(app)
        invocation = Invocation(id='t1', name='read_file', arguments={})
        invocation.result = create_tool_result('Bearer abc123DEF456ghi789')
        with pytest.raises(TokeoAiRedactGuardError, match='missing or invalid pattern'):
            guard.on_return(None, invocation)


def test_{{ app_label }}_ai_truncate_guard_caps_long_results():
    # the project truncate guard keeps the head of an over-long text and marks
    # the cut, so a big blob cannot blow the budget. it acts at two stages:
    # on_return (a tool result) and on_close (the final answer)
    with {{ app_class_name }}AiTestApp() as app:
        guard = {{ app_class_name }}AiTruncateGuard(app)
        guard._declaration = dict(options=dict(limit=10))
        # on_return caps the tool result text and notes the cut on reason
        invocation = Invocation(id='t1', name='read_file', arguments={})
        invocation.result = create_tool_result('x' * 50)
        guard.on_return(None, invocation)
        assert invocation.result.value.as_str == 'x' * 10 + '... [truncated 40 chars]'
        assert 'truncated 40 chars' in (invocation.reason or '')
        # on_close caps the run's final answer text
        result = ChatResult(text='y' * 50)
        guard.on_close(None, result)
        assert result.text == 'y' * 10 + '... [truncated 40 chars]'
