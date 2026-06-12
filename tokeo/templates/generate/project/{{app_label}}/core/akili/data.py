"""
Synthetic training data for the Spiral akili micro model.

The calendar domain is closed, so the dataset is generated, not collected.
Every example is a (request, plan-DSL) pair. The complete language -- every
word and every sentence pattern the model is taught -- lives in
```AKILI-LEX.yaml``` next to this module; this module holds the mechanics:
loading and validating the lexicon, the mixture, and how patterns are
filled and rendered into plans. Teaching akili new language is editing the
lexicon and retraining.

### What the mixture teaches

- ~15% abstentions mapped to ```<nomatch>``` (about 12% chatter and hard
    negatives, about 3% signed-count requests): without them the model
    would invent a plan for every input -- this is the anti-hallucination
    share. Negatives carry preambles and lead-ins too, so surrounding
    chatter alone never signals "calendar"; calendar-near hard negatives
    ("the date of christmas") keep the honesty close to the domain, and
    a sign written onto a bare count is taught as ```<nomatch>``` too, in
    every phrasing -- bare ("plus -2 days") and consumer-wrapped ("the
    weekday of today plus -2 days") -- since a sign in the request is not
    part of the language, so the model echoes instead of inventing a digit
    or quietly dropping the shift.
- ~11% relative words (tomorrow, uebermorgen, last week, next year ...):
    the lexicon maps every word to its shift from today; umlaut and
    folded spellings are taught side by side.
- ~4% relative chains ("tomorrow next year"): two relative words, two
    shifts from today in order -- the three-step plan is full, so these
    carry no consumer.
- ~45% shifts, plain and composed: the unit word (days, months, years)
    picks the tool, signs included, day units get half the mass; a {c}
    in the pattern puts a consumer behind the shift -- the three-step
    share, e.g. "the weekday of today plus 2 days" ->
    ```current();add_days(date=@1,days=2);weekday(date=@2)```.
- ~28% single-step requests for the non-shifting tools: recognition and
    exact slot filling (dates copied byte by byte, numbers as written).
- Time words (today/now/current, heute/jetzt/aktuell) teach the bridge:
    they put ```current()``` in front and reference its result via ```@1```.
- Day counts are half single-digit on purpose: short numbers must be
    copied exactly, not extended (2 is 2, never 26).
- Offsets are signed: minus/before/ago wordings (minus/vor in German)
    map to negative values, plus/after/in to positive ones -- the request
    always shows the bare count, the sign lives in the plan. A literal
    sign inside a request ("plus -2 days") is deliberately not part of
    the language.
- ~6% of the requests carry one human typo, a doubled letter or swapped
    neighbours ("tommorrow"); digits and date characters are never
    touched, the plan side stays exact.

The whole dataset is a pure function of its seed and the lexicon: same
seed, same lexicon, same deduplicated pairs -- a training run is fully
reproducible. Run ```python -m {{ app_label }}.core.akili.data``` to print samples.

### The lexicon

```yaml
.. include:: ./AKILI-LEX.yaml
```
"""

import pathlib
import random
from datetime import date, timedelta

import yaml

from {{ app_label }}.core.akili.dsl import render, DOMAIN, NOMATCH

# which placeholders every pattern group must carry; the loader checks
# them so a lexicon edit fails loudly, never silently in training
_REQUIRED = {
    'shift': ('{n}', '{u}'),
    'shift_minus': ('{n}', '{u}'),
    'relative': ('{w}',),
    'relative_chain': ('{w}', '{w2}'),
}


def _load_lexicon():
    """
    Load and validate ```AKILI-LEX.yaml```.

    The lexicon is data, so it is checked like data: tools must exist in
    the plan grammar, shifts must be integers, and every pattern group
    must carry its required placeholders.

    ### Returns

    - **dict**: The parsed lexicon (words, names, and pattern groups)

    """
    path = pathlib.Path(__file__).parent / 'AKILI-LEX.yaml'
    lexicon = yaml.safe_load(path.read_text())
    for language, words in lexicon['relative_words'].items():
        for word, row in words.items():
            if row['tool'] not in DOMAIN:
                raise ValueError(f'AKILI-LEX.yaml: unknown tool {row["tool"]!r} for {word!r}')
            int(row['shift'])
    for language, rows in lexicon['units'].items():
        for row in rows:
            if row['tool'] not in DOMAIN:
                raise ValueError(f'AKILI-LEX.yaml: unknown tool {row["tool"]!r} in units/{language}')
            # declensions: 'one' and 'many' may be a word or a list of words
            for key in ('one', 'many'):
                if not isinstance(row[key], list):
                    row[key] = [row[key]]
    for tool in lexicon['consumers']:
        if tool not in DOMAIN:
            raise ValueError(f'AKILI-LEX.yaml: unknown consumer tool {tool!r}')
    for tool in lexicon['patterns']['single']:
        if tool not in DOMAIN:
            raise ValueError(f'AKILI-LEX.yaml: unknown tool {tool!r} in patterns/single')
    for group, needed in _REQUIRED.items():
        for language, phrases in lexicon['patterns'][group].items():
            for phrase in phrases:
                for placeholder in needed:
                    if placeholder not in phrase:
                        raise ValueError(f'AKILI-LEX.yaml: {group}/{language} pattern {phrase!r} misses {placeholder}')
    return lexicon


_LEXICON = _load_lexicon()


def _lang(lang_en):
    return 'en' if lang_en else 'de'


def _iso(rng):
    # a literal date for a {d} slot: uniform over ~55 years (20000 days from
    # 2000-01-01). the wide window keeps every digit position varied, so the
    # model learns to copy any date layout rather than memorizing a few
    return (date(2000, 1, 1) + timedelta(days=rng.randrange(0, 20000))).isoformat()


def _time_word(rng, lang_en):
    return rng.choice(_LEXICON['time_words'][_lang(lang_en)])


def _consumer(rng, lang_en):
    # the tool and its spoken name in the requested language
    tool = rng.choice(list(_LEXICON['consumers']))
    return tool, _LEXICON['consumers'][tool][_lang(lang_en)]


def _preamble(rng):
    # negatives carry preambles and lead-ins too, so chatter around the
    # request never becomes a positive signal by itself; about two thirds
    # of the examples carry each of them
    parts = []
    if rng.random() < 0.667:
        parts.append(rng.choice(_LEXICON['preambles']))
    if rng.random() < 0.667:
        parts.append(rng.choice(_LEXICON['leadins']))
    return ''.join(part + ' ' for part in parts)


def sample(rng, minus=True):
    """
    Draw one (request, dsl) example from the mixture.

    The first roll picks the bucket (negative, relative word, relative
    chain, shift, or single-step), the second the language (about two
    thirds
    English); the helpers then pick a pattern from the lexicon, fill its
    slots, and render the matching plan. With ```minus=False``` every
    backward wording is left out: the resulting dataset teaches a model
    without any notion of minus.

    """
    # one uniform roll in [0,1) partitions the mixture; the cut points are
    # the cumulative shares. tuning them shifts what the model practises
    # most, which directly shapes what it gets good at:
    #   0.00-0.12  (12%)  lexicon negatives -> learn to abstain on chatter
    #   0.12-0.15  ( 3%)  signed-count requests -> abstain, don't invent
    #   0.15-0.26  (11%)  single relative words (tomorrow, letzte woche)
    #   0.26-0.30  ( 4%)  relative chains (two shifts from today)
    #   0.30-0.72  (42%)  shifts, plain and consumer-composed (any unit)
    #   0.72-0.76  ( 4%)  a bare time word (today/now/heute) -> current()
    #   0.76-0.80  ( 4%)  date_diff between today and a relative date
    #   0.80-1.00  (20%)  single-step calls to the non-shifting tools
    kind = rng.random()
    if kind < 0.12:
        return _preamble(rng) + rng.choice(_LEXICON['negatives']), NOMATCH
    # the second roll picks the language: ~65% english, ~35% german. raising
    # this would teach english better at german's expense, and vice versa
    lang_en = rng.random() < 0.65
    if kind < 0.15:
        # a sign on a bare count: honest <nomatch>, not an invented digit
        return _render_signed_nomatch(rng, lang_en)
    if kind < 0.26:
        return _render_relative(rng, lang_en, minus)
    if kind < 0.30:
        return _render_relative_chain(rng, lang_en, minus)
    if kind < 0.72:
        return _render_shift(rng, lang_en, minus)
    if kind < 0.76:
        # a bare time word standing on its own means "the current date"
        return _render_now(rng, lang_en)
    if kind < 0.80:
        # date_diff between today and a date relative to today
        return _render_date_diff_relative(rng, lang_en)
    single = _LEXICON['patterns']['single']
    tool = rng.choice(list(single))
    phrase = rng.choice(single[tool][_lang(lang_en)])
    return _render_single(rng, tool, phrase, lang_en)


def _unit(rng, lang_en):
    # which unit a shift uses. the day unit gets half the probability mass
    # on purpose: day arithmetic is the bread-and-butter skill and the
    # hardest to copy exactly, so it is practised more than the others
    # combined. the day unit is the factor-1 add_days row (weeks are also an
    # add_days row, but with factor 7, so they must not steal the day boost)
    rows = _LEXICON['units'][_lang(lang_en)]
    day = [row for row in rows if row['tool'] == 'add_days' and row.get('factor', 1) == 1]
    if day and rng.random() < 0.5:
        return rng.choice(day)
    return rng.choice(rows)


def _count(rng, unit):
    # how large the {n} count is, per unit. the day distribution is the
    # important one: half the time a single digit (1-9), half the time a
    # larger number (10-364). the single-digit half is deliberate -- short
    # numbers MUST be copied exactly ("2" stays "2", never becomes "26"),
    # so they are over-represented to drill exact copying. weeks stay small
    # (1-8) so the resulting day shift (count x 7) is realistic and well
    # within the grammar's three-digit cap; months stay within a year
    # (1-11) and years stay small (1-5), the ranges that actually occur
    if unit.get('factor', 1) > 1:
        return rng.randrange(1, 9)
    tool = unit['tool']
    if tool == 'add_days':
        return rng.randrange(1, 10) if rng.random() < 0.5 else rng.randrange(10, 365)
    return rng.randrange(1, 12 if tool == 'add_months' else 6)


def _consume(rng, lang_en, request, plan):
    # a {c} in the pattern means a consumer reads the latest result; the
    # rule holds across every pattern group
    if '{c}' in request:
        consumer, name = _consumer(rng, lang_en)
        request = request.replace('{c}', name)
        plan.append((consumer, {'date': f'@{len(plan)}'}))
    return request, plan


def _render_relative(rng, lang_en, minus=True):
    # relative words always shift from today: current resolves the day,
    # the lexicon row says which shift, an optional consumer reads it
    words = _LEXICON['relative_words'][_lang(lang_en)]
    rows = [(word, row['tool'], str(row['shift'])) for word, row in words.items()]
    if not minus:
        rows = [row for row in rows if not row[2].startswith('-')]
    word, tool, value = rng.choice(rows)
    phrase = rng.choice(_LEXICON['patterns']['relative'][_lang(lang_en)])
    request = phrase.replace('{w}', word)
    plan = [('current', {}), (tool, {'date': '@1', DOMAIN[tool][1]: value})]
    request, plan = _consume(rng, lang_en, request, plan)
    return _preamble(rng) + request, render(plan)


def _render_relative_chain(rng, lang_en, minus=True):
    # two relative words chain into two shifts from today: a day word
    # first, then a month or year word -- three steps fill the plan, so
    # no consumer fits here
    words = _LEXICON['relative_words'][_lang(lang_en)]
    rows = [(word, row['tool'], str(row['shift'])) for word, row in words.items()]
    if not minus:
        rows = [row for row in rows if not row[2].startswith('-')]
    word, tool, value = rng.choice([row for row in rows if row[1] == 'add_days'])
    word2, tool2, value2 = rng.choice([row for row in rows if row[1] != 'add_days'])
    phrase = rng.choice(_LEXICON['patterns']['relative_chain'][_lang(lang_en)])
    request = phrase.replace('{w}', word).replace('{w2}', word2)
    plan = [
        ('current', {}),
        (tool, {'date': '@1', DOMAIN[tool][1]: value}),
        (tool2, {'date': '@2', DOMAIN[tool2][1]: value2}),
    ]
    return _preamble(rng) + request, render(plan)


def _render_signed_nomatch(rng, lang_en):
    # a sign written onto a bare count is not part of the language, in
    # every phrasing: bare ('plus -2 days', 'add +5 months to ...') AND
    # consumer-wrapped ('the weekday of today plus -2 days'). direction is
    # carried by words, never by a sign in the request, so a sign flips the
    # whole request to <nomatch> no matter how it is dressed. taught across
    # both forms so the model answers with the honest echo instead of
    # quietly dropping the shift or copying a confused digit. generated,
    # varying over units, counts, languages, signs, the date kind, and the
    # optional consumer
    unit = _unit(rng, lang_en)
    group = 'shift_minus' if rng.random() < 0.5 else 'shift'
    # all patterns now, plain or with a consumer {c}
    phrase = rng.choice(_LEXICON['patterns'][group][_lang(lang_en)])
    count = _count(rng, unit)
    sign = rng.choice(['-', '+'])
    request = phrase.replace('{n}', sign + str(count))
    request = request.replace('{u}', rng.choice(unit['one'] if count == 1 else unit['many']))
    # the date is a time word most of the time, sometimes a literal date,
    # so 'today plus -2 days' and '2026-06-08 plus -2 days' both echo
    if '{d}' in request:
        request = request.replace('{d}', _time_word(rng, lang_en) if rng.random() < 0.6 else _iso(rng))
    # fill the consumer name when the chosen pattern carries one
    if '{c}' in request:
        _, name = _consumer(rng, lang_en)
        request = request.replace('{c}', name)
    return _preamble(rng) + request, NOMATCH


def _render_shift(rng, lang_en, minus=True):
    # one renderer for every shift, plain or composed: the unit wording
    # (days, months, years) picks the tool, the request shows the bare
    # count, the sign lives in the plan, and a {c} appends the consumer
    unit = _unit(rng, lang_en)
    tool = unit['tool']
    sign = '-' if minus and rng.random() < 0.4 else ''
    group = 'shift_minus' if sign else 'shift'
    phrase = rng.choice(_LEXICON['patterns'][group][_lang(lang_en)])
    count = _count(rng, unit)
    request = phrase.replace('{n}', str(count)).replace('{u}', rng.choice(unit['one'] if count == 1 else unit['many']))
    plan = []
    if '{d}' in request and rng.random() < 0.5:
        value = _iso(rng)
        request = request.replace('{d}', value)
        base = value
    else:
        # a time word or an ago/vor wording: today is implied
        request = request.replace('{d}', _time_word(rng, lang_en))
        plan.append(('current', {}))
        base = '@1'
    # the request shows the bare count and the unit word ("1 week"); the
    # plan carries count x factor, so a week becomes 7 days on add_days
    # (there is no add_weeks tool -- a week shift IS a seven-day shift)
    amount = count * unit.get('factor', 1)
    plan.append((tool, {'date': base, DOMAIN[tool][1]: sign + str(amount)}))
    request, plan = _consume(rng, lang_en, request, plan)
    return _preamble(rng) + request, render(plan)


def _render_now(rng, lang_en):
    # a bare time word standing on its own means "the current date":
    # today/now/current and heute/jetzt/aktuell all resolve to current().
    # without this the time words are only ever seen as the {d} slot of a
    # larger pattern (e.g. "5 days before today"), so a bare "today" would
    # be out of distribution and the model would bolt a spurious shift onto
    # it. _preamble adds the usual optional preamble and lead-in for variety
    word = _time_word(rng, lang_en)
    return _preamble(rng) + word, render([('current', {})])


def _render_date_diff_relative(rng, lang_en):
    # date_diff where the first endpoint is today and the second is a date
    # relative to today: current() resolves today (@1), one forward shift
    # produces the other endpoint (@2), and date_diff counts the days
    # between them. exactly three steps, so it fits the plan budget. only
    # forward relative words are used, so the count is positive and the
    # phrasing natural ("from today until tomorrow"). two relative endpoints
    # ("from yesterday until tomorrow") would need four steps -- a shift for
    # each endpoint plus the diff -- which exceeds the cap, so they are
    # deliberately out of scope
    words = _LEXICON['relative_words'][_lang(lang_en)]
    forward = [(word, row['tool'], str(row['shift'])) for word, row in words.items() if row['shift'] > 0]
    word, tool, value = rng.choice(forward)
    phrase = rng.choice(_LEXICON['patterns']['single']['date_diff'][_lang(lang_en)])
    request = phrase.replace('{d}', _time_word(rng, lang_en)).replace('{d2}', word)
    plan = [
        ('current', {}),
        (tool, {'date': '@1', DOMAIN[tool][1]: value}),
        ('date_diff', {'start': '@1', 'end': '@2'}),
    ]
    return _preamble(rng) + request, render(plan)


def _render_single(rng, tool, phrase, lang_en):
    # one call to a non-shifting tool; a date mention is literal or a time
    # word, and a time word resolves through current as step one
    plan = []
    request = phrase
    first = '@1'
    if '{d}' in phrase:
        if rng.random() < 0.35:
            request = request.replace('{d}', _time_word(rng, lang_en))
            plan.append(('current', {}))
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
    else:
        plan.append((tool, {'date': first}))
    return _preamble(rng) + request, render(plan)


def _typo(rng, request):
    # one human typo, a doubled letter or swapped neighbours ('tomorrow'
    # becomes 'tommorrow'), so the byte matcher survives sloppy fingers;
    # only letter pairs qualify -- digits and date characters are never
    # touched, the plan side stays exact
    spots = [i for i in range(len(request) - 1) if request[i].isalpha() and request[i + 1].isalpha()]
    if not spots:
        return request
    i = rng.choice(spots)
    if rng.random() < 0.5:
        return request[:i] + request[i] + request[i:]
    return request[:i] + request[i + 1] + request[i] + request[i + 2:]


def dataset(count, seed=7, minus=True):
    """
    Generate a deduplicated list of (request, dsl) pairs.

    ### Args

    - **count** (int): How many unique examples to return
    - **seed** (int): The rng seed; same seed and lexicon, same dataset
    - **minus** (bool): Include the backward wordings (minus/ago and the
        backward relative words); ```False``` builds the ablation dataset
        without any minus teaching

    ### Returns

    - **list**: (request, plan line) tuples, requests unique

    """
    rng = random.Random(seed)
    seen = set()
    examples = []
    while len(examples) < count:
        request, dsl = sample(rng, minus)
        # a small typo share teaches robustness against sloppy fingers
        if rng.random() < 0.06:
            request = _typo(rng, request)
        if request not in seen:
            seen.add(request)
            examples.append((request, dsl))
    return examples


if __name__ == '__main__':
    import sys

    for request, dsl in dataset(12, minus='--no-minus' not in sys.argv):
        print(f'{request!r:80s} -> {dsl}')
