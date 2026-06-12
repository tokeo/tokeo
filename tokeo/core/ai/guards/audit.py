"""
Built-in ```audit``` guard for Tokeo applications.

Records the outcome of every tool call to the application log, so what an
agent does is visible without changing what it is allowed to do. It runs in
the after-phase and blocks nothing; it is the baseline transparency layer of
the guard pipeline. The structured record stays on ```ChatResult.trace```; this
guard adds the human-facing log line.

"""

from tokeo.core.ai import TokeoAiGuard


class TokeoAiAuditGuard(TokeoAiGuard):
    """
    Transparency guard that logs each completed invocation.

    Logs whether the call was denied, errored, or returned a result, and
    never changes the ```decision```, so it is safe to keep on every agent.

    """

    class Meta:
        """Audit guard meta-data."""

        # records the outcome, so it runs after the tool has run (or been
        # denied); it never blocks
        phase = 'after'

    def check(self, invocation):
        """
        Log the outcome of an invocation; never blocks.

        ### Args

        - **invocation** (Invocation): The completed tool call to record

        """
        if invocation.decision == 'deny':
            self.app.log.info(f'ai audit: tool {invocation.name!r} denied: {invocation.reason}')
        elif invocation.error is not None:
            self.app.log.info(f'ai audit: tool {invocation.name!r} errored: {invocation.error}')
        else:
            text = invocation.result.text if invocation.result is not None else ''
            self.app.log.info(f'ai audit: tool {invocation.name!r} returned: {text!r}')
