"""
Tests for tokeo.core.vaults.enc: the Fernet handler with its key from an
environment variable, its round trips, a wrong key, tampered payloads,
missing or invalid key material and the profile scaffold.
"""

import base64

import pytest
from cryptography.fernet import Fernet

from tokeo.core.vaults import TokeoVaultError
from tokeo.core.vaults.enc import EncVault, generate_fernet_key

# the env variable the profiles of this file take their key from
ENV = 'VAULT_TESTS_ENC_KEY'
PROFILE = {'type': 'enc', 'env': ENV}


@pytest.fixture
def key(monkeypatch):
    """A fresh Fernet key, set in ENV for the test."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(ENV, key)
    return key


def _tamper(token, index):
    # flip one bit of the decoded Fernet token at index and encode it again
    raw = bytearray(base64.urlsafe_b64decode(token))
    raw[index] ^= 0x01
    return base64.urlsafe_b64encode(bytes(raw)).decode()


# encrypt and decrypt


@pytest.mark.parametrize('plaintext', ['s3cret', '', 'Umlaute äöüß €', 'x' * 10000], ids=['ascii', 'empty', 'unicode', 'long'])
def test_encrypt_decrypt_round_trip(key, plaintext):
    """A value encrypted with a key decrypts to the same value."""
    vault = EncVault()
    assert vault.decrypt(PROFILE, vault.encrypt(PROFILE, plaintext)) == plaintext


def test_encrypt_returns_a_fernet_token_of_the_key(key):
    """The payload is a str token that plain Fernet with the same key decrypts."""
    token = EncVault().encrypt(PROFILE, 's3cret')
    assert isinstance(token, str)
    assert Fernet(key.encode()).decrypt(token.encode()) == b's3cret'


def test_encrypt_twice_gives_different_tokens(key):
    """The same value encrypted twice gives two different tokens."""
    vault = EncVault()
    assert vault.encrypt(PROFILE, 's3cret') != vault.encrypt(PROFILE, 's3cret')


def test_decrypt_a_token_made_by_plain_fernet(key):
    """A token made by plain Fernet with the key of the profile decrypts."""
    token = Fernet(key.encode()).encrypt(b's3cret').decode()
    assert EncVault().decrypt(PROFILE, token) == 's3cret'


def test_decrypt_with_a_wrong_key_fails(key, monkeypatch):
    """A token decrypted with another key raises TokeoVaultError."""
    vault = EncVault()
    token = vault.encrypt(PROFILE, 's3cret')
    monkeypatch.setenv(ENV, generate_fernet_key())
    with pytest.raises(TokeoVaultError, match='could not be decrypted'):
        vault.decrypt(PROFILE, token)


@pytest.mark.parametrize('index', [0, 5, 30, -1], ids=['version', 'timestamp', 'ciphertext', 'hmac'])
def test_decrypt_a_tampered_token_fails(key, index):
    """A token with one flipped bit raises TokeoVaultError."""
    vault = EncVault()
    token = vault.encrypt(PROFILE, 's3cret')
    with pytest.raises(TokeoVaultError, match='could not be decrypted'):
        vault.decrypt(PROFILE, _tamper(token, index))


@pytest.mark.parametrize('token', ['', 'not a token', 'gAAAAAB'], ids=['empty', 'garbage', 'truncated'])
def test_decrypt_no_valid_token_fails(key, token):
    """An empty, garbled or truncated payload raises TokeoVaultError."""
    with pytest.raises(TokeoVaultError, match='could not be decrypted'):
        EncVault().decrypt(PROFILE, token)


# key material


@pytest.mark.parametrize('method', ['encrypt', 'decrypt'])
@pytest.mark.parametrize('profile', [{'type': 'enc'}, {'type': 'enc', 'env': ''}], ids=['no_env', 'empty_env'])
def test_profile_without_env_fails(method, profile):
    """A profile without an env name raises TokeoVaultError."""
    with pytest.raises(TokeoVaultError, match='missing an "env" key'):
        getattr(EncVault(), method)(profile, 'value')


@pytest.mark.parametrize('method', ['encrypt', 'decrypt'])
def test_unset_env_variable_fails(method, monkeypatch):
    """An env variable that is not set raises TokeoVaultError naming it."""
    monkeypatch.delenv(ENV, raising=False)
    with pytest.raises(TokeoVaultError, match=f"'{ENV}' is not set"):
        getattr(EncVault(), method)(PROFILE, 'value')


@pytest.mark.parametrize('method', ['encrypt', 'decrypt'])
@pytest.mark.parametrize('value', ['', 'short', 'x' * 44], ids=['empty', 'short', 'no_base64'])
def test_invalid_fernet_key_fails(method, value, monkeypatch):
    """A key that is no Fernet key raises TokeoVaultError."""
    monkeypatch.setenv(ENV, value)
    with pytest.raises(TokeoVaultError, match='not a valid Fernet key'):
        getattr(EncVault(), method)(PROFILE, 'value')


# generate_fernet_key


def test_generate_fernet_key_gives_fresh_valid_keys():
    """Two generated keys differ and each one makes a working Fernet."""
    first, second = generate_fernet_key(), generate_fernet_key()
    assert first != second
    for key in (first, second):
        assert isinstance(key, str)
        assert Fernet(key.encode()).decrypt(Fernet(key.encode()).encrypt(b'x')) == b'x'


# create


def test_create_scaffolds_an_enc_profile_with_a_fresh_key(monkeypatch):
    """create gives an enc profile on VAULT_<NAME>_KEY and a key that encrypts."""
    scaffold = EncVault().create('my-app')
    assert scaffold['profile'] == {'type': 'enc', 'env': 'VAULT_MY_APP_KEY'}
    [secret] = scaffold['secrets']
    assert (secret['name'], secret['quote']) == ('VAULT_MY_APP_KEY', False)
    monkeypatch.setenv('VAULT_MY_APP_KEY', secret['value'])
    vault = EncVault()
    assert vault.decrypt(scaffold['profile'], vault.encrypt(scaffold['profile'], 's3cret')) == 's3cret'


def test_create_draws_a_new_key_each_time():
    """Two scaffolds of the same name get different keys."""
    vault = EncVault()
    assert vault.create('a')['secrets'][0]['value'] != vault.create('a')['secrets'][0]['value']
