"""
Tests for tokeo.core.vaults.scrypt: the Fernet key derived by scrypt from a
passphrase in an environment variable and the salt and cost of the profile,
its round trips, a wrong passphrase, missing or invalid profile values and
the profile scaffold.
"""

import base64
import hashlib

import pytest

from tokeo.core.vaults import TokeoVaultError
from tokeo.core.vaults.enc import EncVault
from tokeo.core.vaults.scrypt import ScryptVault

# the env variable the profiles of this file take their passphrase from
ENV = 'VAULT_TESTS_SCRYPT_PASSPHRASE'
SALT = base64.b64encode(b'0123456789abcdef').decode()
OTHER_SALT = base64.b64encode(b'fedcba9876543210').decode()
# a low cost keeps the derivations of the tests fast
PROFILE = {'type': 'scrypt', 'env': ENV, 'salt': SALT, 'cost': 16, 'block_size': 8, 'parallelization': 1}


@pytest.fixture
def passphrase(monkeypatch):
    """The passphrase "correct horse", set in ENV for the test."""
    monkeypatch.setenv(ENV, 'correct horse')
    return 'correct horse'


def _derive(passphrase, salt, n, r=8, p=1):
    # the expected Fernet key, derived by the scrypt of hashlib as a reference
    raw = hashlib.scrypt(passphrase.encode(), salt=base64.b64decode(salt), n=n, r=r, p=p, dklen=32)
    return base64.urlsafe_b64encode(raw)


# _key


def test_key_matches_the_scrypt_of_hashlib(passphrase):
    """The key is the url-safe base64 of hashlib.scrypt of passphrase and salt."""
    assert ScryptVault()._key(PROFILE) == _derive(passphrase, SALT, 16)


def test_key_is_deterministic_for_the_same_salt(passphrase):
    """Two derivations with the same passphrase and salt give the same key."""
    assert ScryptVault()._key(PROFILE) == ScryptVault()._key(dict(PROFILE))


def test_key_differs_for_another_salt(passphrase):
    """Another salt gives another key for the same passphrase."""
    assert ScryptVault()._key(PROFILE) != ScryptVault()._key(dict(PROFILE, salt=OTHER_SALT))


def test_key_differs_for_another_passphrase(passphrase, monkeypatch):
    """Another passphrase gives another key for the same salt."""
    first = ScryptVault()._key(PROFILE)
    monkeypatch.setenv(ENV, 'wrong horse')
    assert ScryptVault()._key(PROFILE) != first


@pytest.mark.parametrize('field, value', [('cost', 32), ('block_size', 4), ('parallelization', 2)])
def test_key_differs_for_another_cost_parameter(passphrase, field, value):
    """Changing cost, block_size or parallelization changes the key."""
    assert ScryptVault()._key(dict(PROFILE, **{field: value})) != ScryptVault()._key(PROFILE)


def test_key_takes_the_cost_parameters_as_strings(passphrase):
    """Cost parameters given as strings derive the same key as the ints."""
    profile = dict(PROFILE, cost='16', block_size='8', parallelization='1')
    assert ScryptVault()._key(profile) == ScryptVault()._key(PROFILE)


def test_key_without_cost_parameters_uses_the_defaults(passphrase):
    """Without cost parameters the key is derived with N=2**14, r=8, p=1."""
    profile = {'type': 'scrypt', 'env': ENV, 'salt': SALT}
    assert ScryptVault()._key(profile) == _derive(passphrase, SALT, 2**14)


# encrypt and decrypt


@pytest.mark.parametrize('plaintext', ['s3cret', '', 'Umlaute äöüß €'], ids=['ascii', 'empty', 'unicode'])
def test_encrypt_decrypt_round_trip(passphrase, plaintext):
    """A value encrypted with a passphrase decrypts to the same value."""
    vault = ScryptVault()
    assert vault.decrypt(PROFILE, vault.encrypt(PROFILE, plaintext)) == plaintext


def test_decrypt_with_a_wrong_passphrase_fails(passphrase, monkeypatch):
    """A token decrypted with another passphrase raises TokeoVaultError."""
    vault = ScryptVault()
    token = vault.encrypt(PROFILE, 's3cret')
    monkeypatch.setenv(ENV, 'wrong horse')
    with pytest.raises(TokeoVaultError, match='could not be decrypted'):
        vault.decrypt(PROFILE, token)


def test_decrypt_with_another_salt_fails(passphrase):
    """A token decrypted with another salt raises TokeoVaultError."""
    vault = ScryptVault()
    token = vault.encrypt(PROFILE, 's3cret')
    with pytest.raises(TokeoVaultError, match='could not be decrypted'):
        vault.decrypt(dict(PROFILE, salt=OTHER_SALT), token)


def test_decrypt_a_tampered_token_fails(passphrase):
    """A token with one changed character raises TokeoVaultError."""
    vault = ScryptVault()
    token = vault.encrypt(PROFILE, 's3cret')
    # the 40th character encodes byte 30, which lies in the ciphertext part
    tampered = token[:40] + ('A' if token[40] != 'A' else 'B') + token[41:]
    with pytest.raises(TokeoVaultError, match='could not be decrypted'):
        vault.decrypt(PROFILE, tampered)


def test_token_is_decrypted_by_enc_with_the_derived_key(passphrase, monkeypatch):
    """The enc handler decrypts a scrypt token with the derived key."""
    token = ScryptVault().encrypt(PROFILE, 's3cret')
    monkeypatch.setenv('VAULT_TESTS_SCRYPT_DERIVED', _derive(passphrase, SALT, 16).decode())
    assert EncVault().decrypt({'type': 'enc', 'env': 'VAULT_TESTS_SCRYPT_DERIVED'}, token) == 's3cret'


# profile values


@pytest.mark.parametrize('method', ['encrypt', 'decrypt'])
def test_profile_without_env_fails(method):
    """A profile without an env name raises TokeoVaultError."""
    profile = {k: v for k, v in PROFILE.items() if k != 'env'}
    with pytest.raises(TokeoVaultError, match='missing an "env" key'):
        getattr(ScryptVault(), method)(profile, 'value')


@pytest.mark.parametrize('method', ['encrypt', 'decrypt'])
def test_unset_env_variable_fails(method, monkeypatch):
    """An env variable that is not set raises TokeoVaultError naming it."""
    monkeypatch.delenv(ENV, raising=False)
    with pytest.raises(TokeoVaultError, match=f"'{ENV}' is not set"):
        getattr(ScryptVault(), method)(PROFILE, 'value')


@pytest.mark.parametrize('method', ['encrypt', 'decrypt'])
@pytest.mark.parametrize('salt', [None, ''], ids=['no_salt', 'empty_salt'])
def test_profile_without_salt_fails(passphrase, method, salt):
    """A profile without a salt raises TokeoVaultError."""
    profile = dict(PROFILE, salt=salt)
    with pytest.raises(TokeoVaultError, match='missing a "salt"'):
        getattr(ScryptVault(), method)(profile, 'value')


@pytest.mark.parametrize(
    'field, value',
    [('salt', 'xyz'), ('salt', 12345678), ('cost', 15), ('cost', 1), ('cost', 'many')],
    ids=['salt_no_base64', 'salt_int', 'cost_no_power_of_two', 'cost_one', 'cost_no_number'],
)
def test_invalid_profile_value_fails(passphrase, field, value):
    """An invalid salt or cost raises TokeoVaultError, not a raw error."""
    with pytest.raises(TokeoVaultError):
        ScryptVault().encrypt(dict(PROFILE, **{field: value}), 'value')


# create


def test_create_scaffolds_a_scrypt_profile_with_a_fresh_salt():
    """create gives a 16 byte salt and a quoted passphrase placeholder."""
    scaffold = ScryptVault().create('my-app')
    profile = scaffold['profile']
    assert (profile['type'], profile['env']) == ('scrypt', 'VAULT_MY_APP_PASSPHRASE')
    assert len(base64.b64decode(profile['salt'])) == 16
    [secret] = scaffold['secrets']
    assert (secret['name'], secret['quote']) == ('VAULT_MY_APP_PASSPHRASE', True)
    assert secret['value'].startswith('change me')


def test_create_draws_a_new_salt_each_time():
    """Two scaffolds of the same name get different salts."""
    vault = ScryptVault()
    assert vault.create('a')['profile']['salt'] != vault.create('a')['profile']['salt']


def test_create_profile_works_once_the_passphrase_is_set(monkeypatch):
    """The scaffolded profile works once its passphrase variable is set."""
    scaffold = ScryptVault().create('example')
    monkeypatch.setenv('VAULT_EXAMPLE_PASSPHRASE', 'my passphrase')
    # a low cost keeps the derivation of the test fast
    profile = dict(scaffold['profile'], cost=16)
    vault = ScryptVault()
    assert vault.decrypt(profile, vault.encrypt(profile, 's3cret')) == 's3cret'
