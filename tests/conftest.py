"""
The fixtures for all tests of the app.
"""

import os
import pytest
from io import StringIO
from cement.utils.misc import rando as _rando
from cement.utils import fs
from tests.utils import ROOT


def pytest_configure(config):
    # the temps of the tests stay in tmp/tests of the project after the run
    if config.option.basetemp is None:
        os.makedirs(os.path.join(ROOT, "tmp"), exist_ok=True)
        config.option.basetemp = os.path.join(ROOT, "tmp", "tests")


@pytest.fixture(scope="function")
def tmp(tmp_path):
    # inside tmp_path, pytest keeps it until the next run
    yield fs.Tmp(dir=str(tmp_path))


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
