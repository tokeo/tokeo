"""
Built-in ```redact``` guard for Tokeo applications.

Masks secret-looking spans in a tool's model-facing result text after the tool
ran, so a value a tool happened to surface (a token in a file it read, a key in
a fetched page) does not flow on into the message history, the trace, or the
```audit``` log line. It runs in the after-phase and blocks nothing; it only
rewrites ```result.text``` (the structured ```result.data``` is left untouched, as
it stays out of the message history).

Redaction is best-effort masking by pattern, not a guarantee: the built-in set
targets a few high-precision secret shapes (bearer tokens, ```sk-``` keys, AWS
key ids, ```password:```/```token:``` assignments), and a project extends or
replaces it through the ```patterns``` option for the shapes its own data carries.
List it before ```truncate``` so a secret is masked across the whole text before
any tail is cut, and before ```audit``` so the log line records the masked text.

```yaml
ai:
  guards:
    redact:
      type: redact
      options:
        # extra regex patterns whose matches are masked, on top of the
        # built-in set; omit to use the built-ins only
        patterns:
          - 'X-Internal-[A-Za-z0-9]+'
        # what each matched span is replaced with
        replacement: '[redacted]'
```

"""

import re

from tokeo.core.ai import TokeoAiGuard


# a few high-precision secret shapes masked by default; each pattern matches
# the secret span itself, so the whole match is replaced. kept conservative
# so ordinary tool output is not mangled -- a project adds its own patterns
# for the shapes its data carries
_DEFAULT_PATTERNS = [
    # an http authorization bearer token
    r'(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}',
    # an openai-style api key
    r'\bsk-[A-Za-z0-9]{16,}\b',
    # an aws access key id
    r'\bAKIA[0-9A-Z]{16}\b',
    # a "name: value" or "name=value" secret assignment
    r'(?i)\b(?:api[_-]?key|secret|password|token)\b\s*[:=]\s*\S+',
]


class TokeoAiRedactGuard(TokeoAiGuard):
    """
    After-phase guard that masks secret-looking spans in a result.

    Applies each configured pattern to ```invocation.result.text``` and replaces
    every match with the ```replacement``` marker. It never changes the
    ```decision```, so it is safe on every agent; when it masks anything it notes
    the count on ```reason``` so the trace shows the result was shaped.

    """

    class Meta:
        """Redact guard meta-data."""

        # masks after the tool ran, on the model-facing result text
        phase = 'after'
        # extra regex patterns on top of the built-in set; None uses only
        # the built-ins
        patterns = None
        # what each matched span is replaced with
        replacement = '[redacted]'

    def _setup(self, app):
        """
        Compile the built-in and configured patterns once.

        ### Args

        - **app**: The Tokeo application instance

        """
        extra = self._meta.patterns or []
        self._compiled = [re.compile(pattern) for pattern in _DEFAULT_PATTERNS + list(extra)]

    def check(self, invocation):
        """
        Mask secret-looking spans in the result text; never blocks.

        ### Args

        - **invocation** (Invocation): The completed tool call whose
            ```result.text``` is masked in place when a pattern matches

        """
        if invocation.result is None:
            return
        text = invocation.result.text or ''
        hits = 0
        for pattern in self._compiled:
            text, count = pattern.subn(self._meta.replacement, text)
            hits += count
        if hits:
            invocation.result.text = text
            note = f'redacted {hits} secret(s)'
            invocation.reason = f'{invocation.reason}; {note}' if invocation.reason else note
