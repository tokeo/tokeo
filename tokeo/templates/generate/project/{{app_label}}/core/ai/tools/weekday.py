"""
Weekday tool for the {{ app_name }} ai agent.

Names the weekday of a date. Dates are strict ISO (``YYYY-MM-DD``);
understanding looser language is the model's job, not the tool's.

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ``ai.tools`` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

import calendar

from tokeo.core.ai import TokeoAiTool


class TokeoAiWeekdayTool(TokeoAiTool):
    """
    Tool that names the weekday of an ISO date.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = 'name the weekday of a date'

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
        Name the weekday of the date.

        ### Args

        - **date** (str): The date as ``YYYY-MM-DD``

        ### Returns

        - **str**: The English weekday name (Monday ... Sunday)

        """
        from datetime import date as date_type
        return calendar.day_name[date_type.fromisoformat(str(date)).weekday()]
