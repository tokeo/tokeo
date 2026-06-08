"""
Add-months tool for the {{ app_name }} ai agent.

Shifts a date by a number of months -- forward or, with a negative number,
backward. The day is clamped to the target month's length (january 31 plus
one month is february 28 or 29). Dates are strict ISO (``YYYY-MM-DD``);
understanding looser language is the model's job, not the tool's.

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ``ai.tools`` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

import calendar
from datetime import date as date_type

from tokeo.core.ai import TokeoAiTool


class TokeoAiAddMonthsTool(TokeoAiTool):
    """
    Tool that adds a number of months to an ISO date.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = 'add a number of months to a date'

        # JSON-schema object describing the arguments
        parameters = dict(
            type='object',
            properties=dict(
                date=dict(type='string', description='the date (YYYY-MM-DD)'),
                months=dict(type='integer', description='the months to add (negative goes back)'),
            ),
            required=['date', 'months'],
        )

    def exec(self, date, months):
        """
        Shift the date by the given months, clamping the day.

        ### Args

        - **date** (str): The date as ``YYYY-MM-DD``
        - **months** (int): The months to add; a negative number goes back

        ### Returns

        - **str**: The shifted date as ``YYYY-MM-DD``

        """
        value = date_type.fromisoformat(str(date))
        total = value.year * 12 + (value.month - 1) + int(months)
        year, month = divmod(total, 12)
        month += 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return date_type(year, month, day).isoformat()
