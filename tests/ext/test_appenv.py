"""
Tests for tokeo.ext.appenv: the environment variable of the app names a known
environment or none, the general APP_ENV counts while the variable of the app
is not set, an unknown name raises, and the app reads only the config files of
its config dir, none of the places cement reads by itself. The local config of
the machine is read last, only from an absolute folder outside of the config
dir with private folders and files.
"""

import os
import types

import pytest

from tokeo.main import Tokeo
from tokeo.core.exc import TokeoError
from tokeo.ext.appenv import TokeoAppEnv


def _make_app(**meta):
    # a fake app with its meta and the validate_config cement calls
    return types.SimpleNamespace(_meta=types.SimpleNamespace(**meta), validate_config=lambda: None)


def _make_local(tmp_path, *names):
    # a private local config folder in tmp_path/local, folders 700, files 600
    local = tmp_path / 'local'
    for name in names:
        (local / name).parent.mkdir(parents=True, exist_ok=True)
        (local / name).write_text('')
        (local / name).chmod(0o600)
    for d in [local, *[d for d in local.rglob('*') if d.is_dir()]]:
        d.chmod(0o700)
    return local


def _make_appenv(tmp_path):
    # an appenv of the app shop in tmp_path, without an environment
    return TokeoAppEnv(_make_app(label='shop', main_dir=str(tmp_path / 'shop'), config_file_suffix='.yaml'))


@pytest.fixture(autouse=True)
def no_app_env(monkeypatch):
    # the general APP_ENV of the shell changes no test that does not set it
    monkeypatch.delenv('APP_ENV', raising=False)


class AppenvApp(Tokeo):
    """
    The app of tokeo itself, not its test app: the TestApp of cement clears the
    config places of cement already, so only a real app shows what appenv does.
    """

    class Meta:
        exit_on_close = False


@pytest.mark.parametrize('value', ['prd', 'live', 'Development2'])
def test_unknown_environment_raises(monkeypatch, value):
    """An env value that names no environment raises, naming variable and value."""
    monkeypatch.setenv('SHOP_ENV', value)
    app = _make_app(label='shop', main_dir='/tmp')
    with pytest.raises(TokeoError, match=f"^SHOP_ENV='{value.lower()}' is no environment, use dev, development, prod, production, "):
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
    """Each accepted value of the variable selects its environment."""
    monkeypatch.setenv('SHOP_ENV', value)
    app = _make_app(label='shop', main_dir='/tmp', config_file_suffix='.yaml')
    assert TokeoAppEnv(app).APP_ENV == env


@pytest.mark.parametrize('value', [None, 'production', 'prd'])
def test_test_app_runs_in_testing(monkeypatch, value):
    """A "<app>_test" app runs in testing, whatever any of its variables is."""
    for name in ['SHOP_ENV', 'SHOP_TEST_ENV', 'APP_ENV']:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    app = _make_app(label='shop_test', main_dir='/tmp', config_file_suffix='.yaml')
    appenv = TokeoAppEnv(app)
    assert (appenv.APP_ENV, appenv.APP_ENV_VAR_VALUE, app._meta.label) == ('testing', 'test', 'shop')


@pytest.mark.parametrize(
    'shop_env, app_env, var_name, env',
    [
        (None, 'production', 'APP_ENV', 'production'),
        ('dev', 'production', 'SHOP_ENV', 'development'),
        ('', 'production', 'SHOP_ENV', None),
    ],
    ids=['fallback', 'app_wins', 'empty_app_wins'],
)
def test_app_env_is_the_fallback_of_the_variable_of_the_app(monkeypatch, shop_env, app_env, var_name, env):
    """APP_ENV=production counts only while SHOP_ENV is unset, an empty one wins."""
    if shop_env is None:
        monkeypatch.delenv('SHOP_ENV', raising=False)
    else:
        monkeypatch.setenv('SHOP_ENV', shop_env)
    monkeypatch.setenv('APP_ENV', app_env)
    appenv = TokeoAppEnv(_make_app(label='shop', main_dir='/tmp', config_file_suffix='.yaml'))
    assert (appenv.APP_ENV_VAR_NAME, appenv.APP_ENV) == (var_name, env)


def test_unknown_app_env_names_app_env(monkeypatch):
    """APP_ENV=prd without SHOP_ENV raises and names APP_ENV."""
    monkeypatch.delenv('SHOP_ENV', raising=False)
    monkeypatch.setenv('APP_ENV', 'prd')
    with pytest.raises(TokeoError, match="^APP_ENV='prd' is no environment, use dev, development, "):
        TokeoAppEnv(_make_app(label='shop', main_dir='/tmp'))


def test_no_environment_reads_base_only(monkeypatch, tmp_path):
    """Without an environment only the base configs are read."""
    monkeypatch.delenv('SHOP_ENV', raising=False)
    config = tmp_path / 'config' / 'shop'
    config.mkdir(parents=True)
    (config / 'base.yaml').write_text('')
    (config / 'production.yaml').write_text('')
    app = _make_app(label='shop', main_dir=str(tmp_path / 'shop'), config_file_suffix='.yaml')
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
    app = _make_app(label='shop', main_dir=str(tmp_path / 'shop'), config_file_suffix='.yaml')
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


def test_app_reads_only_its_config_dir(monkeypatch):
    """No /etc/tokeo or ~/.tokeo.yaml of cement, only config/tokeo/base.yaml."""
    monkeypatch.delenv('TOKEO_ENV', raising=False)
    with AppenvApp(argv=[]) as app:
        assert app._meta.config_files == [f'{app.env.APP_CONFIG_DIR}/base.yaml']
        assert app._meta.config_dirs == []


def test_local_config_files_are_only_the_local_ones_of_the_environment(tmp_path):
    """testing.local.yaml first, then the .local ones right in testing.d only."""
    local = _make_local(
        tmp_path,
        'testing.local.yaml',
        'testing.d/10-b.local.yaml',
        'testing.d/2-a.local.yaml',
        'testing.d/sub/3-c.local.yaml',
        'testing.d/c.yaml',
        'testing.yaml',
    )
    appenv = _make_appenv(tmp_path)
    assert appenv.get_local_config_files('testing', '.yaml', str(local)) == [
        str(local / name) for name in ['testing.local.yaml', 'testing.d/2-a.local.yaml', 'testing.d/10-b.local.yaml']
    ]
    assert appenv.get_local_config_files('testing', '.yaml', None) == []
    assert appenv.get_local_config_files('base', '.yaml', str(local)) == []


@pytest.mark.parametrize(
    'folder, expected',
    [
        ('config/local', "must be an absolute path, not 'config/local'"),
        ('~/local', "must be an absolute path, not '~/local'"),
        (7, 'must be an absolute path, not 7'),
        ('{tmp}/config/shop', "must lie outside of the config dir of the app, not '{tmp}/config/shop'"),
        ('{tmp}/config/shop/../shop/', "must lie outside of the config dir of the app, not '{tmp}/config/shop/../shop/'"),
        ('{tmp}/config/shop/xyz', "must lie outside of the config dir of the app, not '{tmp}/config/shop/xyz'"),
        ('{tmp}/config/shop/abc/def/xyz', "must lie outside of the config dir of the app, not '{tmp}/config/shop/abc/def/xyz'"),
        ('{tmp}/new', "must be a folder, not '{tmp}/new'"),
        ('{tmp}/config/shopx', "must be a folder, not '{tmp}/config/shopx'"),
    ],
    ids=['relative', 'home', 'number', 'config_dir', 'config_dir_spelled', 'in', 'deep_in', 'apart', 'apart_prefix'],
)
def test_local_config_dir_must_be_absolute_and_outside_of_the_config_dir(tmp_path, folder, expected):
    """A relative folder or the config dir or one in it raises, not one apart."""
    appenv = _make_appenv(tmp_path)
    if isinstance(folder, str):
        folder, expected = folder.format(tmp=tmp_path), expected.format(tmp=tmp_path)
    with pytest.raises(TokeoError) as err_info:
        appenv.get_local_config_files('testing', '.yaml', folder)
    assert str(err_info.value) == f'shop.local_config_dir {expected}'


@pytest.mark.parametrize(
    'path, mode',
    [('', 0o755), ('', 0o750), ('testing.d', 0o705), ('testing.local.yaml', 0o644), ('testing.d/1-a.local.yaml', 0o640)],
    ids=['dir_others', 'dir_group', 'env_dir_others', 'file_others', 'file_group'],
)
def test_local_config_must_give_no_rights_to_group_and_others(tmp_path, path, mode):
    """Like ssh, a folder or a file with rights for group or others raises."""
    local = _make_local(tmp_path, 'testing.local.yaml', 'testing.d/1-a.local.yaml')
    (local / path).chmod(mode)
    with pytest.raises(TokeoError) as err_info:
        _make_appenv(tmp_path).get_local_config_files('testing', '.yaml', str(local))
    assert str(err_info.value) == f"shop.local_config_dir must give no rights to group and others, not mode {mode:03o} of '{local / path}'"


def test_local_config_dir_above_the_config_dir_reads_only_its_own_files(tmp_path):
    """A folder above the config dir reads its own files, none of the config dir."""
    local = _make_local(tmp_path, 'testing.local.yaml', 'config/shop/testing.local.yaml', 'config/shop/testing.d/a.local.yaml')
    appenv = _make_appenv(tmp_path / 'local')
    assert appenv.APP_CONFIG_DIR == str(local / 'config' / 'shop')
    assert appenv.get_local_config_files('testing', '.yaml', str(local)) == [str(local / 'testing.local.yaml')]


def test_local_config_without_a_config_of_the_environment_raises(tmp_path):
    """A set folder with the config of production only raises in testing."""
    local = _make_local(tmp_path, 'production.local.yaml', 'testing.d/c.yaml')
    with pytest.raises(TokeoError) as err_info:
        _make_appenv(tmp_path).get_local_config_files('testing', '.yaml', str(local))
    assert str(err_info.value) == f"shop.local_config_dir must hold testing.local.yaml or testing.d/*.local.yaml, not '{local}'"


def test_local_config_must_not_link_into_the_config_dir(tmp_path):
    """A file of the folder that links into the config dir of the app raises."""
    local = _make_local(tmp_path, 'testing.local.yaml')
    appenv = _make_appenv(tmp_path)
    os.makedirs(appenv.APP_CONFIG_DIR)
    secret = os.path.join(appenv.APP_CONFIG_DIR, 'testing.local.yaml')
    open(secret, 'w').close()
    (local / 'testing.local.yaml').unlink()
    (local / 'testing.local.yaml').symlink_to(secret)
    with pytest.raises(TokeoError) as err_info:
        appenv.get_local_config_files('testing', '.yaml', str(local))
    link = local / 'testing.local.yaml'
    assert str(err_info.value) == f"shop.local_config_dir must not link into the config dir of the app, not '{link}'"


@pytest.mark.skipif(os.geteuid() == 0, reason='root reads any file')
def test_local_config_the_app_cannot_read_raises(tmp_path):
    """A file without the right to read for the app raises."""
    local = _make_local(tmp_path, 'testing.local.yaml')
    (local / 'testing.local.yaml').chmod(0o200)
    with pytest.raises(TokeoError) as err_info:
        _make_appenv(tmp_path).get_local_config_files('testing', '.yaml', str(local))
    assert str(err_info.value) == f"shop.local_config_dir must be readable by the app, not '{local / 'testing.local.yaml'}'"


@pytest.mark.parametrize('env, expected', [('test', '/from/the/machine'), (None, None)], ids=['testing', 'no_environment'])
def test_app_reads_the_local_config_dir_last(monkeypatch, tmp_path, env, expected):
    """The folder of the variable is read last in testing, not without an env."""
    local = _make_local(tmp_path, 'testing.local.yaml')
    (local / 'testing.local.yaml').write_text('tokeo:\n  crash_dir: /from/the/machine\n')
    monkeypatch.setenv('TOKEO_LOCAL_CONFIG_DIR', str(local))
    if env is None:
        monkeypatch.delenv('TOKEO_ENV', raising=False)
    else:
        monkeypatch.setenv('TOKEO_ENV', env)
    with AppenvApp(argv=[]) as app:
        assert app.config.get('tokeo', 'crash_dir') == expected
        assert (app._meta.config_files[-1] == str(local / 'testing.local.yaml')) is (env is not None)
