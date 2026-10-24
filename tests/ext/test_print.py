"""
Tests for tokeo.ext.print: app.print and app.inspect write to the stdout of
the moment of the call.
"""

import io
import sys

from tokeo.main import TokeoTest


class PrintTest(TokeoTest):
    """A test app with the extension tokeo.ext.print."""

    class Meta:
        extensions = [
            'tokeo.ext.print',
        ]


def test_print_and_inspect_write_to_the_current_stdout(monkeypatch):
    """print and inspect write to sys.stdout as it is at the time of the call."""
    with PrintTest() as app:
        stdout = io.StringIO()
        monkeypatch.setattr(sys, 'stdout', stdout)
        app.print('hello', 'world')
        app.inspect({'answer': 42})
    assert 'hello world\n' in stdout.getvalue()
    assert 'answer' in stdout.getvalue() and '42' in stdout.getvalue()
