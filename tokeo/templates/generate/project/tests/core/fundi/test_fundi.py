"""
Real test cases for fundi, the Spiral micro language model.

Verifies the project's own trained model (the lab lives in
``{{ app_label }}/core/fundi``) against the real shipped configuration, under
the ``guarded`` agent. Train-first: until ``weights.npz`` exists the test
skips. Run from the project root, for example with
``pytest tests/core/test_fundi.py``.
"""

from pathlib import Path

import pytest
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


def test_{{ app_label }}_fundi_lexicon_loads():
    # the editable language definition (FUNDI-LEX.yaml) must parse and
    # validate cleanly: every section filled for both languages, tools
    # known to the grammar, required placeholders present -- this runs
    # without weights and catches lexicon edits before a training run
    from {{ app_label }}.core.fundi.data import _LEXICON
    from {{ app_label }}.core.fundi.dsl import DOMAIN
    for language in ('en', 'de'):
        assert _LEXICON['time_words'][language]
        assert _LEXICON['relative_words'][language]
        assert _LEXICON['units'][language]
        for group in ('single', 'shift', 'shift_minus', 'relative', 'relative_chain'):
            pool = _LEXICON['patterns'][group]
            assert (pool[language] if language in pool else all(pool[tool][language] for tool in pool))
    assert _LEXICON['negatives'] and _LEXICON['preambles'] and _LEXICON['leadins']
    for tool in _LEXICON['consumers']:
        assert tool in DOMAIN
    # the longest plan the data can produce must fit the decoder budget,
    # with room for the closing EOS step -- a longer pattern group would
    # otherwise be silently truncated at inference time
    from {{ app_label }}.core.fundi.data import dataset
    from {{ app_label }}.core.fundi.infer import PLAN_BUDGET
    longest = max(len(d) for _, d in dataset(8000, seed=7) if d != '<nomatch>')
    assert longest + 1 <= PLAN_BUDGET, (longest, PLAN_BUDGET)


@pytest.mark.skipif(
    not Path('{{ app_label }}/core/fundi/weights.npz').exists(),
    reason="fundi has no trained weights yet (run 'python -m {{ app_label }}.core.fundi.train')",
)
def test_{{ app_label }}_ai_fundi_model():
    # the project's own trained micro language model ({{ app_label }}/core/fundi)
    # plans with learned weights: exact copies, real chains incl. the
    # today-bridge, honest nomatch -- and the guards still rule the loop
    from datetime import date, timedelta
    with {{ app_class_name }}AiTestApp() as app:
        assert app.ai.ask('weekday of 2026-12-24', agent='guarded', profile='fundi') == '[fundi] weekday: Thursday'
        assert app.ai.ask('weekday of 2026-12-24 minus 2 days', agent='guarded', profile='fundi') == '[fundi] weekday: Tuesday'
        assert app.ai.ask('add 2 months to 2026-06-08', agent='guarded', profile='fundi') == '[fundi] add_months: 2026-08-08'
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        assert app.ai.ask('welches datum ist morgen', agent='guarded', profile='fundi') == f'[fundi] add_days: {tomorrow}'
        assert app.ai.ask('die mondphase am 2000-01-06', agent='guarded', profile='fundi') == '[fundi] moon_phase: new moon'
        # a relative chain: two shifts from today, day word then year word
        base = date.today() + timedelta(days=1)
        try:
            target = base.replace(year=base.year + 1)
        except ValueError:
            # feb 29 clamps to feb 28 in a common year, like the tool does
            target = base.replace(year=base.year + 1, day=28)
        chain2 = app.ai.ask('the date of tomorrow next year', agent='guarded', profile='fundi')
        assert chain2 == f'[fundi] add_years: {target.isoformat()}'
        # a hard negative: calendar-near wording the model cannot serve
        assert app.ai.ask('what date is my birthday', agent='guarded', profile='fundi') == '[fundi] what date is my birthday'
        # a sign on a bare count is not language: honest echo, not a digit,
        # whether bare or consumer-wrapped
        assert app.ai.ask('today plus -2 days', agent='guarded', profile='fundi') == '[fundi] today plus -2 days'
        wrapped = 'the weekday of today plus -2 days'
        assert app.ai.ask(wrapped, agent='guarded', profile='fundi') == f'[fundi] {wrapped}'
        assert app.ai.ask('sing me a song', agent='guarded', profile='fundi') == '[fundi] sing me a song'
        days = (date(2026, 12, 24) - date.today()).days
        chained = app.ai.chat([{'role': 'user', 'content': 'count the days from today until 2026-12-24'}], agent='guarded', profile='fundi')
        assert chained.text == f'[fundi] date_diff: {days}'
        assert chained.raw['plan'] == ['current', 'date_diff']
