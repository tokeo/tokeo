"""
Tests for tokeo.ext.print.
"""

import io
import sys

from tokeo.main import TokeoTest


def test_print_and_inspect_write_to_the_current_stdout(monkeypatch):
    # a stdout replaced later, e.g. by patch_stdout of prompt_toolkit, gets it
    with TokeoTest() as app:
        stdout = io.StringIO()
        monkeypatch.setattr(sys, 'stdout', stdout)
        app.print('hello', 'world')
        app.inspect({'answer': 42})
    assert 'hello world\n' in stdout.getvalue()
    assert 'answer' in stdout.getvalue() and '42' in stdout.getvalue()
