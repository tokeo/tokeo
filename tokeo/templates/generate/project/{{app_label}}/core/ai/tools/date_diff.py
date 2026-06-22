"""
Date-diff tool for the {{ app_name }} ai agent.

Counts the days between two dates -- the workhorse of the calendar toolset
the akili micro model drives. Dates are strict ISO (```YYYY-MM-DD```);
understanding looser language is the model's job, not the tool's.

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ```ai.tools``` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

from datetime import date

from tokeo.core.ai import TokeoAiTool


class TokeoAiDateDiffTool(TokeoAiTool):
    """
    Tool that counts the days between two ISO dates.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = 'count the days between two dates'

        # JSON-schema object describing the arguments
        parameters = dict(
            type='object',
            properties=dict(
                start=dict(type='string', description='the first date (YYYY-MM-DD)'),
                end=dict(type='string', description='the second date (YYYY-MM-DD)'),
            ),
            required=['start', 'end'],
        )

    def exec(self, start, end):
        """
        Count the days from start to end.

        ### Args

        - **start** (str): The first date as ```YYYY-MM-DD```
        - **end** (str): The second date as ```YYYY-MM-DD```

        ### Returns

        - **str**: The signed number of days

        """
        return str((date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days)
