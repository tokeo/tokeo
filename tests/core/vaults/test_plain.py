"""
Tests for tokeo.core.vaults.plain: the plain handler keeps a value as it is,
needs no key and no cryptography, and scaffolds a profile without secrets.
"""

from tokeo.core.vaults.plain import PlainVault

PROFILE = {'type': 'plain'}


def test_encrypt_and_decrypt_keep_the_value():
    """encrypt and decrypt hand the value back unchanged."""
    vault = PlainVault()
    assert vault.encrypt(PROFILE, 'my-dev-password') == 'my-dev-password'
    assert vault.decrypt(PROFILE, 'my-dev-password') == 'my-dev-password'


def test_build_scaffold_gives_a_profile_without_secrets():
    """create gives the type only and no environment variable."""
    assert PlainVault().build_scaffold('dev') == {'profile': {'type': 'plain'}, 'secrets': []}
