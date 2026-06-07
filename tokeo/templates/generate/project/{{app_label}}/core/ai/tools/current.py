"""
Current tool for the {{ app_name }} ai agent.

A tiny information tool that returns the current date and time. It takes no
arguments, so it also demonstrates the simplest possible tool schema, and its
output format is a tool setting (``Meta.format``), overridable per item by the
``options`` in the configuration.

This module is self-contained: it holds only the tool class. The project names
it by its full dotted class path under ``ai.tools`` in the config, so it needs
no registration and no entry in the app extensions; the handler imports and
instantiates it on demand.
"""

from datetime import datetime

from tokeo.core.ai import TokeoAiTool


class TokeoAiCurrentTool(TokeoAiTool):
    """
    Tool that returns the current date and time.

    The ``Meta`` description and parameters are what the model sees; the
    ``format`` is the tool's own setting, overridden per item by its config
    ``options`` and read from ``_meta``.

    """

    class Meta:
        """Tool meta-data sent to the model, plus the tool's own settings."""

        # Short description the model sees
        description = 'return the current date and time'

        # JSON-schema object describing the arguments (none needed)
        parameters = dict(
            type='object',
            properties=dict(),
        )

        # strftime format of the reply; a tool setting, not model-facing
        format = '%Y-%m-%d %H:%M:%S'

    def exec(self):
        """
        Return the current local date and time as text.

        ### Returns

        - **str**: The formatted current date and time

        """
        return datetime.now().strftime(self._meta.format)
