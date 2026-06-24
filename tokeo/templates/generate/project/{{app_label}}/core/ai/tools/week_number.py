"""
Week-number tool for the {{ app_name }} ai agent.

Tells the ISO week number of a date. Dates are strict ISO (```YYYY-MM-DD```);
understanding looser language is the model's job, not the tool's.

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ```ai.tools``` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

from datetime import datetime as datetime_type

from tokeo.core.ai import TokeoAiTool
from tokeo.core.utils.date import to_utc


class TokeoAiWeekNumberTool(TokeoAiTool):
    """
    Tool that tells the ISO week number of an ISO date.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = 'tell the iso week number of a date'

        # JSON-schema object describing the arguments
        parameters = dict(
            type='object',
            properties=dict(
                date=dict(type='string', description='the date (YYYY-MM-DD)'),
            ),
            required=['date'],
        )

    def exec(self, date):
        """
        Tell the ISO week number of the date.

        ### Args

        - **date** (str): The date as ```YYYY-MM-DD``` or a timestring

        ### Returns

        - **int**: The ISO week number (1 ... 53)

        """
        d = to_utc(date, auto_type=True)
        # a timestring still has a single calendar day; reduce to it
        day = d.date() if isinstance(d, datetime_type) else d
        # return the number itself: its str() is exactly the model-facing form,
        # so let the loop wrap the plain value rather than restate as_str here
        return day.isocalendar().week
