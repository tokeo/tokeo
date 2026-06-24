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

import math
from datetime import date as date_type

from tokeo.core.ai import TokeoAiTool
from tokeo.core.ai.tool import create_tool_result
from tokeo.core.utils.date import to_utc


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
_KNOWN_NEW_MOON = date_type(2000, 1, 6)
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

        The value carries the phase plus the figures it is derived from, for a
        ui or a follow-up step; the model sees only the phase name as the
        as_str. All figures use the mean synodic month, so they are an
        approximation, not an ephemeris.

        ### Args

        - **date** (str): The date as ```YYYY-MM-DD``` or a timestring

        ### Returns

        - **ToolResult**: A dict value with the phase name, its index (0-7),
            the moon's age in days, the cycle fraction and lit fraction (both
            0-1), and whether it is waxing; as_str is the phase name

        """
        d = to_utc(date, auto_type=False)
        # age is the days since the last new moon: total days since a known new
        # moon, with whole cycles dropped, so only the position in the current
        # cycle remains
        age = (d.date() - _KNOWN_NEW_MOON).days % _SYNODIC_DAYS
        fraction = age / _SYNODIC_DAYS
        index = int(fraction * 8 + 0.5) % 8
        # lit fraction of the disc from the phase geometry: 0 at new, 1 at full
        illumination = (1 - math.cos(2 * math.pi * fraction)) / 2
        result = dict(
            phase=_PHASES[index],
            index=index,
            age_days=round(age, 4),
            fraction=round(fraction, 4),
            illumination=round(illumination, 4),
            waxing=fraction < 0.5,
        )
        # the model sees the phase name; the dict rides along for the trace or
        # a follow-up step
        return create_tool_result(result, as_str=result['phase'])
