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
    with pytest.raises(TokeoError, match=f"^SHOP_ENV='{value.lower()}' is no environment, use development, "):
        TokeoAppEnv(app)


@pytest.mark.parametrize(
    'value, env',
    [
        ('development', 'development'),
        ('production', 'production'),
        ('staging', 'staging'),
        ('testing', 'testing'),
        ('dev', 'development'),
        ('prod', 'production'),
        ('stage', 'staging'),
        ('test', 'testing'),
    ],
)
def test_long_and_short_names(monkeypatch, value, env):
    """The long name of an environment is its value, the short name works too."""
    monkeypatch.setenv('SHOP_ENV', value)
    app = types.SimpleNamespace(_meta=types.SimpleNamespace(label='shop', main_dir='/tmp', config_file_suffix='.yaml'))
    assert TokeoAppEnv(app).APP_ENV == env


def test_test_app_runs_in_testing(monkeypatch):
    """A "<app>_test" app runs in testing and names it by the long name."""
    monkeypatch.delenv('SHOP_ENV', raising=False)
    app = types.SimpleNamespace(_meta=types.SimpleNamespace(label='shop_test', main_dir='/tmp', config_file_suffix='.yaml'))
    appenv = TokeoAppEnv(app)
    assert (appenv.APP_ENV, appenv.APP_ENV_VAR_VALUE, app._meta.label) == ('testing', 'testing', 'shop')


def test_no_environment_reads_base_only(monkeypatch, tmp_path):
    """Without an environment only the base configs are read."""
    monkeypatch.delenv('SHOP_ENV', raising=False)
    config = tmp_path / 'config' / 'shop'
    config.mkdir(parents=True)
    (config / 'base.yaml').write_text('')
    (config / 'production.yaml').write_text('')
    app = types.SimpleNamespace(_meta=types.SimpleNamespace(label='shop', main_dir=str(tmp_path / 'shop'), config_file_suffix='.yaml'))
    assert TokeoAppEnv(app).APP_ENV is None
    assert app._meta.config_files == [str(config / 'base.yaml')]


def test_partial_and_local_configs(monkeypatch, tmp_path):
    """
    Partial configs follow their env config in natural order, .local ones come
    last and never for base.
    """
    monkeypatch.setenv('SHOP_ENV', 'production')
    config = tmp_path / 'config' / 'shop'
    for name in [
        'base.yaml',
        'base.local.yaml',
        'base.d/10-b.yaml',
        'base.d/2-a.yaml',
        'base.d/x.local.yaml',
        'production.yaml',
        'production.local.yaml',
        'production.d/1-a.yaml',
        'production.d/sub/2-b.local.yaml',
    ]:
        (config / name).parent.mkdir(parents=True, exist_ok=True)
        (config / name).write_text('')
    app = types.SimpleNamespace(_meta=types.SimpleNamespace(label='shop', main_dir=str(tmp_path / 'shop'), config_file_suffix='.yaml'))
    TokeoAppEnv(app)
    assert app._meta.config_files == [
        str(config / name)
        for name in [
            'base.yaml',
            'base.d/2-a.yaml',
            'base.d/10-b.yaml',
            'production.yaml',
            'production.d/1-a.yaml',
            'production.local.yaml',
            'production.d/sub/2-b.local.yaml',
        ]
    ]
