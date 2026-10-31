"""
Tests for tokeo.ext.vault: the vault config handler that loads
```!vault:<profile>``` values of a yaml config as VaultRef and decrypts them on
read, its registry of vault handlers, the profiles and their validation,
environment overrides of secrets and the hook for handlers from outside.
"""

from configparser import RawConfigParser

import pytest
import yaml
from cryptography.fernet import Fernet

from tokeo.ext.yaml import TokeoYamlConfigError
from tokeo.ext.vault import TokeoVaultConfigHandler
from tokeo.core.vaults import Vault, VaultRef, TokeoVaultError
from tokeo.core.vaults.enc import EncVault
from tokeo.core.vaults.scrypt import ScryptVault
from tests.ext.vault.vault_tests_helper import (
    MASTER_ENV,
    VaultTest,
    isolate_yaml_tags,
    set_secrets,
    write_config,
    fernet_token,
    scrypt_token,
)


@pytest.fixture(autouse=True)
def yaml_tags(monkeypatch):
    """The !vault tag on yaml.FullLoader for the test only."""
    isolate_yaml_tags(monkeypatch)


@pytest.fixture
def key(monkeypatch):
    """The master key, set with the scrypt passphrase in the environment."""
    return set_secrets(monkeypatch)


class _UpperVault(Vault):
    # a handler of its own: "encrypts" by upper case, "decrypts" by lower case
    def encrypt(self, profile, plaintext):
        return plaintext.upper()

    def decrypt(self, profile, ciphertext):
        return ciphertext.lower()


def _register_upper(app):
    # hook function registering _UpperVault as type upper from outside
    app.config.register_handler('upper', _UpperVault())


# load


def test_load_makes_the_vault_handler_the_config_handler():
    """With the extension loaded app.config is the vault config handler."""
    with VaultTest() as app:
        assert isinstance(app.config, TokeoVaultConfigHandler)
        assert app._meta.config_handler == 'tokeo.vault'


def test_load_registers_the_vault_tag_on_the_full_loader():
    """After loading, yaml.full_load turns !vault:master x into a VaultRef."""
    with VaultTest():
        ref = yaml.full_load('a: !vault:master gAAA')['a']
    assert isinstance(ref, VaultRef)
    assert (ref.profile, ref.ciphertext) == ('master', 'gAAA')


def test_load_of_a_config_file_keeps_the_secret_a_vaultref(key, tmp_path):
    """A !vault value of a config file is stored as VaultRef, never as plaintext."""
    path = write_config(tmp_path, f'db:\n  password: !vault:master {fernet_token(key, "s3cret")}\n')
    with VaultTest(path) as app:
        stored = RawConfigParser.get(app.config, 'db', 'password')
        assert isinstance(stored, VaultRef)
        assert stored.profile == 'master'


def test_load_does_not_need_the_key(tmp_path, monkeypatch):
    """The app sets up with a !vault value even when the key is not set."""
    monkeypatch.delenv(MASTER_ENV, raising=False)
    path = write_config(tmp_path, 'db:\n  password: !vault:master gAAA\n')
    with VaultTest(path) as app:
        assert isinstance(RawConfigParser.get(app.config, 'db', 'password'), VaultRef)


# register_handler and get_handler


def test_get_handler_gives_the_built_in_handlers():
    """enc and scrypt are registered with their handler classes."""
    with VaultTest() as app:
        assert type(app.config.get_handler('enc')) is EncVault
        assert type(app.config.get_handler('scrypt')) is ScryptVault


def test_get_handler_of_an_unknown_type_fails():
    """An unregistered type raises TokeoVaultError naming the type."""
    with VaultTest() as app:
        with pytest.raises(TokeoVaultError, match="no vault handler registered for type 'nope'"):
            app.config.get_handler('nope')


def test_register_handler_adds_and_replaces_a_handler():
    """A registered handler is given for its type, also over a built-in."""
    with VaultTest() as app:
        upper, other = _UpperVault(), _UpperVault()
        app.config.register_handler('upper', upper)
        app.config.register_handler('enc', other)
        assert app.config.get_handler('upper') is upper
        assert app.config.get_handler('enc') is other


def test_register_handler_is_per_app():
    """A handler registered on one app is unknown to the next app."""
    with VaultTest() as app:
        app.config.register_handler('upper', _UpperVault())
    with VaultTest() as app:
        with pytest.raises(TokeoVaultError):
            app.config.get_handler('upper')


# tokeo_vault_register_handlers


def test_hook_registers_a_handler_from_outside(tmp_path):
    """A handler registered by the hook decrypts the values of its profiles."""
    path = write_config(tmp_path, 'vault:\n  profiles:\n    loud:\n      type: upper\ndb:\n  password: !vault:loud S3CRET\n')
    with VaultTest(path, hooks=[('tokeo_vault_register_handlers', _register_upper)]) as app:
        assert isinstance(app.config.get_handler('upper'), _UpperVault)
        assert app.config.get('db', 'password') == 's3cret'


# get_profile


def test_get_profile_gives_the_profile_fields(tmp_path):
    """get_profile gives the fields of vault.profiles.<name>."""
    with VaultTest(write_config(tmp_path)) as app:
        assert app.config.get_profile('master') == {'type': 'enc', 'env': MASTER_ENV}


def test_get_profile_of_an_unknown_name_fails(tmp_path):
    """A name missing in vault.profiles raises TokeoVaultError naming it."""
    with VaultTest(write_config(tmp_path)) as app:
        with pytest.raises(TokeoVaultError, match="unknown vault profile 'nope'"):
            app.config.get_profile('nope')


@pytest.mark.parametrize(
    'text',
    ['', 'vault:\n  other: 1\n', 'vault:\n  profiles:\n', 'vault:\n  profiles: just a string\n'],
    ids=['no_vault_section', 'no_profiles', 'empty_profiles', 'profiles_string'],
)
def test_get_profile_without_usable_profiles_fails(tmp_path, text):
    """Without a vault.profiles mapping every profile is unknown."""
    path = tmp_path / 'app.yaml'
    path.write_text(text)
    with VaultTest(str(path)) as app:
        with pytest.raises(TokeoVaultError, match="unknown vault profile 'master'"):
            app.config.get_profile('master')


@pytest.mark.parametrize(
    'profile',
    ['enc', '[enc]', '', '{env: SOME_ENV}'],
    ids=['string', 'list', 'empty', 'no_type'],
)
def test_get_profile_without_a_type_fails(tmp_path, profile):
    """A profile that is no mapping or has no type raises TokeoVaultError."""
    path = tmp_path / 'app.yaml'
    path.write_text(f'vault:\n  profiles:\n    bad: {profile}\n')
    with VaultTest(str(path)) as app:
        with pytest.raises(TokeoVaultError, match="vault profile 'bad' is missing a type"):
            app.config.get_profile('bad')


def test_get_profile_stays_readable_beside_a_secret_in_another_profile(key, tmp_path):
    """master stays readable while another profile holds a !vault value."""
    path = tmp_path / 'app.yaml'
    path.write_text(
        f'vault:\n  profiles:\n    master:\n      type: enc\n      env: {MASTER_ENV}\n'
        f'    other:\n      type: enc\n      env: !vault:master {fernet_token(key, MASTER_ENV)}\n'
    )
    with VaultTest(str(path)) as app:
        assert app.config.get_profile('master') == {'type': 'enc', 'env': MASTER_ENV}


def test_get_profile_with_a_secret_in_it_fails(key, tmp_path):
    """A profile that holds a !vault value raises TokeoVaultError naming it."""
    path = tmp_path / 'app.yaml'
    path.write_text(
        f'vault:\n  profiles:\n    master:\n      type: enc\n      env: {MASTER_ENV}\n'
        f'    other:\n      type: enc\n      env: !vault:master {fernet_token(key, MASTER_ENV)}\n'
    )
    with VaultTest(str(path)) as app:
        with pytest.raises(TokeoVaultError, match="vault profile 'other' must not hold a !vault value"):
            app.config.get_profile('other')


def test_get_profile_takes_env_overrides_of_its_fields(tmp_path, monkeypatch):
    """An env override of a profile field applies to get_profile."""
    monkeypatch.setenv('TOKEO_TEST_VAULT_PROFILES_MASTER_ENV', 'OTHER_ENV')
    with VaultTest(write_config(tmp_path)) as app:
        assert app.config.get_profile('master') == {'type': 'enc', 'env': 'OTHER_ENV'}


# resolve


@pytest.mark.parametrize('value', ['plain', 42, None, ['a'], {'k': 'v'}], ids=['str', 'int', 'none', 'list', 'dict'])
def test_resolve_passes_a_plain_value_through(value):
    """A value that is no VaultRef comes back as the very same object."""
    with VaultTest() as app:
        assert app.config.resolve(value) is value


def test_resolve_decrypts_a_vaultref(key, tmp_path):
    """A VaultRef of the master profile resolves to its plaintext."""
    with VaultTest(write_config(tmp_path)) as app:
        assert app.config.resolve(VaultRef('master', fernet_token(key, 's3cret'))) == 's3cret'


def test_resolve_decrypts_a_vaultref_of_a_scrypt_profile(key, tmp_path):
    """A VaultRef of the scrypt profile resolves with the derived key."""
    with VaultTest(write_config(tmp_path)) as app:
        assert app.config.resolve(VaultRef('human', scrypt_token('s3cret'))) == 's3cret'


def test_resolve_with_an_unknown_profile_fails(key, tmp_path):
    """A VaultRef of a missing profile raises TokeoVaultError."""
    with VaultTest(write_config(tmp_path)) as app:
        with pytest.raises(TokeoVaultError, match="unknown vault profile 'nope'"):
            app.config.resolve(VaultRef('nope', fernet_token(key, 's3cret')))


def test_resolve_with_an_unregistered_type_fails(key, tmp_path):
    """A profile whose type has no handler raises TokeoVaultError."""
    path = tmp_path / 'app.yaml'
    path.write_text('vault:\n  profiles:\n    odd:\n      type: rot13\n')
    with VaultTest(str(path)) as app:
        with pytest.raises(TokeoVaultError, match="no vault handler registered for type 'rot13'"):
            app.config.resolve(VaultRef('odd', 'x'))


def test_resolve_with_a_wrong_key_fails(key, tmp_path):
    """A payload encrypted with another key raises TokeoVaultError."""
    other = Fernet.generate_key().decode()
    with VaultTest(write_config(tmp_path)) as app:
        with pytest.raises(TokeoVaultError, match='could not be decrypted'):
            app.config.resolve(VaultRef('master', fernet_token(other, 's3cret')))


# get


def test_get_decrypts_a_secret(key, tmp_path):
    """A !vault value of section.key is read as its plaintext."""
    path = write_config(tmp_path, f'db:\n  password: !vault:master {fernet_token(key, "s3cret")}\n')
    with VaultTest(path) as app:
        assert app.config.get('db', 'password') == 's3cret'


def test_get_decrypts_secrets_at_any_depth(key, tmp_path):
    """Secrets nested in mappings and lists read as plaintext."""
    token = fernet_token(key, 'deep')
    path = write_config(
        tmp_path, f'db:\n  auth:\n    user: admin\n    password: !vault:master {token}\n    list: [1, !vault:human {scrypt_token("pw")}]\n'
    )
    with VaultTest(path) as app:
        assert app.config.get('db', 'auth') == {'user': 'admin', 'password': 'deep', 'list': [1, 'pw']}


def test_get_keeps_the_stored_secret_encrypted(key, tmp_path):
    """After a read the stored config still holds the VaultRef."""
    path = write_config(tmp_path, f'db:\n  auth:\n    password: !vault:master {fernet_token(key, "s3cret")}\n')
    with VaultTest(path) as app:
        assert app.config.get('db', 'auth') == {'password': 's3cret'}
        assert isinstance(RawConfigParser.get(app.config, 'db', 'auth')['password'], VaultRef)


def test_get_without_secrets_returns_the_stored_object(key, tmp_path):
    """A mapping without secrets is read as the very same stored object."""
    path = write_config(tmp_path, 'db:\n  auth:\n    user: admin\n')
    with VaultTest(path) as app:
        assert app.config.get('db', 'auth') is RawConfigParser.get(app.config, 'db', 'auth')


def test_get_section_dict_decrypts_the_secrets(key, tmp_path):
    """get_section_dict gives the secrets of a section as plaintext."""
    path = write_config(tmp_path, f'db:\n  user: admin\n  password: !vault:master {fernet_token(key, "s3cret")}\n')
    with VaultTest(path) as app:
        assert app.config.get_section_dict('db') == {'user': 'admin', 'password': 's3cret'}


def test_get_decrypts_a_vaultref_merged_by_code(key):
    """A VaultRef merged into the config by code is decrypted on read as well."""
    with VaultTest() as app:
        app.config.merge({'vault': {'profiles': {'master': {'type': 'enc', 'env': MASTER_ENV}}}})
        app.config.merge({'db': {'password': VaultRef('master', fernet_token(key, 's3cret'))}})
        assert app.config.get('db', 'password') == 's3cret'


def test_get_without_the_key_fails_on_read(tmp_path, monkeypatch):
    """A secret whose key is not set raises TokeoVaultError when it is read."""
    monkeypatch.delenv(MASTER_ENV, raising=False)
    path = write_config(tmp_path, 'db:\n  password: !vault:master gAAA\n')
    with VaultTest(path) as app:
        with pytest.raises(TokeoVaultError, match=f"'{MASTER_ENV}' is not set"):
            app.config.get('db', 'password')


def test_get_of_a_tampered_secret_fails(key, tmp_path):
    """A secret changed in the file raises TokeoVaultError on read."""
    token = fernet_token(key, 's3cret')
    tampered = token[:40] + ('A' if token[40] != 'A' else 'B') + token[41:]
    path = write_config(tmp_path, f'db:\n  password: !vault:master {tampered}\n')
    with VaultTest(path) as app:
        with pytest.raises(TokeoVaultError, match='could not be decrypted'):
            app.config.get('db', 'password')


# environment overrides


def test_env_override_replaces_a_secret_with_its_plain_value(key, tmp_path, monkeypatch):
    """TOKEO_TEST_DB_PASSWORD=plain wins over the !vault value of db.password."""
    path = write_config(tmp_path, f'db:\n  password: !vault:master {fernet_token(key, "s3cret")}\n')
    with VaultTest(path) as app:
        monkeypatch.setenv('TOKEO_TEST_DB_PASSWORD', 'plain')
        assert app.config.get('db', 'password') == 'plain'


def test_env_override_holding_a_token_is_not_decrypted(key, tmp_path, monkeypatch):
    """A Fernet token given as env override is read as the token itself."""
    path = write_config(tmp_path, f'db:\n  password: !vault:master {fernet_token(key, "s3cret")}\n')
    token = fernet_token(key, 'other')
    with VaultTest(path) as app:
        monkeypatch.setenv('TOKEO_TEST_DB_PASSWORD', token)
        assert app.config.get('db', 'password') == token


def test_env_override_of_a_nested_secret_is_plain(key, tmp_path, monkeypatch):
    """TOKEO_TEST_DB_AUTH_PASSWORD replaces one nested secret, not its sibling."""
    path = write_config(
        tmp_path,
        f'db:\n  auth:\n    password: !vault:master {fernet_token(key, "s3cret")}\n    token: !vault:master {fernet_token(key, "t0ken")}\n',
    )
    with VaultTest(path) as app:
        monkeypatch.setenv('TOKEO_TEST_DB_AUTH_PASSWORD', 'plain')
        assert app.config.get('db', 'auth') == {'password': 'plain', 'token': 't0ken'}


def test_env_override_with_a_vault_tag_fails(key, tmp_path, monkeypatch):
    """An env override holding a !vault value raises TokeoYamlConfigError."""
    path = write_config(tmp_path, f'db:\n  password: !vault:master {fernet_token(key, "s3cret")}\n')
    with VaultTest(path) as app:
        monkeypatch.setenv('TOKEO_TEST_DB_PASSWORD', f'!vault:master {fernet_token(key, "other")}')
        with pytest.raises(TokeoYamlConfigError, match='TOKEO_TEST_DB_PASSWORD'):
            app.config.get('db', 'password')
