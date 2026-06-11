"""
Built-in ``truncate`` guard for Tokeo applications.

Caps the length of a tool's model-facing result text after the tool ran, so a
call that returns a large blob (a long file read, a big retrieval) cannot blow
the context budget or flood the trace and log. It runs in the after-phase and
blocks nothing; it only shortens ``result.text``, appending a marker that
records how much was cut. The structured ``result.data`` is left untouched, so
a ui or the trace can still reach the full detail.

This is the ``result stage`` the ``ToolResult`` doc refers to, made an explicit,
configurable guard rather than a hidden constant. List it after ``redact`` so a
secret is masked across the whole text before any tail is removed, and before
``audit`` so the log line records the shortened text.

```yaml
ai:
  guards:
    truncate:
      type: truncate
      options:
        # cap the model-facing result text at this many characters; 0
        # disables the cap
        limit: 2000
        # appended in place of the removed tail; "{n}" is the cut count
        marker: '... [truncated {n} chars]'
```

"""

from tokeo.core.ai import TokeoAiGuard


class TokeoAiTruncateGuard(TokeoAiGuard):
    """
    After-phase guard that caps an over-long result text.

    When ``result.text`` is longer than ``limit`` characters it keeps the head
    and appends ``marker`` (with the cut count), so the model still sees the
    start of a large output without the whole blob entering the history. It
    never changes the ``decision`` and notes the cut on ``reason``.

    """

    class Meta:
        """Truncate guard meta-data."""

        # shortens after the tool ran, on the model-facing result text
        phase = 'after'
        # the character cap; 0 disables it
        limit = 2000
        # appended in place of the cut tail; "{n}" is the removed count
        marker = '... [truncated {n} chars]'

    def check(self, invocation):
        """
        Shorten an over-long result text in place; never blocks.

        ### Args

        - **invocation** (Invocation): The completed tool call whose
            ``result.text`` is capped at ``limit`` characters when longer

        """
        if invocation.result is None or not self._meta.limit:
            return
        text = invocation.result.text or ''
        if len(text) <= self._meta.limit:
            return
        cut = len(text) - self._meta.limit
        invocation.result.text = text[: self._meta.limit] + self._meta.marker.format(n=cut)
        note = f'truncated {cut} chars'
        invocation.reason = f'{invocation.reason}; {note}' if invocation.reason else note
