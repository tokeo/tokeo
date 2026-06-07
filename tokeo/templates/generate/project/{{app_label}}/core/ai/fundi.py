"""
Fundi ai provider for the {{ app_name }} application.

The ``fundi`` provider is the project's own local micro model: callable
in-process, no server, no network, and deterministic -- the same input always
produces the same output. The version is selected through the profile's
``options.model``; ``fundi0.1`` is the default.

``fundi0.1`` has a small but clearly defined capability:

- it honors the OpenAI-style conversation (the last user message is the
    request, ``tool`` messages are feedback of the current run),
- it actually reads the injected tool specs: a tool is recognized by its
    name or by its description keywords anywhere in the request,
- it extracts arguments by the tool's ``parameters`` schema (ISO dates
    ``YYYY-MM-DD``, plain integers, or -- for a tool with a single required
    string -- the request remainder after the tool name),
- it resolves the time words today/now/tonight (and the German heute/jetzt)
    through an available current-date tool and chains at most two calls,
    feeding the first result into the second,
- it never invents arguments: a matched tool whose required arguments
    cannot be filled from the request is dropped, and on ``denied:`` or
    ``error:`` feedback it answers with the reason instead of retrying.

``fundi0.0`` stays the plain labelled echo, the simplest rung of the ladder;
later a small trained model (``fundi1.0``) can take its place behind the same
provider.

This module is self-contained: it holds only the provider class. The project
names it by its full dotted class path as the ``type`` of a profile under
``ai.profiles``, so it needs no registration and no entry in the app
extensions; the handler imports and instantiates it on demand.
"""

import re

from tokeo.core.ai import TokeoAiProvider, ChatResult, ToolCall, Usage


# the model's tiny language knowledge: words that mean "the current date",
# resolved through an available current-date tool (the time-word bridge)
_TIME_WORDS = {'today', 'now', 'tonight', 'heute', 'jetzt'}

# schema property names (or descriptions mentioning "date") treated as dates
_DATE_NAMES = {'date', 'start', 'end', 'from', 'to'}

# marker for an argument filled from the previous tool result in a chain
_PREV = '<prev>'

_ISO_DATE = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')
_INTEGER = re.compile(r'(?<![\d-])-?\d+(?![\d-])')


def _words(text):
    # the significant lowercase words of a text; short fill words carry no
    # intent, so they never count as a match
    return {word for word in re.findall(r'[a-z_]+', (text or '').lower()) if len(word) >= 4}


def _is_date_param(name, spec):
    # a string property counts as a date when its name or description says so
    if (spec or {}).get('type') not in (None, 'string'):
        return False
    return name.lower() in _DATE_NAMES or 'date' in str((spec or {}).get('description') or '').lower()


class TokeoAiFundiProvider(TokeoAiProvider):
    """
    The project's own local micro model, deterministic, versioned.

    ### Notes

    : Replies are labelled with the model version (``[fundi0.1] ...``), so
        it stays obvious which fundi answered. ``fundi0.1`` plans tool calls
        from the injected specs as documented in the module docstring;
        ``fundi0.0`` is the plain labelled echo.

    """

    def chat(self, profile, messages, tools=None):
        """
        Run one deterministic model turn.

        ### Args

        - **profile** (dict): The resolved profile; ``options.model`` selects
            the fundi version (default ``fundi0.1``)
        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **tools** (list | None): OpenAI-style function specs; ``fundi0.1``
            plans its calls from them, ``fundi0.0`` ignores them

        ### Returns

        - **ChatResult**: The deterministic response (text or tool calls)

        """
        model = ((profile or {}).get('options') or {}).get('model') or 'fundi0.1'
        prompt, feedback = self._conversation(messages)
        if model == 'fundi0.0':
            return self._result(model, f'[{model}] {prompt}' if prompt else f'[{model}] (no prompt)', prompt)
        # denied or failed feedback ends the run with the reason; a micro
        # model does not retry, so a stuck loop can never build up
        if feedback and feedback[-1].startswith('denied:'):
            return self._result(model, f'[{model}] not permitted: {feedback[-1][len("denied:"):].strip()}', prompt)
        if feedback and feedback[-1].startswith('error:'):
            return self._result(model, f'[{model}] failed: {feedback[-1][len("error:"):].strip()}', prompt)
        plan = self._plan(prompt, tools or [])
        done = len(feedback)
        if done < len(plan):
            name, arguments = plan[done]
            # a chained argument takes the date (or number) from the previous
            # tool result, so step two works with what step one returned
            if done:
                arguments = {key: self._from_result(feedback[-1]) if value == _PREV else value for key, value in arguments.items()}
            call = ToolCall(id=f'call_{done + 1}', name=name, arguments=arguments)
            return self._result(model, '', prompt, tool_calls=[call], finish_reason='tool_calls', plan=plan)
        if plan:
            return self._result(model, f'[{model}] {plan[-1][0]}: {feedback[-1]}', prompt, plan=plan)
        return self._result(model, f'[{model}] {prompt}' if prompt else f'[{model}] (no prompt)', prompt)

    def _conversation(self, messages):
        # the request is the last user message; the feedback is every tool
        # result that came back after it (the current run of the loop)
        prompt = ''
        feedback = []
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            if message.get('role') == 'user':
                prompt = message.get('content') or ''
                feedback = []
            elif message.get('role') == 'tool':
                feedback.append(str(message.get('content') or ''))
        return prompt, feedback

    def _plan(self, prompt, tools):
        # match the request against the injected specs: a tool is recognized
        # by its name (strong) or by at least two description keywords; the
        # order of the plan follows the first evidence in the request
        request = (prompt or '').lower()
        request_words = _words(request)
        # date values queue up in the order they appear in the request; a
        # time word (today, now, ...) joins the queue at its own position as
        # a placeholder, so "from today until X" fills start before end
        dates = [(match.start(), match.group(0)) for match in _ISO_DATE.finditer(request)]
        for match in re.finditer(r'[a-z_]+', request):
            if match.group(0) in _TIME_WORDS:
                dates.append((match.start(), _PREV))
                break
        dates = [value for _, value in sorted(dates)]
        numbers = [int(number) for number in _INTEGER.findall(_ISO_DATE.sub(' ', request))]
        wants_current = _PREV in dates
        matched = []
        for spec in tools:
            function = (spec or {}).get('function') or {}
            name = function.get('name') or ''
            # the name counts in both spellings: as written and with the
            # underscores spoken as spaces
            forms = [form for form in (name.lower(), name.lower().replace('_', ' ')) if form]
            found = [request.find(form) for form in forms if form in request]
            position = min(found) if found else None
            hits = _words(function.get('description')) & request_words
            if position is None and len(hits) < 2:
                continue
            if position is None:
                position = min(request.find(word) for word in hits)
            matched.append((position, name, function))
        matched.sort()
        plan = []
        bridge = False
        for _, name, function in matched[:2]:
            arguments, missing_date = self._arguments(name, function, request, dates, numbers, bool(plan))
            if arguments is None:
                # never invent arguments: an unfillable tool is dropped, the
                # validate guard stays the safety net for real models
                continue
            if missing_date and wants_current and not plan:
                bridge = True
            plan.append((name, arguments))
        if bridge:
            current = self._current_tool(tools, plan)
            if current:
                # the time-word bridge: resolve "today" through the current
                # tool first and feed its result into the planned call
                plan.insert(0, (current, {}))
        # an unresolved placeholder without the bridge cannot run; drop it
        plan = [step for index, step in enumerate(plan) if _PREV not in step[1].values() or index > 0]
        return plan[:2]

    def _arguments(self, name, function, request, dates, numbers, chained):
        # fill the schema properties in their declared order from the typed
        # values found in the request; required ones must all resolve
        properties = ((function.get('parameters') or {}).get('properties')) or {}
        required = (function.get('parameters') or {}).get('required') or []
        arguments = {}
        missing_date = False
        date_queue = list(dates)
        number_queue = list(numbers)
        for key, spec in properties.items():
            kind = (spec or {}).get('type')
            if _is_date_param(key, spec):
                if date_queue:
                    arguments[key] = date_queue.pop(0)
                    missing_date = missing_date or arguments[key] == _PREV
            elif kind in ('integer', 'number'):
                if number_queue:
                    arguments[key] = number_queue.pop(0)
            elif kind in (None, 'string') and key in required and len(required) == 1 and not arguments:
                # the mock-compatible rule: one required string takes the
                # request remainder after the tool name (either spelling)
                remainder = ''
                for form in (name.lower(), name.lower().replace('_', ' ')):
                    position = request.find(form)
                    if form and position >= 0:
                        remainder = request[position + len(form):].strip()
                        break
                if remainder:
                    arguments[key] = remainder
        for key in required:
            if key not in arguments:
                return None, False
        if missing_date and chained:
            # only the first planned tool may lean on the time-word bridge
            return None, False
        return arguments, missing_date

    def _current_tool(self, tools, plan):
        # the zero-required-arg tool that tells the current date, by spec
        planned = {name for name, _ in plan}
        for spec in tools:
            function = (spec or {}).get('function') or {}
            name = function.get('name') or ''
            required = ((function.get('parameters') or {}).get('required')) or []
            evidence = _words(function.get('description')) | {name.lower()}
            if name and name not in planned and not required and evidence & {'current', 'time', 'date'}:
                return name
        return None

    def _from_result(self, text):
        # a chained call takes the date from the previous result, or the
        # bare number, or the trimmed text as a last resort
        date = _ISO_DATE.search(text or '')
        if date:
            return date.group(0)
        number = _INTEGER.search(text or '')
        if number:
            return int(number.group(0))
        return (text or '').strip()

    def _result(self, model, text, prompt, tool_calls=None, finish_reason='stop', plan=None):
        # the raw payload carries the plan, so what the micro model decided
        # stays inspectable next to what it answered
        usage = Usage(
            prompt_tokens=len((prompt or '').split()),
            completion_tokens=len((text or '').split()),
            total_tokens=len((prompt or '').split()) + len((text or '').split()),
        )
        return ChatResult(
            text=text,
            tool_calls=tool_calls or [],
            usage=usage,
            finish_reason=finish_reason,
            raw={'provider': 'fundi', 'model': model, 'plan': [name for name, _ in (plan or [])]},
        )
