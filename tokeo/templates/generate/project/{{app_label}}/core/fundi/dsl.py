"""
Fundi plan DSL for the Spiral ai micro model.

The trained fundi model does one thing: it turns a request into a plan,
written in a tiny deterministic language. One line, steps joined by ``;``,
each step a tool call; ``@k`` references the result of step k; a request
outside the domain becomes ``<nomatch>``::

    current();add_days(date=@1,days=2);weekday(date=@2)
    date_diff(start=2026-06-07,end=2026-12-24)
    <nomatch>

This module renders, parses, and -- most importantly -- constrains the plan
grammar: the decoder may only emit byte sequences this automaton accepts, so
the model can never produce a malformed plan or a tool that is not active.
"""

NOMATCH = '<nomatch>'

# the closed domain the 1.0 model is trained for: the calendar toolset with
# its schema slots (kept in sync with the project's tool classes)
DOMAIN = {
    'current': [],
    'date_diff': ['start', 'end'],
    'add_days': ['date', 'days'],
    'weekday': ['date'],
    'week_number': ['date'],
    'moon_phase': ['date'],
}

# what a value may look like per slot: an iso date (or @k) for date-like
# slots, a small signed integer for days
_DATE_SLOTS = {'start', 'end', 'date'}


def render(plan):
    """Render a plan (list of (tool, {arg: value})) as its DSL line."""
    if not plan:
        return NOMATCH
    steps = []
    for tool, arguments in plan:
        inner = ','.join(f'{key}={value}' for key, value in arguments.items())
        steps.append(f'{tool}({inner})')
    return ';'.join(steps)


def parse(text):
    """
    Parse a DSL line back into a plan.

    ### Args

    - **text** (str): The DSL line (``<nomatch>`` gives an empty plan)

    ### Returns

    - **list**: Steps as (tool, {arg: value}) tuples; values stay strings
        except ``@k`` references which stay literal

    ### Raises

    - **ValueError**: If the line is not valid DSL

    """
    text = (text or '').strip()
    if not text or text == NOMATCH:
        return []
    plan = []
    for step in text.split(';'):
        if '(' not in step or not step.endswith(')'):
            raise ValueError(f'malformed step: {step!r}')
        tool, inner = step[:-1].split('(', 1)
        if tool not in DOMAIN:
            raise ValueError(f'unknown tool: {tool!r}')
        arguments = {}
        if inner:
            for pair in inner.split(','):
                key, _, value = pair.partition('=')
                if key not in DOMAIN[tool] or not value:
                    raise ValueError(f'malformed argument: {pair!r}')
                arguments[key] = value
        if list(arguments.keys()) != DOMAIN[tool]:
            raise ValueError(f'arguments mismatch for {tool!r}')
        plan.append((tool, arguments))
    return plan


class Constrainer:
    """
    Byte-level grammar automaton for constrained greedy decoding.

    Tracks a partial DSL line and answers which next bytes are legal, given
    the set of active tools (the runtime injection tie-in: only tools the
    profile and agent activated may be planned).

    """

    def __init__(self, active=None):
        # the active tool names restrict the trie; defaults to the domain
        self._active = [name for name in DOMAIN if active is None or name in active]
        self._text = ''

    def feed(self, byte):
        """Accept one decoded byte (as a 1-char string)."""
        self._text += byte

    def allowed(self):
        """
        Return the set of legal next characters (or {'<eos>'} when a plan
        line is complete).

        """
        return _allowed(self._text, self._active)


def _starts(prefix, options):
    # the next legal characters to continue any option from this prefix
    chars = set()
    complete = False
    for option in options:
        if option == prefix:
            complete = True
        elif option.startswith(prefix):
            chars.add(option[len(prefix):][0])
    return chars, complete


def _allowed(text, active):
    # walk the partial line and compute the legal continuations; the DSL is
    # simple enough for a hand automaton: steps of tool(...)[;tool(...)]*
    step = text.rsplit(';', 1)[-1]
    step_index = text.count(';') + 1
    if step == '' and text == '':
        chars, _ = _starts('', active + [NOMATCH])
        return chars
    if text == NOMATCH:
        return {'<eos>'}
    if NOMATCH.startswith(step) and ';' not in text:
        chars, _ = _starts(step, [NOMATCH])
        if chars:
            return chars
    if '(' not in step:
        chars, complete = _starts(step, active)
        if complete:
            chars.add('(')
        return chars
    if step.endswith(')'):
        # the step is closed: chain on or end the plan
        return _after_close(text, step_index)
    tool, inner = step.split('(', 1)
    slots = DOMAIN.get(tool, [])
    if not slots:
        # a zero-arg tool closes immediately
        return {')'} if inner == '' else _after_close(text, step_index)
    pairs = inner.split(',')
    slot = slots[len(pairs) - 1] if len(pairs) <= len(slots) else None
    if slot is None:
        return set()
    current = pairs[-1]
    key, eq, value = current.partition('=')
    if not eq:
        chars, complete = _starts(key, [slot])
        if complete:
            chars.add('=')
        return chars
    closers = {')'} if len(pairs) == len(slots) else {','}
    if slot in _DATE_SLOTS:
        return _date_value(value, step_index, closers)
    return _int_value(value, closers)


def _date_value(value, step_index, closers):
    # a date value is YYYY-MM-DD or a @k reference to an earlier step
    if value.startswith('@'):
        if len(value) == 1:
            return {str(k) for k in range(1, step_index)}
        return set(closers)
    digits = set('0123456789')
    layout = 'dddd-dd-dd'
    if len(value) < len(layout):
        expect = layout[len(value)]
        chars = digits if expect == 'd' else {'-'}
        if not value:
            chars = set(chars) | {'@'} if step_index > 1 else set(chars)
        return chars
    return set(closers)


def _int_value(value, closers):
    # a small signed integer; one to three digits is plenty for days
    digits = set('0123456789')
    if value in ('', '-'):
        chars = set(digits)
        if value == '':
            chars.add('-')
        return chars
    if len(value.lstrip('-')) < 4:
        return digits | set(closers)
    return set(closers)


def _after_close(text, step_index):
    # after a closed step the plan may chain (up to three steps) or end
    return {';', '<eos>'} if step_index < 3 else {'<eos>'}
