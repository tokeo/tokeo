"""
Akili plan DSL for the Spiral ai micro model.

The trained akili model does one thing: it turns a request into a *plan*,
written in a tiny deterministic language. One line, steps joined by ``;``,
each step a tool call; ``@k`` references the result of step k; a request
outside the domain becomes ``<nomatch>``::

    current();add_days(date=@1,days=2);weekday(date=@2)
    date_diff(start=2026-06-07,end=2026-12-24)
    <nomatch>

This module has three jobs. ``render`` serializes a plan to that line,
``parse`` reads a line back into steps (validating as it goes), and the
``Constrainer`` -- the important one -- is a byte-level grammar automaton:
during decoding it answers, character by character, which next bytes are
legal. The decoder may only emit accepted bytes, so the model can never
produce a malformed plan, an unknown tool, or a tool the runtime did not
activate. The model supplies *judgement* (which legal continuation), the
automaton supplies the *fence* (what is legal at all).
"""

# the sentinel a request outside the calendar domain maps to; it is also a
# legal "plan", so the model can honestly abstain instead of inventing one
NOMATCH = '<nomatch>'

# the closed domain akili is trained for: the calendar toolset, each tool
# mapped to its ordered list of argument slots. this ordering is load-
# bearing -- render writes slots in this order, parse checks the keys come
# back in exactly this order, and the Constrainer fills them one by one in
# this order. adding a tool here (and to the lexicon + the project's tool
# classes) is how the language grows; the slot list IS the tool's schema
DOMAIN = {
    'current': [],                          # zero args: just "what day is it"
    'date_diff': ['start', 'end'],          # two dates in, a day count out
    'add_days': ['date', 'days'],           # shift a date by a signed count
    'add_months': ['date', 'months'],
    'add_years': ['date', 'years'],
    'weekday': ['date'],                    # consumers: one date in, a fact out
    'week_number': ['date'],
    'moon_phase': ['date'],
}

# slots whose value is a date (iso YYYY-MM-DD or an @k back-reference); every
# other slot (days/months/years) holds a small signed integer. the
# Constrainer branches on this set to know which value grammar to enforce
_DATE_SLOTS = {'start', 'end', 'date'}


def render(plan):
    """
    Render a plan into its single DSL line.

    ### Args

    - **plan** (list): Steps as ``(tool, {slot: value})`` tuples

    ### Returns

    - **str**: The DSL line, or ``<nomatch>`` for an empty plan

    """
    # an empty plan is the honest "no calendar action" answer
    if not plan:
        return NOMATCH
    steps = []
    for tool, arguments in plan:
        # join the slots as key=value; dict insertion order matches the
        # order the renderers build them, which matches DOMAIN order
        inner = ','.join(f'{key}={value}' for key, value in arguments.items())
        steps.append(f'{tool}({inner})')
    # the steps of one plan are joined by semicolons into one line
    return ';'.join(steps)


def parse(text):
    """
    Parse a DSL line back into a plan, validating every part.

    Each ``raise`` here is a guardrail: at training time it catches a
    malformed target, at inference time it would catch a plan the grammar
    somehow let through (it never should). The checks are deliberately
    strict -- a plan is either exactly well-formed or rejected.

    ### Args

    - **text** (str): The DSL line (``<nomatch>`` gives an empty plan)

    ### Returns

    - **list**: Steps as ``(tool, {slot: value})`` tuples; values stay
        strings, including ``@k`` references

    ### Raises

    - **ValueError**: If the line is not valid DSL

    """
    text = (text or '').strip()
    # the empty line and the abstention sentinel both mean "no steps"
    if not text or text == NOMATCH:
        return []
    plan = []
    for step in text.split(';'):
        # every step must look like tool(...) -- an open paren and a
        # trailing close paren; a truncated step (no ')') fails here, which
        # is exactly how a too-short decoder budget would surface
        if '(' not in step or not step.endswith(')'):
            raise ValueError(f'malformed step: {step!r}')
        # split off the trailing ')' then split tool name from its inner args
        tool, inner = step[:-1].split('(', 1)
        # the tool must be one of the eight known tools
        if tool not in DOMAIN:
            raise ValueError(f'unknown tool: {tool!r}')
        arguments = {}
        if inner:
            for pair in inner.split(','):
                # split key=value; partition keeps an empty value visible
                key, _, value = pair.partition('=')
                # the key must be a real slot of this tool and have a value
                if key not in DOMAIN[tool] or not value:
                    raise ValueError(f'malformed argument: {pair!r}')
                arguments[key] = value
        # final check: the slots present, in order, must equal the tool's
        # full schema -- no missing, extra, or reordered slots
        if list(arguments.keys()) != DOMAIN[tool]:
            raise ValueError(f'arguments mismatch for {tool!r}')
        plan.append((tool, arguments))
    return plan


class Constrainer:
    """
    Byte-level grammar automaton for constrained greedy decoding.

    It holds the partial DSL line emitted so far and, on demand, reports the
    set of characters that may legally come next (or ``{'<eos>'}`` when the
    line is complete). The decoder consults it before every character and
    picks the highest-scored *legal* one -- so the grammar, not luck, keeps
    the output well formed.

    The ``active`` set is the runtime tie-in: only tools the profile and
    agent activated are offered, so the model cannot plan a tool that is not
    available in this call even if it learned about it in training.

    """

    def __init__(self, active=None):
        # restrict the plannable tools to the active set, preserving DOMAIN
        # order; active=None means "all tools" (used during training/eval)
        self._active = [name for name in DOMAIN if active is None or name in active]
        # the decoded characters so far; the automaton is a pure function of
        # this string plus the active set, so it needs no other state
        self._text = ''

    def feed(self, byte):
        """Accept one decoded character and extend the partial line."""
        self._text += byte

    def allowed(self):
        """
        Return the set of legal next characters.

        ### Returns

        - **set**: Single-character strings, or ``{'<eos>'}`` when the line
            is a complete plan and may end

        """
        return _allowed(self._text, self._active)


def _starts(prefix, options):
    # a tiny prefix-trie step: given what is typed so far (prefix) and the
    # words it could be completing (options), return the set of characters
    # that continue some option, and whether the prefix already equals a
    # whole option (so the caller may also offer a terminator)
    chars = set()
    complete = False
    for option in options:
        if option == prefix:
            # the prefix is already a full option (e.g. a finished tool name)
            complete = True
        elif option.startswith(prefix):
            # the next character that would extend toward this option
            chars.add(option[len(prefix):][0])
    return chars, complete


def _allowed(text, active):
    # the heart of the grammar: walk the partial line and decide the legal
    # continuations. the DSL is regular enough for a hand-written automaton,
    # which is easier to read (and to trust) than a parser generator.
    #
    # isolate the step being typed (everything after the last ';') and which
    # step number it is (1-based), so we can enforce the plan-length cap and
    # the @k reference range
    step = text.rsplit(';', 1)[-1]
    step_index = text.count(';') + 1
    # case 1: nothing typed yet -- the line may begin with any active tool
    # name or with the abstention sentinel <nomatch>
    if step == '' and text == '':
        chars, _ = _starts('', active + [NOMATCH])
        return chars
    # case 2: the line is exactly <nomatch> -- complete, only EOS may follow
    if text == NOMATCH:
        return {'<eos>'}
    # case 3: we are still spelling <nomatch> (only valid as the whole line,
    # so never after a ';'); keep offering its next characters
    if NOMATCH.startswith(step) and ';' not in text:
        chars, _ = _starts(step, [NOMATCH])
        if chars:
            return chars
    # case 4: still inside a tool name (no '(' yet) -- offer the characters
    # that continue some active tool name, and '(' once the name is complete
    if '(' not in step:
        chars, complete = _starts(step, active)
        if complete:
            chars.add('(')
        return chars
    # case 5: the step is closed with ')' -- decide whether to chain or end
    if step.endswith(')'):
        return _after_close(text, step_index)
    # otherwise we are inside the argument list of a tool call
    tool, inner = step.split('(', 1)
    slots = DOMAIN.get(tool, [])
    # case 6: a zero-argument tool (current) -- the only legal byte is ')'
    # while empty, then it behaves as a closed step
    if not slots:
        return {')'} if inner == '' else _after_close(text, step_index)
    # the args typed so far, comma-separated; the one being typed is the last
    pairs = inner.split(',')
    # which slot are we on? the (count-1)-th, in DOMAIN order; None if we have
    # somehow overshot the schema (a dead end the grammar never reaches)
    slot = slots[len(pairs) - 1] if len(pairs) <= len(slots) else None
    if slot is None:
        return set()
    current = pairs[-1]
    # split the current pair into key / '=' / value
    key, eq, value = current.partition('=')
    # case 7: still spelling the slot name -- offer its remaining characters,
    # and '=' once the slot name is complete
    if not eq:
        chars, complete = _starts(key, [slot])
        if complete:
            chars.add('=')
        return chars
    # the value's terminator depends on whether more slots follow: a comma if
    # this is not the last slot, otherwise the closing ')'
    closers = {')'} if len(pairs) == len(slots) else {','}
    # case 8: fill the value with the grammar that fits the slot's type
    if slot in _DATE_SLOTS:
        return _date_value(value, step_index, closers)
    return _int_value(value, closers)


def _date_value(value, step_index, closers):
    # a date slot accepts either a back-reference @k or a literal iso date.
    if value.startswith('@'):
        # @ alone: offer the indices of earlier steps (1..step_index-1); a
        # reference can only point at a result that already exists
        if len(value) == 1:
            return {str(k) for k in range(1, step_index)}
        # @k is complete (k is a single digit here) -- only a closer follows
        return set(closers)
    digits = set('0123456789')
    # the fixed iso layout: four year digits, dash, two month, dash, two day
    layout = 'dddd-dd-dd'
    if len(value) < len(layout):
        # the next position is either a digit ('d') or the literal '-'
        expect = layout[len(value)]
        chars = digits if expect == 'd' else {'-'}
        # at the very first character also allow '@' to start a reference,
        # but only when there is an earlier step to point at
        if not value:
            chars = set(chars) | {'@'} if step_index > 1 else set(chars)
        return chars
    # the date is full (10 chars) -- only a closer follows
    return set(closers)


def _int_value(value, closers):
    # a small signed integer (days/months/years). the digit cap below is a
    # real modelling choice: at most three digits means the grammar accepts
    # 0..999, enough for day shifts up to 365. raising it to four would let
    # the model emit four-digit counts -- which it never learns, since the
    # data caps counts well under 1000, so they would be pure noise
    digits = set('0123456789')
    # an empty value may start with a digit, or with '-' for a negative; a
    # lone '-' must then be followed by a digit
    if value in ('', '-'):
        chars = set(digits)
        if value == '':
            chars.add('-')
        return chars
    # strip the optional sign before counting digits; under three digits we
    # may add another digit or close, at three we must close
    if len(value.lstrip('-')) < 4:
        return digits | set(closers)
    return set(closers)


def _after_close(text, step_index):
    # a closed step may chain into another (';') or end the line ('<eos>').
    # the cap of three steps is the deepest plan the model is taught:
    # resolve a date, shift it, consume the result. it must agree with two
    # other places -- the renderers never build longer plans, and infer.py's
    # PLAN_BUDGET reserves room for the longest line. raising it to four
    # would require training four-step plans AND widening that budget
    return {';', '<eos>'} if step_index < 3 else {'<eos>'}
