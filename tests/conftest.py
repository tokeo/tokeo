"""
The fixtures for all tests of the app.
"""

import pytest
from io import StringIO
from cement.utils.misc import rando as _rando
from cement.utils import fs


@pytest.fixture(scope="function")
def tmp(request):
    t = fs.Tmp()
    yield t

    # cleanup of the directory and the file
    t.remove()


@pytest.fixture(scope="function")
def key(request):
    yield _rando()


@pytest.fixture(scope="function")
def rando(request):
    yield _rando()[:12]


@pytest.fixture
def disable_stdin_capture(monkeypatch):
    # -- NEEDED-FOR: fabric.connection.Connection.run() in a test-function.
    monkeypatch.setattr("sys.stdin", StringIO(""))
    return monkeypatch
