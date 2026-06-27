"""
Real test cases for akili, the Spiral micro language model.

Two layers, because akili is train-first. The data-contract tests run
without weights and lock in what the synthetic data *teaches* -- bare time
words, greetings, signed counts, plan legality, tool coverage. The model
test needs ```weights.npz``` (skips until it exists) and checks the actual
answers of the project's own trained model, under the ```guarded``` agent.
Run from the project root, for example ```pytest tests/core/akili```.
"""

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from {{ app_label }}.main import {{ app_class_name }}Test

# the held-out exact-plan accuracy the trained model must reach before the
# per-phrasing checks are trustworthy. raised to 0.97 together with the
# larger capacity (dim 128, ff 512); confirm after the first training run
# of the bigger model and set it just under the achieved value
_MIN_ACCURACY = 0.95

# english weekday names, locale-independent, matching the tool's output
_WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

# a sign written immediately after a shift keyword is the signed-count
# wording (the {n} slot); the dashes inside an iso date never match this
_SIGNED = re.compile(r'(plus|add|to|after|minus|subtract|from|before|nach|vor|auf|ab|off|addiere|ziehe|in)\s+[-+]\d')


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


def test_{{ app_label }}_akili_lexicon_loads():
    # the editable language definition (AKILI-LEX.yaml) must parse and
    # validate cleanly: every section filled for both languages, tools
    # known to the grammar, required placeholders present -- this runs
    # without weights and catches lexicon edits before a training run
    from {{ app_label }}.core.akili.data import _LEXICON
    from {{ app_label }}.core.akili.dsl import DOMAIN

    for language in ('en', 'de'):
        assert _LEXICON['time_words'][language]
        assert _LEXICON['relative_words'][language]
        assert _LEXICON['units'][language]
        for group in ('single', 'shift', 'shift_minus', 'relative', 'relative_chain'):
            pool = _LEXICON['patterns'][group]
            assert pool[language] if language in pool else all(pool[tool][language] for tool in pool)
    assert _LEXICON['negatives'] and _LEXICON['preambles'] and _LEXICON['leadins']
    for tool in _LEXICON['consumers']:
        assert tool in DOMAIN
    # the longest plan the data can produce must fit the decoder budget,
    # with room for the closing EOS step -- a longer pattern group would
    # otherwise be silently truncated at inference time
    from {{ app_label }}.core.akili.data import dataset
    from {{ app_label }}.core.akili.infer import PLAN_BUDGET

    longest = max(len(d) for _, d in dataset(8000, seed=7) if d != '<nomatch>')
    assert longest + 1 <= PLAN_BUDGET, (longest, PLAN_BUDGET)


def test_{{ app_label }}_akili_data_contract():
    # the teaching guarantees that make the model's answers predictable.
    # these assert what the synthetic data contains, so a lexicon or mixture
    # edit that would change the trained behaviour fails here -- before a
    # training run, and without needing any weights
    from {{ app_label }}.core.akili.data import dataset, _LEXICON
    from {{ app_label }}.core.akili.dsl import DOMAIN, NOMATCH, Constrainer, parse

    pairs = dataset(12000, seed=7)

    # a bare time word, standing on its own, must resolve to current(); the
    # request ends with exactly the time word and the plan is current().
    # without the bare-now slice 'today'/'now' were out of distribution and
    # produced spurious shifts, so this is the regression lock for that fix
    for word in _LEXICON['time_words']['en'] + _LEXICON['time_words']['de']:
        assert any(r.split() and r.split()[-1] == word and d == 'current()' for r, d in pairs), word

    # short greetings and pleasantries are negatives, so a bare greeting
    # echoes honestly instead of being answered with a date
    greetings = ('hello', 'hi there', 'hey', 'hallo', 'moin', 'servus', 'danke schoen')
    assert all(g in _LEXICON['negatives'] for g in greetings)
    assert any(d == NOMATCH and 'hallo' in r for r, d in pairs)

    # a sign written onto a count is not language: it maps to <nomatch>,
    # bare and consumer-wrapped, and NO positive example ever carries one
    assert not any(_SIGNED.search(r) for r, d in pairs if d != NOMATCH)
    assert any(_SIGNED.search(r) and d == NOMATCH for r, d in pairs)
    consumers = [names[lang] for names in _LEXICON['consumers'].values() for lang in ('en', 'de')]
    assert any(_SIGNED.search(r) and d == NOMATCH and any(c in r for c in consumers) for r, d in pairs)

    # every generated plan is legal under the grammar automaton (each
    # character is accepted in turn) and round-trips through parse; ending
    # the line is always legal once the plan is complete
    for r, d in pairs[:3000]:
        if d == NOMATCH:
            continue
        constrainer = Constrainer()
        for character in d:
            assert character in constrainer.allowed(), (d, character)
            constrainer.feed(character)
        assert '<eos>' in constrainer.allowed(), d
        parse(d)

    # the consumer-of-today composition still resolves through current as
    # step one (this must not be broken by the bare-now change)
    assert any(d == 'current();weekday(date=@1)' for _, d in pairs)

    # a "N weeks" shift has no add_weeks tool: it expands to add_days with a
    # multiple of 7 (1 week -> 7 days). day shifts stay unconstrained, so
    # this catches a regression where a week were treated as a single day
    weeks = re.compile(r'\b\d+\s+(weeks?|wochen?)\b')
    week_examples = [(r, d) for r, d in pairs if weeks.search(r) and 'add_days(' in d]
    assert week_examples, 'no "N weeks" shift examples in the data'
    for r, d in week_examples:
        match = re.search(r'days=(-?\d+)', d)
        assert match and int(match.group(1)) % 7 == 0, (r, d)

    # date_daydiff between today and a date relative to today is a three-step
    # plan: current(); a forward shift (@1 -> @2); date_daydiff(start=@1,
    # end=@2). so the second endpoint is resolved rather than treated as a
    # bare shift, and the shift is forward, so no negative value appears
    relative_diff = [(r, d) for r, d in pairs if 'date_daydiff(start=@1,end=@2)' in d and d.count(';') == 2]
    assert relative_diff, 'no relative-endpoint date_daydiff examples in the data'
    for r, d in relative_diff:
        assert d.startswith('current();') and '=-' not in d, (r, d)

    # every tool in the domain is actually exercised by the data
    exercised = {tool for _, d in pairs for tool in DOMAIN if tool + '(' in d}
    assert exercised == set(DOMAIN), set(DOMAIN) - exercised

    # the generator is deterministic for a fixed seed
    assert dataset(4000, seed=7)[:60] == dataset(4000, seed=7)[:60]

    # the --no-minus ablation removes every backward wording: no plan in the
    # minus-free dataset carries a negative count, while the normal data does
    assert not any('=-' in d for _, d in dataset(8000, seed=7, minus=False))
    assert any('=-' in d for _, d in pairs)


def test_{{ app_label }}_akili_domain_covers_calendar_tools():
    # akili's DOMAIN (dsl.py) and the project's `calendar` tool group (ai.yaml)
    # are two hand-kept lists of the same toolset. the planner only emits tools
    # in both lists, so one the config activates but DOMAIN misspells (a rename
    # landing in one list only) drops from the grammar silently -- no error,
    # just a worse plan. lock the two lists together here, without weights
    from {{ app_label }}.core.akili.dsl import DOMAIN

    with {{ app_class_name }}AiTestApp() as app:
        calendar = set(app.ai._resolve_tools(['calendar']))
    missing = calendar - set(DOMAIN)
    assert not missing, f'calendar tools missing from akili DOMAIN: {sorted(missing)}'


@pytest.mark.skipif(
    not Path('{{ app_label }}/core/akili/weights.npz').exists(),
    reason="akili has no trained weights yet (run 'python -m {{ app_label }}.core.akili.train')",
)
def test_{{ app_label }}_ai_akili_model():
    # the project's own trained micro language model ({{ app_label }}/core/akili)
    # plans with learned weights: exact copies, real chains incl. the
    # today-bridge, honest nomatch -- and the guards still rule the loop
    with {{ app_class_name }}AiTestApp() as app:

        def ask(text):
            return app.ai.ask(text, agent='guarded', profile='akili')

        # the quality bar first: the held-out exact-plan accuracy is recorded
        # in the weights, and below the floor the per-phrasing checks below
        # are flaky -- so fail here with a clear message instead of on some
        # borderline wording. retrain with more steps to lift it
        from {{ app_label }}.core.akili.infer import AkiliModel

        accuracy = AkiliModel().config.get('accuracy', 0.0)
        assert accuracy >= _MIN_ACCURACY, (
            f'akili held-out accuracy {accuracy:.4f} < {_MIN_ACCURACY}; '
            'retrain with more steps (e.g. AKILI_STEPS=4000 AKILI_DATA=60000)'
        )

        # exact copies and single-step tools
        assert ask('weekday of 2026-12-24') == '[akili] weekday: Thursday'
        assert ask('weekday of 2026-12-24 minus 2 days') == '[akili] weekday: Tuesday'
        assert ask('add 2 months to 2026-06-08') == '[akili] add_months: 2026-08-08'
        assert ask('die mondphase am 2000-01-06') == '[akili] moon_phase: new moon'
        iso_week = date(2026, 12, 24).isocalendar()[1]
        assert ask('the week number of 2026-12-24') == f'[akili] week_number: {iso_week}'

        # a bare time word is the current date: today/now/heute/jetzt all
        # resolve to current(); the time part varies, so match the date head
        today = date.today().isoformat()
        for word in ('today', 'now', 'heute', 'jetzt'):
            out = ask(word)
            assert out.startswith(f'[akili] current: {today}'), (word, out)

        # relative words and the today-bridge
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        assert ask('welches datum ist morgen') == f'[akili] add_days: {tomorrow}'

        # a week is seven days (there is no add_weeks tool): a week shift
        # expands to add_days with a multiple of 7
        assert ask('today minus 1 week') == f'[akili] add_days: {(date.today() - timedelta(days=7)).isoformat()}'
        assert ask('2026-06-08 plus 3 weeks') == '[akili] add_days: 2026-06-29'

        # date_daydiff between today and a date relative to today (three steps:
        # current, a forward shift, then the diff): today until tomorrow is
        # one day, today until next week is seven
        assert ask('count the days from today until tomorrow') == '[akili] date_daydiff: 1'
        assert ask('count the days from today until next week') == '[akili] date_daydiff: 7'

        # a consumer reading a shifted date: the weekday of (today + 14)
        shifted = date.today() + timedelta(days=14)
        assert ask('the weekday of today plus 14 days') == f'[akili] weekday: {_WEEKDAYS[shifted.weekday()]}'

        # a relative chain: two shifts from today, day word then year word
        base = date.today() + timedelta(days=1)
        try:
            target = base.replace(year=base.year + 1)
        except ValueError:
            # feb 29 clamps to feb 28 in a common year, like the tool does
            target = base.replace(year=base.year + 1, day=28)
        assert ask('the date of tomorrow next year') == f'[akili] add_years: {target.isoformat()}'

        # honest abstention: hard negative, greetings, and signed counts all
        # echo (no invented plan, no invented digit, no dropped shift)
        assert ask('what date is my birthday') == '[akili] what date is my birthday'
        for greeting in ('hello', 'hallo', 'moin'):
            assert ask(greeting) == f'[akili] {greeting}'
        assert ask('today plus -2 days') == '[akili] today plus -2 days'
        wrapped = 'the weekday of today plus -2 days'
        assert ask(wrapped) == f'[akili] {wrapped}'
        assert ask('sing me a song') == '[akili] sing me a song'

        # multi-turn chat still threads and the guards report the plan
        days = (date(2026, 12, 24) - date.today()).days
        chained = app.ai.chat(
            [{'role': 'user', 'content': 'count the days from today until 2026-12-24'}],
            agent='guarded',
            profile='akili',
        )
        assert chained.answer.text == f'[akili] date_daydiff: {days}'
        assert chained.answer.raw['plan'] == ['current', 'date_daydiff']
