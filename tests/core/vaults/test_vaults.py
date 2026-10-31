"""
Tests for tokeo.core.vaults: the VaultRef built from a ```!vault:<profile>```
yaml scalar, the base Vault interface and the vault error class.
"""

import pytest
import yaml

from tokeo.core.exc import TokeoError
from tokeo.core.vaults import VaultRef, Vault, TokeoVaultError


class _Loader(yaml.SafeLoader):
    # a loader of its own, so the !vault tag never reaches yaml's shared loaders
    pass


_Loader.add_multi_constructor('!vault:', VaultRef.from_yaml)


def _load(text):
    # parse text with the !vault tag and return the value of its key "a"
    return yaml.load(text, Loader=_Loader)['a']


# TokeoVaultError


def test_tokeo_vault_error_is_a_tokeo_error():
    """TokeoVaultError derives from TokeoError and is caught as such."""
    with pytest.raises(TokeoError):
        raise TokeoVaultError('boom')


# VaultRef


def test_vaultref_holds_profile_and_ciphertext():
    """A VaultRef keeps the profile name and the payload as given."""
    ref = VaultRef('master', 'gAAAAABpayload')
    assert (ref.profile, ref.ciphertext) == ('master', 'gAAAAABpayload')


def test_vaultref_repr_hides_the_ciphertext():
    """The repr names the profile only, never the payload."""
    ref = VaultRef('master', 'gAAAAABpayload')
    assert repr(ref) == "VaultRef(profile='master')"
    assert 'gAAAAABpayload' not in repr(ref)


# VaultRef.from_yaml


def test_vaultref_from_yaml_takes_profile_from_the_tag():
    """!vault:master gAAA loads as a VaultRef of profile master and payload gAAA."""
    ref = _load('a: !vault:master gAAA')
    assert isinstance(ref, VaultRef)
    assert (ref.profile, ref.ciphertext) == ('master', 'gAAA')


@pytest.mark.parametrize(
    'text, ciphertext',
    [
        ('a: !vault:p "two words"', 'two words'),
        ('a: !vault:p 12345', '12345'),
        ('a: !vault:p true', 'true'),
        ('a: !vault:p', ''),
    ],
    ids=['quoted', 'digits', 'bool_word', 'empty'],
)
def test_vaultref_from_yaml_keeps_the_payload_a_raw_string(text, ciphertext):
    """The payload stays the raw scalar string, never an int, bool or None."""
    assert _load(text).ciphertext == ciphertext


def test_vaultref_from_yaml_with_empty_suffix_has_empty_profile():
    """!vault: without a profile name loads with an empty profile."""
    assert _load('a: !vault: gAAA').profile == ''


@pytest.mark.parametrize('text', ['a: !vault:p {x: 1}', 'a: !vault:p [1, 2]'], ids=['mapping', 'sequence'])
def test_vaultref_from_yaml_rejects_a_non_scalar(text):
    """A mapping or a sequence under a !vault tag raises a yaml ConstructorError."""
    with pytest.raises(yaml.constructor.ConstructorError):
        _load(text)


# Vault


@pytest.mark.parametrize('method', ['encrypt', 'decrypt'])
def test_vault_base_methods_are_abstract(method):
    """encrypt and decrypt of the base Vault raise NotImplementedError."""
    with pytest.raises(NotImplementedError):
        getattr(Vault(), method)({'type': 'x'}, 'value')


def test_vault_create_is_not_supported_by_the_base():
    """create of the base Vault raises TokeoVaultError."""
    with pytest.raises(TokeoVaultError, match='cannot create a profile scaffold'):
        Vault().create('example')


@pytest.mark.parametrize(
    'name, env',
    [
        ('example', 'VAULT_EXAMPLE_KEY'),
        ('my-app.prod', 'VAULT_MY_APP_PROD_KEY'),
        ('a  b', 'VAULT_A_B_KEY'),
        ('Prod2', 'VAULT_PROD2_KEY'),
    ],
)
def test_vault_env_name_is_sanitised_and_upper_case(name, env):
    """Each run of other characters becomes one _, letters go upper case."""
    assert Vault()._env_name(name, 'KEY') == env
