"""
Tests for the vault controller of tokeo.ext.vault: the commands vault create,
vault encrypt and vault decrypt, what they print, how their output pastes
into a config and environment, and the errors of wrong arguments.
"""

import shlex

import pytest
import yaml
from cryptography.fernet import Fernet

from tokeo.core.vaults import Vault, TokeoVaultError
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


@pytest.fixture
def vault(tmp_path, capsys):
    """Runs a vault command on the given config text and gives what it printed."""

    def run(*args, text='', hooks=()):
        path = write_config(tmp_path, text)
        with VaultTest(path, argv=['vault', *args], hooks=list(hooks)) as app:
            app.run()
        return capsys.readouterr().out

    return run


class _PlainVault(Vault):
    # a handler of its own without a profile scaffold
    def encrypt(self, profile, plaintext):
        return plaintext

    def decrypt(self, profile, ciphertext):
        return ciphertext


def _register_plain(app):
    # hook function registering _PlainVault as type plain from outside
    app.config.register_handler('plain', _PlainVault())


def _split_scaffold(out):
    # split the output of vault create into its parsed yaml block and its
    # NAME=value environment lines as a dict
    block, _, env = out.partition('\n\n')
    return yaml.safe_load(block), dict(shlex.split(line)[0].split('=', 1) for line in env.splitlines())


# create


def test_create_enc_prints_a_profile_and_its_key(vault):
    """create --type enc example prints its profile and VAULT_EXAMPLE_KEY."""
    profile, env = _split_scaffold(vault('create', '--type', 'enc', 'example'))
    assert profile == {'vault': {'profiles': {'example': {'type': 'enc', 'env': 'VAULT_EXAMPLE_KEY'}}}}
    assert list(env) == ['VAULT_EXAMPLE_KEY']
    assert Fernet(env['VAULT_EXAMPLE_KEY'].encode()).decrypt(Fernet(env['VAULT_EXAMPLE_KEY'].encode()).encrypt(b'x')) == b'x'


def test_create_scrypt_prints_a_salt_and_a_quoted_passphrase(vault):
    """create --type scrypt prints a salt and a quoted passphrase placeholder."""
    out = vault('create', '--type', 'scrypt', 'example')
    profile, env = _split_scaffold(out)
    fields = profile['vault']['profiles']['example']
    assert (fields['type'], fields['env']) == ('scrypt', 'VAULT_EXAMPLE_PASSPHRASE')
    assert fields['salt']
    assert 'VAULT_EXAMPLE_PASSPHRASE="change me' in out
    assert env['VAULT_EXAMPLE_PASSPHRASE'].startswith('change me')


def test_create_output_pastes_into_a_working_profile(vault, tmp_path, monkeypatch, capsys):
    """The output of create, pasted into config and env, makes a working profile."""
    block, _, env = vault('create', '--type', 'enc', 'example').partition('\n\n')
    name, value = env.strip().split('=', 1)
    monkeypatch.setenv(name, value)
    # a config of the pasted profile only, without the profiles of the helper
    config = tmp_path / 'pasted.yaml'
    config.write_text(block)
    with VaultTest(str(config), argv=['vault', 'encrypt', '--profile', 'example', 's3cret']) as app:
        app.run()
    payload = capsys.readouterr().out.strip()
    config.write_text(f'{block}\ndb:\n  password: {payload}\n')
    with VaultTest(str(config)) as app:
        assert app.config.get('db', 'password') == 's3cret'


def test_create_of_an_unknown_type_fails(vault):
    """vault create --type nope raises TokeoVaultError naming the type."""
    with pytest.raises(TokeoVaultError, match="no vault handler registered for type 'nope'"):
        vault('create', '--type', 'nope', 'example')


def test_create_of_a_type_without_scaffold_fails(vault):
    """A registered type whose handler has no scaffold raises TokeoVaultError."""
    with pytest.raises(TokeoVaultError, match='cannot create a profile scaffold'):
        vault('create', '--type', 'plain', 'example', hooks=[('tokeo_vault_register_handlers', _register_plain)])


@pytest.mark.parametrize('args', [['create', 'example'], ['create', '--type', 'enc']], ids=['no_type', 'no_name'])
def test_create_needs_type_and_name(vault, args):
    """vault create without --type or without a name exits with an error."""
    with pytest.raises(SystemExit):
        vault(*args)


# encrypt


def test_encrypt_prints_a_vault_line_of_the_profile(vault, key):
    """encrypt --profile master s3cret prints !vault:master and its token."""
    tag, token = vault('encrypt', '--profile', 'master', 's3cret').split()
    assert tag == '!vault:master'
    assert Fernet(key.encode()).decrypt(token.encode()) == b's3cret'


def test_encrypt_prints_no_key_material(vault, key):
    """The output of vault encrypt holds neither the key nor the plaintext."""
    out = vault('encrypt', '--profile', 'master', 's3cret')
    assert key not in out
    assert 's3cret' not in out


def test_encrypt_with_a_scrypt_profile_decrypts_on_read(vault, key, tmp_path):
    """A value encrypted with the scrypt profile reads back as plaintext."""
    payload = vault('encrypt', '--profile', 'human', 's3cret').strip()
    with VaultTest(write_config(tmp_path, f'db:\n  password: {payload}\n')) as app:
        assert app.config.get('db', 'password') == 's3cret'


def test_encrypt_with_an_unknown_profile_fails(vault, key):
    """vault encrypt --profile nope raises TokeoVaultError naming the profile."""
    with pytest.raises(TokeoVaultError, match="unknown vault profile 'nope'"):
        vault('encrypt', '--profile', 'nope', 's3cret')


def test_encrypt_without_the_key_fails(vault, monkeypatch):
    """vault encrypt raises TokeoVaultError when the key variable is not set."""
    monkeypatch.delenv(MASTER_ENV, raising=False)
    with pytest.raises(TokeoVaultError, match=f"'{MASTER_ENV}' is not set"):
        vault('encrypt', '--profile', 'master', 's3cret')


@pytest.mark.parametrize('args', [['encrypt', 's3cret'], ['encrypt', '--profile', 'master']], ids=['no_profile', 'no_value'])
def test_encrypt_needs_profile_and_value(vault, key, args):
    """vault encrypt without --profile or without a value exits with an error."""
    with pytest.raises(SystemExit):
        vault(*args)


# decrypt with a payload


def test_decrypt_a_payload_with_a_profile(vault, key):
    """vault decrypt --profile master <token> prints the plaintext."""
    assert vault('decrypt', '--profile', 'master', fernet_token(key, 's3cret')) == 's3cret\n'


def test_decrypt_a_payload_with_a_scrypt_profile(vault, key):
    """decrypt --profile human <token> prints the plaintext."""
    assert vault('decrypt', '--profile', 'human', scrypt_token('s3cret')) == 's3cret\n'


def test_decrypt_a_payload_with_a_wrong_key_fails(vault, key):
    """A payload of another key raises TokeoVaultError."""
    with pytest.raises(TokeoVaultError, match='could not be decrypted'):
        vault('decrypt', '--profile', 'master', fernet_token(Fernet.generate_key().decode(), 's3cret'))


def test_decrypt_without_input_fails(vault, key):
    """vault decrypt without payload and without --config raises TokeoVaultError."""
    with pytest.raises(TokeoVaultError, match='provide an encrypted payload'):
        vault('decrypt')


def test_decrypt_a_payload_without_profile_fails(vault, key):
    """vault decrypt <token> without --profile raises TokeoVaultError."""
    with pytest.raises(TokeoVaultError, match='a --profile is required'):
        vault('decrypt', fernet_token(key, 's3cret'))


def test_decrypt_a_payload_with_an_unknown_profile_fails(vault, key):
    """decrypt --profile nope raises TokeoVaultError naming the profile."""
    with pytest.raises(TokeoVaultError, match="unknown vault profile 'nope'"):
        vault('decrypt', '--profile', 'nope', fernet_token(key, 's3cret'))


# decrypt with --config


def test_decrypt_config_reference(vault, key):
    """decrypt --config db.password prints the plaintext of the value."""
    text = f'db:\n  password: !vault:master {fernet_token(key, "s3cret")}\n'
    assert vault('decrypt', '--config', 'db.password', text=text) == 's3cret\n'


def test_decrypt_config_reference_at_depth(vault, key):
    """vault decrypt --config db.auth.password follows the nested mapping."""
    text = f'db:\n  auth:\n    password: !vault:human {scrypt_token("deep")}\n'
    assert vault('decrypt', '--config', 'db.auth.password', text=text) == 'deep\n'


def test_decrypt_config_reference_of_a_plain_value(vault, key):
    """A config reference to a plain value prints the value as is."""
    assert vault('decrypt', '--config', 'db.user', text='db:\n  user: admin\n') == 'admin\n'


def test_decrypt_config_reference_with_profile_fails(vault, key):
    """--config together with --profile raises TokeoVaultError."""
    with pytest.raises(TokeoVaultError, match='--profile is not used with --config'):
        vault('decrypt', '--config', 'db.password', '--profile', 'master')


def test_decrypt_config_reference_without_key_part_fails(vault, key):
    """A reference without section.key raises TokeoVaultError."""
    with pytest.raises(TokeoVaultError, match='at least section.key'):
        vault('decrypt', '--config', 'db')


@pytest.mark.parametrize('ref', ['nope.password', 'db.nope', 'db.user.deeper'], ids=['section', 'key', 'below_a_leaf'])
def test_decrypt_config_reference_not_found_fails(vault, key, ref):
    """A missing section, key or path raises TokeoVaultError naming it."""
    with pytest.raises(TokeoVaultError, match=f"config reference '{ref}' was not found"):
        vault('decrypt', '--config', ref, text='db:\n  user: admin\n')


def test_decrypt_config_reference_keeps_the_decryption_error(vault, key):
    """An undecryptable referenced secret reports the decryption error."""
    text = f'db:\n  password: !vault:master {fernet_token(Fernet.generate_key().decode(), "s3cret")}\n'
    with pytest.raises(TokeoVaultError, match='could not be decrypted'):
        vault('decrypt', '--config', 'db.password', text=text)
