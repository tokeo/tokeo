"""
Current tool for the {{ app_name }} ai agent.

A tiny information tool that returns the current UTC date and time as a
timestring. It takes no arguments, so it also demonstrates the simplest
possible tool schema.

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ```ai.tools``` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

from tokeo.core.ai import TokeoAiTool
from tokeo.core.ai.tool import create_tool_result
from tokeo.core.utils.date import utc_now, to_utc_timestring


class TokeoAiCurrentTool(TokeoAiTool):
    """
    Tool that returns the current UTC date and time.

    The ```Meta``` description and parameters are what the model sees; ```exec```
    reads the clock and formats the reply as a UTC timestring.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = 'return the current date and time'

        # JSON-schema object describing the arguments (none needed)
        parameters = dict(
            type='object',
            properties=dict(),
        )

    def exec(self):
        """
        Return the current UTC date and time.

        ### Returns

        - **ToolResult**: The current datetime as the value; as_str is the UTC
            timestring ```YYYY-MM-DD HH:MM:SS.MMMZ```

        """
        now = utc_now()
        # the value is the datetime itself, so the trace keeps the full instant;
        # the model sees the UTC timestring as the as_str
        return create_tool_result(now, as_str=to_utc_timestring(now))
