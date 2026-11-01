"""
Tests for tokeo.ext.appenv: the environment variable of the app names a known
environment or none, an unknown name raises.
"""

import types

import pytest

from tokeo.core.exc import TokeoError
from tokeo.ext.appenv import TokeoAppEnv


@pytest.mark.parametrize('value', ['prd', 'live', 'Development2'])
def test_unknown_environment_raises(monkeypatch, value):
    """An env value that names no environment raises, naming variable and value."""
    monkeypatch.setenv('SHOP_ENV', value)
    app = types.SimpleNamespace(_meta=types.SimpleNamespace(label='shop', main_dir='/tmp'))
    with pytest.raises(TokeoError, match=f"^SHOP_ENV='{value.lower()}' is no environment, use dev, "):
        TokeoAppEnv(app)
