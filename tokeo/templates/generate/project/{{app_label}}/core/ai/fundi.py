"""
Fundi ai provider for the {{ app_name }} application.

``fundi`` is the project's own micro language model: a few hundred thousand
parameters trained from scratch on the project's synthetic calendar data (the
lab lives in ``{{ app_label }}/app/fundi``), running in-process with plain NumPy -- no
host to start, no network, deterministic. It plans tool chains, including
nested requests like "the weekday of today plus 2 days", as a constrained
plan DSL decoded greedily over the active tools, so it can never emit a
malformed plan or a tool outside the injection.

Its capability is small but clearly defined: the calendar domain it was
trained for, in English and German. Outside that domain it answers with a
labelled echo instead of guessing, it never invents arguments, and on
``denied:`` or ``error:`` feedback it explains instead of retrying. The
weights are a project asset: run ``make fundi-train`` to create (or improve)
``{{ app_label }}/app/fundi/weights.npz``; without them this provider raises a clear
error and the neutral ``mock`` stays available.

This module is self-contained: it holds only the provider class. The project
names it by its full dotted class path as the ``type`` of a profile under
``ai.profiles``, so it needs no registration and no entry in the app
extensions; the handler imports and instantiates it on demand.
"""

import re

from tokeo.core.ai import TokeoAiError, TokeoAiProvider, ChatResult, ToolCall, Usage


_ISO_DATE = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')
_INTEGER = re.compile(r'(?<![\d-])-?\d+(?![\d-])')


class TokeoAiFundiProvider(TokeoAiProvider):
    """
    The project's own trained micro language model.

    ### Notes

    : Replies are labelled (``[fundi] ...``), so it stays obvious which
        model answered. The plan the model decided is inspectable in the
        result's ``raw`` payload next to the answer.

    """

    _engine = None

    def chat(self, profile, messages, tools=None):
        """
        Run one deterministic model turn.

        ### Args

        - **profile** (dict): The resolved profile (unused settings pass
            through untouched)
        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **tools** (list | None): OpenAI-style function specs; the decoder
            is constrained to these active tools

        ### Returns

        - **ChatResult**: The deterministic response (text or tool calls)

        ### Raises

        - **TokeoAiError**: If no trained weights exist yet

        """
        prompt, feedback = self._conversation(messages)
        # denied or failed feedback ends the run with the reason; the micro
        # model does not retry, so a stuck loop can never build up
        if feedback and feedback[-1].startswith('denied:'):
            return self._result(f'[fundi] not permitted: {feedback[-1][len("denied:"):].strip()}', prompt)
        if feedback and feedback[-1].startswith('error:'):
            return self._result(f'[fundi] failed: {feedback[-1][len("error:"):].strip()}', prompt)
        plan = self._plan(prompt, tools or [])
        done = len(feedback)
        if done < len(plan):
            name, arguments = plan[done]
            # a @k argument takes the date (or number) from the feedback of
            # step k, so a chained step works with what came before it
            arguments = {key: self._resolve(value, feedback) for key, value in arguments.items()}
            call = ToolCall(id=f'call_{done + 1}', name=name, arguments=arguments)
            return self._result('', prompt, tool_calls=[call], finish_reason='tool_calls', plan=plan)
        if plan:
            return self._result(f'[fundi] {plan[-1][0]}: {feedback[-1]}', prompt, plan=plan)
        return self._result(f'[fundi] {prompt}' if prompt else '[fundi] (no prompt)', prompt)

    def _plan(self, prompt, tools):
        # the trained model (app/fundi) emits the plan as constrained DSL
        # over the active tools; numpy and the weights load lazily, and a
        # missing asset turns into a clear, actionable error
        try:
            from {{ app_label }}.app.fundi.dsl import parse
            from {{ app_label }}.app.fundi.infer import FundiModel
        except ImportError as error:
            raise TokeoAiError(f'fundi needs the app/fundi lab and numpy: {error}')
        if self._engine is None:
            try:
                self._engine = FundiModel()
            except FileNotFoundError:
                raise TokeoAiError("fundi has no trained weights yet -- run 'make fundi-train' to create {{ app_label }}/app/fundi/weights.npz")
        active = {((spec or {}).get('function') or {}).get('name') for spec in tools}
        steps = []
        for name, arguments in parse(self._engine.plan(prompt, active=active)):
            typed = {}
            for key, value in arguments.items():
                # plan values are text; bare integers become numbers so the
                # validate guard sees schema-true arguments
                typed[key] = int(value) if value.lstrip('-').isdigit() else value
            steps.append((name, typed))
        return steps

    def _resolve(self, value, feedback):
        # a @k value takes the date, the number, or the trimmed text from
        # the feedback of step k
        if isinstance(value, str) and value.startswith('@') and value[1:].isdigit():
            index = int(value[1:]) - 1
            if 0 <= index < len(feedback):
                return self._from_result(feedback[index])
        return value

    def _from_result(self, text):
        date = _ISO_DATE.search(text or '')
        if date:
            return date.group(0)
        number = _INTEGER.search(text or '')
        if number:
            return int(number.group(0))
        return (text or '').strip()

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

    def _result(self, text, prompt, tool_calls=None, finish_reason='stop', plan=None):
        # the raw payload carries the plan, so what the model decided stays
        # inspectable next to what it answered
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
            raw={'provider': 'fundi', 'model': 'fundi', 'plan': [name for name, _ in (plan or [])]},
        )
