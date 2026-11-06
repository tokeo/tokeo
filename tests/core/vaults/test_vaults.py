"""
Tests for tokeo.core.vaults: the VaultRef built from a ```!vault:<profile>```
yaml scalar by the VaultLoader, the VaultSecret of a decrypted value and
redact_vault that masks both, the base Vault interface and the vault error
class.
"""

import pytest
import yaml

from tokeo.core.exc import TokeoError
from tokeo.core.vaults import VAULT_MASK, VaultRef, VaultLoader, VaultSecret, Vault, TokeoVaultError, redact_vault, redact_vault_yaml


def _load(text):
    # parse text with the !vault tag and return the value of its key "a"
    return yaml.load(text, Loader=VaultLoader)['a']


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
def test_vault_build_env_name_is_sanitised_and_upper_case(name, env):
    """Each run of other characters becomes one _, letters go upper case."""
    assert Vault()._build_env_name(name, 'KEY') == env


# VaultLoader


def test_vault_loader_leaves_the_full_loader_without_the_tag():
    """Only the VaultLoader knows !vault:, yaml.full_load still refuses it."""
    assert isinstance(_load('a: !vault:master gAAA'), VaultRef)
    with pytest.raises(yaml.constructor.ConstructorError, match='!vault:master'):
        yaml.full_load('a: !vault:master gAAA')


def test_vault_loader_refuses_a_python_tag():
    """The VaultLoader is a safe loader, a !!python tag raises."""
    with pytest.raises(yaml.constructor.ConstructorError, match='python/name:os.system'):
        _load('a: !!python/name:os.system')


# VaultSecret and redact_vault


def test_vault_secret_is_the_str_and_hides_in_repr():
    """A VaultSecret works as its str, only repr shows the mask."""
    secret = VaultSecret('s3cret')
    assert secret == 's3cret' and f'{secret}' == 's3cret' and secret.upper() == 'S3CRET'
    assert repr(secret) == repr(VAULT_MASK) and repr({'password': secret}) == "{'password': '***'}"


def test_redact_vault_masks_vault_values_at_any_depth():
    """VaultRef and VaultSecret become the mask, all other values stay."""
    data = {
        'user': 'tom',
        'password': VaultSecret('s3cret'),
        'hosts': [{'password': VaultRef('master', 'gAAA'), 'port': 22}],
        'pair': ('a', VaultSecret('b')),
    }
    assert redact_vault(data) == {'user': 'tom', 'password': '***', 'hosts': [{'password': '***', 'port': 22}], 'pair': ('a', '***')}
    # the data itself stays as it is
    assert data['password'] == 's3cret'


def test_redact_vault_leaves_a_plain_str_built_from_a_secret():
    """A str built from a VaultSecret is no vault value any more."""
    url = 'amqp://u:' + VaultSecret('pw') + '@host'
    assert redact_vault({'url': url}) == {'url': 'amqp://u:pw@host'}


# redact_vault_yaml


def test_redact_vault_yaml_masks_each_vault_value_and_keeps_the_text():
    """Each !vault: value becomes the mask, comments, layout and others stay."""
    text = (
        'db:\n'
        '  # the password\n'
        '  password: !vault:master gAAAA  # comment\n'
        '  address: !vault:dev Main Street 1\n'
        '  list: [1, !vault:human gBBB, 3]\n'
        '  quoted: !vault:dev "a: b"\n'
        '  block: !vault:dev |\n'
        '    line one\n'
        '    line two\n'
        '  after: x\n'
    )
    assert redact_vault_yaml(text) == (
        'db:\n'
        '  # the password\n'
        '  password: ***  # comment\n'
        '  address: ***\n'
        '  list: [1, ***, 3]\n'
        '  quoted: ***\n'
        '  block: ***\n'
        '  after: x\n'
    )


def test_redact_vault_yaml_keeps_a_text_without_vault_values():
    """A text without !vault: comes back unchanged, also an empty one."""
    assert redact_vault_yaml('a: 1  # one\n') == 'a: 1  # one\n'
    assert redact_vault_yaml('') == ''
