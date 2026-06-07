"""
Real test cases for the {{ app_name }} ai setup.

Verifies the project's own tools, the guarded ``mock`` agent, and the guard
pipeline against the real shipped configuration (the testing environment
merges ``testing.d/ai.yaml`` over ``base.d/ai.yaml``, pointing the file tools
below ``tmp/tests``). Run from the project root, for example with
``pytest tests/ext/ai/test_{{ app_label }}_ai.py``.
"""

import logging
from pathlib import Path

import pytest
from tokeo.core.ai import Invocation, TokeoAiError
from tokeo.core.ai.linter import TokeoAiLinter
from tokeo.core.ai.validate import TokeoAiValidateGuard
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
        assert app.ai._tool('calc').exec(input='2 + 3') == '5'
        assert len(app.ai._tool('current').exec()) == 19   # YYYY-mm-dd HH:MM:SS
        assert app.ai._tool('read_file').exec(path='sample.txt') == 'buy milk\n'
        assert app.ai._tool('append_file').exec(text='hello') == "appended to 'notes.txt'"
        assert (scratch / 'notes.txt').read_text() == 'hello\n'
        with pytest.raises(TokeoAiError, match='escapes the tool base directory'):
            app.ai._tool('read_file').exec(path='../../setup.py')
        with pytest.raises(TokeoAiError, match='no such file'):
            app.ai._tool('read_file').exec(path='missing.txt')


def test_{{ app_label }}_ai_agent_mock_runs_tools(scratch):
    # the guarded agent answers through the loop: calculate, tell the time,
    # and read a file -- driven by the built-in mock model
    with {{ app_class_name }}AiTestApp() as app:
        assert app.ai.ask('calc 2 + 3', agent='mock') == 'Done. The tool returned: 5'
        assert app.ai.ask('current', agent='mock').startswith('Done. The tool returned: 2')
        assert app.ai.ask('read_file sample.txt', agent='mock') == 'Done. The tool returned: buy milk\n'


def test_{{ app_label }}_ai_agent_mock_denies_writing(scratch):
    # the readonly policy denies the writing tool by name: the call never
    # runs, the loop continues, and the audit guard records the denial
    logs = []
    handler = logging.Handler()
    handler.emit = lambda record: logs.append(record.getMessage())
    with {{ app_class_name }}AiTestApp() as app:
        app.log.backend.addHandler(handler)
        app.log.backend.setLevel(logging.INFO)
        result = app.ai.chat([{'role': 'user', 'content': 'append_file hello'}], agent='mock')
        assert "denied: tool 'append_file' is not permitted by policy" in result.text
        assert len(result.trace) == 1
        assert result.trace[0].decision == 'deny'
        assert result.trace[0].result is None
        assert not (scratch / 'notes.txt').exists()
        assert any('ai audit' in message and 'denied' in message for message in logs)


def test_{{ app_label }}_ai_validate_guard_checks_arguments():
    # the validate guard denies a malformed call against the tool's real
    # schema before anything runs (here: the required "input" is missing)
    with {{ app_class_name }}AiTestApp() as app:
        guard = TokeoAiValidateGuard(app)
        invocation = Invocation(id='t1', name='calc', arguments={}, parameters=TokeoAiCalcTool.Meta.parameters)
        guard.check(invocation)
        assert invocation.decision == 'deny'
        assert "missing required argument 'input'" in invocation.reason


def test_{{ app_label }}_ai_fundi_calendar():
    # the in-process fundi 0.1 micro model drives the calendar toolset: it
    # reads the injected specs, extracts iso dates by schema, resolves
    # "today" through the current tool (a real two-step chain), and answers
    # a denial with the reason instead of retrying
    from datetime import date
    with {{ app_class_name }}AiTestApp() as app:
        assert app.ai.ask('weekday of 2026-12-24', profile='fundi') == '[fundi0.1] weekday: Thursday'
        assert app.ai.ask('week_number of 2026-12-24', profile='fundi') == '[fundi0.1] week_number: 52'
        assert app.ai.ask('add_days 90 onto 2026-06-07', profile='fundi') == '[fundi0.1] add_days: 2026-09-05'
        assert app.ai.ask('moon_phase on 2000-01-06', profile='fundi') == '[fundi0.1] moon_phase: new moon'
        days = (date(2026, 12, 24) - date.today()).days
        chained = app.ai.chat([{'role': 'user', 'content': 'count the days from today until 2026-12-24'}], profile='fundi')
        assert chained.text == f'[fundi0.1] date_diff: {days}'
        assert chained.raw['plan'] == ['current', 'date_diff']
        # under the guarded mock agent the readonly policy still rules: the
        # model asks for the writing tool, the guard denies, fundi explains
        denied = app.ai.ask('append_file remember the milk', profile='fundi', agent='mock')
        assert denied == "[fundi0.1] not permitted: tool 'append_file' is not permitted by policy"


def test_{{ app_label }}_ai_fundi_model():
    # the project owns its micro model, so the project verifies its
    # documented behavior: spec matching, honest no-match, honest failure
    with {{ app_class_name }}AiTestApp() as app:
        # two description keywords match anywhere in the request
        assert app.ai.ask('count the days between 2026-06-07 and 2026-07-07', profile='fundi') == '[fundi0.1] date_diff: 30'
        # nothing matched, nothing invented: the labelled echo answers
        assert app.ai.ask('sing me a song', profile='fundi') == '[fundi0.1] sing me a song'
        # a failing tool ends with the reason instead of a retry
        assert app.ai.ask('add_days 90 onto 2026-13-99', profile='fundi').startswith('[fundi0.1] failed: ')
