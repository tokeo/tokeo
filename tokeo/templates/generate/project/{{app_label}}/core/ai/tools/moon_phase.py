"""
Moon-phase tool for the {{ app_name }} ai agent.

Tells the moon phase of a date -- the one astronomy treat in the calendar
toolset, computed from pure arithmetic (days since a known new moon over the
mean synodic month), so it stays deterministic and needs no data source.
Dates are strict ISO (```YYYY-MM-DD```).

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ```ai.tools``` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

from datetime import date

from tokeo.core.ai import TokeoAiTool


# the eight common phase names, one per eighth of the synodic cycle
_PHASES = (
    'new moon',
    'waxing crescent',
    'first quarter',
    'waxing gibbous',
    'full moon',
    'waning gibbous',
    'last quarter',
    'waning crescent',
)

# a well-known new moon (2000-01-06) and the mean synodic month in days
_KNOWN_NEW_MOON = date(2000, 1, 6)
_SYNODIC_DAYS = 29.530588853


class TokeoAiMoonPhaseTool(TokeoAiTool):
    """
    Tool that tells the moon phase of an ISO date.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # Short description the model sees
        description = 'tell the moon phase of a date'

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
        Tell the moon phase of the date.

        ### Args

        - **date** (str): The date as ```YYYY-MM-DD```

        ### Returns

        - **str**: One of the eight common phase names

        """
        from datetime import date as date_type
        age = (date_type.fromisoformat(str(date)) - _KNOWN_NEW_MOON).days % _SYNODIC_DAYS
        index = int((age / _SYNODIC_DAYS) * 8 + 0.5) % 8
        return _PHASES[index]
