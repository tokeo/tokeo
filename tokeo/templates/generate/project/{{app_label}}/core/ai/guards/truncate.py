"""
Truncate guard example for the {{ app_name }} ai agent.

Caps over-long text so a large payload cannot blow the context budget or flood
the trace and log. It keeps the head and appends a marker that records how much
was cut; it blocks nothing and never changes a ```decision```.

This is a worked example, derived from the core ```TokeoAiTruncateGuard``` type.
It acts at two stages: ```on_return``` caps a completed tool call's
```result.value.as_str``` (a big file read, a large retrieval), and ```on_close```
caps the run's final ```ChatResult.text```. Each stage reads its own settings via
```_config(stage)```, so the config can set a different ```limit```/```marker```
per stage (an ```on_return```/```on_close``` options block) or one shared default.

This module is self-contained: it holds only the guard class. The project names
it by its full dotted class path under ```ai.guards``` in the config, so it
needs no registration and no entry in the app extensions; the handler imports
and instantiates it on demand.

```yaml
ai:
  guards:
    truncate:
      type: {{ app_label }}.core.ai.guards.truncate.{{ app_class_name }}AiTruncateGuard
      options:
        # cap the model-facing text at this many characters; 0 disables the cap
        limit: 2000
        # appended in place of the removed tail; "{n}" is the cut count
        marker: '... [truncated {n} chars]'
```
"""

from tokeo.core.ai.guard import GUARD_STAGE_ON_RETURN, GUARD_STAGE_ON_CLOSE
from tokeo.core.ai.guards.truncate import TokeoAiTruncateGuard


class {{ app_class_name }}AiTruncateGuard(TokeoAiTruncateGuard):
    """
    A truncate guard that caps over-long text at the tool and final stages.

    When the text is longer than ```limit``` characters it keeps the head and
    appends ```marker``` (with the cut count), so the model (or the caller) still
    sees the start of a large output without the whole blob entering the history.
    It never changes the ```decision``` and notes the cut on ```reason``` (at the
    tool stage). Derived from the core ```TokeoAiTruncateGuard``` type; see the
    stage guide in ```TokeoAiGuard``` for what each stage hands a guard.

    """

    class Meta:
        """Truncate guard meta-data."""

        # the configurable defaults, as one dict; a guard entry's options (and a
        # per-stage override) overlay this, read at runtime via _config. limit:
        # the character cap, 0 disables it. marker: appended in place of the cut
        # tail, "{n}" is the removed count
        config_defaults = dict(
            limit=2000,
            marker='... [truncated {n} chars]',
        )

    def _cap(self, text, stage):
        """
        Return ```text``` capped at the stage's ```limit```, plus the cut count.

        ### Args

        - **text** (str): The text to shorten
        - **stage** (str): The stage whose settings to read (```limit```,
            ```marker```)

        ### Returns

        - **tuple**: ```(capped_text, cut)```; ```cut``` is 0 when nothing was
            removed (the cap is off, or the text already fits), in which case
            ```capped_text``` is the original text

        """
        config = self._config(stage)
        limit = config.get('limit')
        if not limit or len(text or '') <= limit:
            return text, 0
        cut = len(text) - limit
        return text[:limit] + config.get('marker').format(n=cut), cut

    def on_return(self, ctx, invocation):
        """
        Shorten an over-long tool result text in place; never blocks.

        Runs at the tool-return station, after the tool ran. ```ctx``` is the
        running state (unused here).

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **invocation** (Invocation): The completed tool call whose
            ```result.value.as_str``` (the model-facing view) is capped when
            longer than ```limit```; the structured views are left untouched

        """
        if invocation.result is None or invocation.result.value is None:
            return
        capped, cut = self._cap(invocation.result.value.as_str or '', GUARD_STAGE_ON_RETURN)
        if not cut:
            return
        # cap only as_str, the model-facing view: truncate shrinks what the model
        # reads to protect the budget, it hides nothing -- so the structured views
        # (as_json, as_data) keep the full output for the trace. coherence across
        # the three views is not needed here, unlike a redact guard that must not
        # leave a secret in an unmasked view
        invocation.result.value.as_str = capped
        note = f'truncated {cut} chars'
        invocation.reason = f'{invocation.reason}; {note}' if invocation.reason else note

    def on_close(self, ctx, result):
        """
        Shorten an over-long final answer text in place; never blocks.

        Runs once on the run's final result, after the loop. ```ctx``` is the
        running state (unused here).

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **result** (ChatResult): The final answer whose ```text``` is capped
            when longer than ```limit```

        """
        capped, cut = self._cap(result.text or '', GUARD_STAGE_ON_CLOSE)
        if cut:
            result.text = capped
