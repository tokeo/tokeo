"""
Add-days tool for the {{ app_name }} ai agent.

Shifts a date by a number of days -- forward or, with a negative number,
backward. Dates are strict ISO (```YYYY-MM-DD```); understanding looser
language is the model's job, not the tool's.

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ```ai.tools``` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

from datetime import timedelta

from tokeo.core.ai import TokeoAiTool


class TokeoAiAddDaysTool(TokeoAiTool):
    """
    Tool that adds a number of days to an ISO date.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = 'add a number of days to a date'

        # JSON-schema object describing the arguments
        parameters = dict(
            type='object',
            properties=dict(
                date=dict(type='string', description='the date (YYYY-MM-DD)'),
                days=dict(type='integer', description='the days to add (negative goes back)'),
            ),
            required=['date', 'days'],
        )

    def exec(self, date, days):
        """
        Shift the date by the given days.

        ### Args

        - **date** (str): The date as ```YYYY-MM-DD```
        - **days** (int): The days to add; a negative number goes back

        ### Returns

        - **str**: The shifted date as ```YYYY-MM-DD```

        """
        from datetime import date as date_type
        return (date_type.fromisoformat(str(date)) + timedelta(days=int(days))).isoformat()
