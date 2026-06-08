"""
Synthetic training data for the {{ app_name }} fundi micro model.

The calendar domain is closed, so the dataset is generated, not collected:
phrase templates (English and German) with slots for dates and numbers,
compositional programs up to three chained steps rendered into language, plus
distractor preambles and out-of-domain negatives that teach the honest
``<nomatch>``. Every example is a (request, plan-DSL) pair.

### What the mixture teaches

- ~15% negatives (chatter mapped to ``<nomatch>``): without them the model
    would invent a plan for every input -- this is the anti-hallucination
    share. Negatives carry preambles and lead-ins too, so surrounding
    chatter alone never signals "calendar".
- ~55% single-step requests: tool recognition and exact slot filling
    (dates copied byte by byte, numbers as written).
- ~30% nested requests rendered from two- and three-step programs: the
    compositional share, e.g. "the weekday of today plus 2 days" ->
    ``current();add_days(date=@1,days=2);weekday(date=@2)``.
- Time words (today/now/current, heute/jetzt/aktuell) teach the bridge:
    they put ``current()`` in front and reference its result via ``@1``.
- Day counts are half single-digit on purpose: short numbers must be
    copied exactly, not extended (2 is 2, never 26).

The whole dataset is a pure function of its seed: ``dataset(n, seed)``
always returns the same deduplicated pairs, so a training run is fully
reproducible. Run ``python -m {{ app_label }}.core.fundi.data`` to print samples.
"""

import random
from datetime import date, timedelta

from {{ app_label }}.core.fundi.dsl import render, NOMATCH


def _iso(rng):
    # a uniform date in a wide window keeps the digit patterns diverse
    return (date(2000, 1, 1) + timedelta(days=rng.randrange(0, 20000))).isoformat()


# how a date slot is spoken: literal iso, or a time word that the plan
# resolves through the current tool
_TIME_WORDS_EN = ['today', 'now', 'current']
_TIME_WORDS_DE = ['heute', 'jetzt', 'aktuell']

# distractor preambles teach the model to ignore chatter around the intent
_PREAMBLES = [
    '', '', '',
    'i will write a new calendar app and need some specific dates. ',
    'we are planning the release schedule. ',
    'quick question for my project notes. ',
    'ich plane gerade ein paar termine. ',
    'fuer meine neue kalender app brauche ich daten. ',
    'short one: ',
]

# polite framings directly in front of the intent
_LEADINS = [
    '', '', '',
    'let me know first of all ',
    'please tell me ',
    'can you tell me ',
    'i need ',
    'sag mir bitte ',
    'ich brauche ',
]

# single-step phrasings; {d} is a date mention, {n} a number
_SINGLE_EN = {
    'weekday': ['the weekday of {d}', 'which weekday is {d}', 'weekday {d}', 'what day of the week is {d}'],
    'week_number': ['the week number of {d}', 'which calendar week is {d}', 'week_number {d}', 'iso week of {d}'],
    'moon_phase': ['the moon phase of {d}', 'moon phase on {d}', 'moon_phase {d}', 'how is the moon on {d}'],
    'date_diff': ['count the days between {d} and {d2}', 'days from {d} until {d2}', 'date_diff between {d} and {d2}',
                  'how many days lie between {d} and {d2}'],
    'add_days': ['add {n} days to {d}', 'add_days {n} onto {d}', 'the date {n} days after {d}', '{d} plus {n} days'],
    'current': ['the current date', 'what is the date right now', 'current', 'tell me the time'],
}
_SINGLE_DE = {
    'weekday': ['der wochentag von {d}', 'welcher wochentag ist {d}', 'wochentag {d}'],
    'week_number': ['die kalenderwoche von {d}', 'welche kw ist {d}'],
    'moon_phase': ['die mondphase am {d}', 'mondphase {d}'],
    'date_diff': ['zaehle die tage zwischen {d} und {d2}', 'tage von {d} bis {d2}', 'wieviele tage liegen zwischen {d} und {d2}'],
    'add_days': ['addiere {n} tage auf {d}', 'das datum {n} tage nach {d}', '{d} plus {n} tage'],
    'current': ['das aktuelle datum', 'wie spaet ist es'],
}

# the composed shape of the user incident: consumer of (add days to a date)
_NESTED_EN = ['the {c} of add a number of days {d} plus {n}', 'the {c} of {d} plus {n} days',
              '{c} of the date {n} days after {d}', 'first add {n} days to {d} and then the {c}']
_NESTED_DE = ['der {c} von {d} plus {n} tagen', 'erst {n} tage auf {d} addieren und dann der {c}']
_CONSUMER_EN = {'weekday': 'weekday', 'week_number': 'week number', 'moon_phase': 'moon phase'}
_CONSUMER_DE = {'weekday': 'wochentag', 'week_number': 'kalenderwoche', 'moon_phase': 'mondphase'}

_NEGATIVE = [
    'sing me a song', 'what is the capital of france', 'please review my pull request',
    'schreibe mir ein gedicht', 'wie wird das wetter morgen', 'open the pod bay doors',
    'summarize this document for me', 'erzaehl mir einen witz', 'order a pizza for tonight',
    'translate this sentence to spanish',
]


def sample(rng):
    """
    Draw one (request, dsl) example from the mixture.

    The first roll picks the bucket (negative, single-step, or nested),
    the second the language (about two thirds English); the helpers then
    pick a template, fill its slots, and render the matching plan.

    """
    kind = rng.random()
    if kind < 0.15:
        return _preamble(rng) + rng.choice(_NEGATIVE), NOMATCH
    lang_en = rng.random() < 0.65
    single = _SINGLE_EN if lang_en else _SINGLE_DE
    if kind < 0.70:
        tool = rng.choice(list(single))
        phrase = rng.choice(single[tool])
        return _render_single(rng, tool, phrase, lang_en)
    consumer = rng.choice(list(_CONSUMER_EN))
    phrase = rng.choice(_NESTED_EN if lang_en else _NESTED_DE)
    names = _CONSUMER_EN if lang_en else _CONSUMER_DE
    return _render_nested(rng, consumer, phrase, names[consumer], lang_en)


def _time_word(rng, lang_en):
    return rng.choice(_TIME_WORDS_EN if lang_en else _TIME_WORDS_DE)


def _preamble(rng):
    # negatives carry preambles and lead-ins too, so chatter around the
    # request never becomes a positive signal by itself
    return rng.choice(_PREAMBLES) + rng.choice(_LEADINS)


def _render_single(rng, tool, phrase, lang_en):
    # a date mention is literal or a time word; the plan resolves a time
    # word through current as step one
    plan = []
    request = phrase
    if '{d}' in phrase:
        if rng.random() < 0.35:
            request = request.replace('{d}', _time_word(rng, lang_en))
            plan.append(('current', {}))
            first = '@1'
        else:
            value = _iso(rng)
            request = request.replace('{d}', value)
            first = value
    if tool == 'current':
        return _preamble(rng) + request, render([('current', {})])
    if tool == 'date_diff':
        second = _iso(rng)
        request = request.replace('{d2}', second)
        plan.append(('date_diff', {'start': first, 'end': second}))
    elif tool == 'add_days':
        # single digits get half the mass: short numbers must copy exactly
        if rng.random() < 0.1:
            days = str(-rng.randrange(1, 60))
        elif rng.random() < 0.5:
            days = str(rng.randrange(1, 10))
        else:
            days = str(rng.randrange(10, 365))
        request = request.replace('{n}', days)
        plan.append(('add_days', {'date': first, 'days': days}))
    else:
        plan.append((tool, {'date': first}))
    return _preamble(rng) + request, render(plan)


def _render_nested(rng, consumer, phrase, consumer_name, lang_en):
    # the three-step shape: resolve the date, shift it, consume the result
    plan = []
    days = str(rng.randrange(1, 10) if rng.random() < 0.5 else rng.randrange(10, 90))
    request = phrase.replace('{c}', consumer_name).replace('{n}', days)
    if rng.random() < 0.5:
        request = request.replace('{d}', _time_word(rng, lang_en))
        plan.append(('current', {}))
        base = '@1'
    else:
        value = _iso(rng)
        request = request.replace('{d}', value)
        base = value
    plan.append(('add_days', {'date': base, 'days': days}))
    plan.append((consumer, {'date': f'@{len(plan)}'}))
    return _preamble(rng) + request, render(plan)


def dataset(count, seed=7):
    """
    Generate a deduplicated list of (request, dsl) pairs.

    ### Args

    - **count** (int): How many unique examples to return
    - **seed** (int): The rng seed; same seed, same dataset

    ### Returns

    - **list**: (request, plan line) tuples, requests unique

    """
    rng = random.Random(seed)
    seen = set()
    examples = []
    while len(examples) < count:
        request, dsl = sample(rng)
        if request not in seen:
            seen.add(request)
            examples.append((request, dsl))
    return examples


if __name__ == '__main__':
    for request, dsl in dataset(12):
        print(f'{request!r:80s} -> {dsl}')
