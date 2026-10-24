"""
Shared parts of the tests of tokeo.ext.tui: the test app with the input of
the shell.
"""

from tokeo.main import TokeoTest


class TuiInputTest(TokeoTest):
    """A test app with the extension tokeo.ext.tui.input."""

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.tui.input',
        ]
