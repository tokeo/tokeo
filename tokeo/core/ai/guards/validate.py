"""
Built-in ```validate``` guard for Tokeo applications.

Checks the arguments of a tool call against the tool's declared ```parameters```
schema before the tool runs, so a malformed call (a hallucinated argument, a
missing required one, a wrong basic type) is denied with a precise reason
instead of crashing the tool. The loop continues and the model sees the
```denied: invalid arguments ...``` feedback, so it can correct the call
(deny-and-continue). List it first in ```agent.guards``` so broken calls are
caught before the other before guards run.

It covers the schema subset tool definitions actually use: ```required```
names, declared ```properties``` (an undeclared argument is denied unless the
schema sets ```additionalProperties: true```), and the basic ```type``` of each
value. A tool without a declared schema is left unchecked.

```yaml
ai:
  guards:
    validate: { type: validate }
  agents:
    audited:
      type: default
      options:
        guards: [validate, audit]
```

"""

from tokeo.core.ai import TokeoAiGuard


# the basic json-schema types a tool declaration uses, mapped to their
# python counterparts; bool subclasses int, so integer and number exclude
# it explicitly in the check below
_TYPES = {
    'string': str,
    'integer': int,
    'number': (int, float),
    'boolean': bool,
    'array': list,
    'object': dict,
    'null': type(None),
}


class TokeoAiValidateGuard(TokeoAiGuard):
    """
    Before-phase guard that denies a tool call with invalid arguments.

    Validates against the schema the handler attached to the invocation
    (```invocation.parameters```). All problems are collected into one reason,
    so the model can fix the whole call at once. With no declared schema
    (no properties and no required names) the call passes unchecked.

    """

    class Meta:
        """Validate guard meta-data."""

        # runs before exec, so a malformed call never reaches the tool
        phase = 'before'

    def check(self, invocation):
        """
        Deny the call when its arguments do not match the tool's schema.

        ### Args

        - **invocation** (Invocation): The tool call to check; on a denial its
            ```decision``` is set to ```deny``` with every problem in ```reason```

        """
        schema = invocation.parameters or {}
        properties = schema.get('properties') or {}
        required = schema.get('required') or []
        # nothing declared means nothing to check; the tool accepts anything
        if not properties and not required:
            return
        arguments = invocation.arguments or {}
        problems = []
        for name in required:
            if name not in arguments:
                problems.append(f'missing required argument {name!r}')
        # an argument outside the declared properties is almost always a
        # hallucinated name and would crash the tool's exec(**arguments);
        # a schema may opt out via additionalProperties: true
        if schema.get('additionalProperties') is not True:
            for name in arguments:
                if name not in properties:
                    problems.append(f'unknown argument {name!r}')
        for name, value in arguments.items():
            declared = (properties.get(name) or {}).get('type')
            expected = _TYPES.get(declared)
            if expected is None:
                continue
            wrong = not isinstance(value, expected)
            # bool passes isinstance checks against int, but a model sending
            # true for a count is a mistake, not a number
            if declared in ('integer', 'number') and isinstance(value, bool):
                wrong = True
            if wrong:
                problems.append(f'argument {name!r} must be of type {declared!r}')
        if problems:
            invocation.decision = 'deny'
            invocation.reason = 'invalid arguments: ' + '; '.join(problems)
