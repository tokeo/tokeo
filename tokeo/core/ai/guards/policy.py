"""
Built-in ```policy``` guard for Tokeo applications.

Allows or denies a tool call by the tool's name, before it runs. This is the
action-level governance baseline: it shapes what an agent may *do*, not just
what it may say. Rules come from the guard entry's ```options``` (```allow``` and
```deny``` lists). A denied call is not executed; the loop continues and the
model sees a ```denied: ...``` result, so the agent can react instead of crash
(deny-and-continue).

```yaml
ai:
  guards:
    safe:
      type: policy
      options:
        deny: [shell]
    mathonly:
      type: policy
      options:
        allow: [calc]
```

"""

from tokeo.core.ai import TokeoAiGuard


class TokeoAiPolicyGuard(TokeoAiGuard):
    """
    Before-phase guard that permits or blocks tool calls by name.

    The rules are read from ```_meta``` (set from the guard entry's
    ```options```): ```deny``` is a denylist and always wins; ```allow```, when set,
    is an allowlist that restricts calls to its members. With neither rule the
    guard permits every call (it then only documents intent).

    """

    class Meta:
        """Policy rules, overridden per guard by its entry's options."""

        # runs before exec, so it can stop a call from running
        phase = 'before'

        # tools allowed; None means "allow any tool not denied"
        allow = None

        # tools always denied; a deny wins over the allowlist
        deny = []

    def check(self, invocation):
        """
        Deny the call when the policy forbids the tool; otherwise allow.

        ### Args

        - **invocation** (Invocation): The tool call to check; on a denial its
            ```decision``` is set to ```deny``` with a ```reason```

        """
        name = invocation.name
        denied = name in (self._meta.deny or [])
        if not denied and self._meta.allow is not None:
            denied = name not in self._meta.allow
        if denied:
            invocation.decision = 'deny'
            invocation.reason = f'tool {name!r} is not permitted by policy'
