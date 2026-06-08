"""
Real test cases for fundi, the {{ app_name }} micro language model.

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


@pytest.mark.skipif(
    not Path('{{ app_label }}/core/fundi/weights.npz').exists(),
    reason="fundi has no trained weights yet (run 'python -m {{ app_label }}.core.fundi.train')",
)
def test_{{ app_label }}_ai_fundi_model():
    # the project's own trained micro language model ({{ app_label }}/core/fundi)
    # plans with learned weights: exact copies, real chains incl. the
    # today-bridge, honest nomatch -- and the guards still rule the loop
    from datetime import date
    with {{ app_class_name }}AiTestApp() as app:
        assert app.ai.ask('weekday of 2026-12-24', agent='guarded', profile='fundi') == '[fundi] weekday: Thursday'
        assert app.ai.ask('weekday of 2026-12-24 minus 2 days', agent='guarded', profile='fundi') == '[fundi] weekday: Tuesday'
        assert app.ai.ask('die mondphase am 2000-01-06', agent='guarded', profile='fundi') == '[fundi] moon_phase: new moon'
        assert app.ai.ask('sing me a song', agent='guarded', profile='fundi') == '[fundi] sing me a song'
        days = (date(2026, 12, 24) - date.today()).days
        chained = app.ai.chat([{'role': 'user', 'content': 'count the days from today until 2026-12-24'}], agent='guarded', profile='fundi')
        assert chained.text == f'[fundi] date_diff: {days}'
        assert chained.raw['plan'] == ['current', 'date_diff']
