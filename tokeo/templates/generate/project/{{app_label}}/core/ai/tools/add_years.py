"""
Add-years tool for the {{ app_name }} ai agent.

Shifts a date by a number of years -- forward or, with a negative number,
backward. The day is clamped where needed (february 29 plus one year is
february 28). Dates are strict ISO (``YYYY-MM-DD``); understanding looser
language is the model's job, not the tool's.

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ``ai.tools`` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

import calendar
from datetime import date as date_type

from tokeo.core.ai import TokeoAiTool


class TokeoAiAddYearsTool(TokeoAiTool):
    """
    Tool that adds a number of years to an ISO date.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = 'add a number of years to a date'

        # JSON-schema object describing the arguments
        parameters = dict(
            type='object',
            properties=dict(
                date=dict(type='string', description='the date (YYYY-MM-DD)'),
                years=dict(type='integer', description='the years to add (negative goes back)'),
            ),
            required=['date', 'years'],
        )

    def exec(self, date, years):
        """
        Shift the date by the given years, clamping the day.

        ### Args

        - **date** (str): The date as ``YYYY-MM-DD``
        - **years** (int): The years to add; a negative number goes back

        ### Returns

        - **str**: The shifted date as ``YYYY-MM-DD``

        """
        value = date_type.fromisoformat(str(date))
        year = value.year + int(years)
        day = min(value.day, calendar.monthrange(year, value.month)[1])
        return date_type(year, value.month, day).isoformat()
