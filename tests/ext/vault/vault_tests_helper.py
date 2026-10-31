"""
Shared parts of the tests of tokeo.ext.vault: the test app on a yaml config
file, the vault section with its profiles, the key material set by
monkeypatch and the tokens made independently of the code under test.
"""

import os
import base64
import hashlib

import yaml
from cryptography.fernet import Fernet
from tokeo.main import TokeoTest

# the env variables the profiles of VAULT_SECTION take their secrets from
MASTER_ENV = 'VAULT_TESTS_MASTER_KEY'
PASSPHRASE_ENV = 'VAULT_TESTS_PASSPHRASE'
PASSPHRASE = 'correct horse battery staple'
SALT = base64.b64encode(b'0123456789abcdef').decode()
# a low scrypt cost keeps the derivations of the tests fast
SCRYPT_COST = 16

# a master profile of type enc and a human profile of type scrypt
VAULT_SECTION = f"""
vault:
  profiles:
    master:
      type: enc
      env: {MASTER_ENV}
    human:
      type: scrypt
      env: {PASSPHRASE_ENV}
      salt: {SALT}
      cost: {SCRYPT_COST}
"""


class VaultTest(TokeoTest):
    """A test app with the vault extension, reading the given yaml config file."""

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.vault',
        ]

    def __init__(self, config_file=None, **kw):
        super().__init__(config_files=[config_file] if config_file else [], **kw)


def isolate_yaml_tags(monkeypatch):
    # give yaml.FullLoader a copy of its multi constructors, so the !vault tag
    # the extension registers globally is gone again after the test
    monkeypatch.setattr(yaml.FullLoader, 'yaml_multi_constructors', dict(yaml.FullLoader.yaml_multi_constructors))


def set_secrets(monkeypatch):
    # set a fresh Fernet key in MASTER_ENV and PASSPHRASE in PASSPHRASE_ENV,
    # return the key
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(MASTER_ENV, key)
    monkeypatch.setenv(PASSPHRASE_ENV, PASSPHRASE)
    return key


def write_config(directory, text=''):
    # write VAULT_SECTION plus text as app.yaml into directory, return its path
    path = os.path.join(str(directory), 'app.yaml')
    with open(path, 'w') as f:
        f.write(VAULT_SECTION + text)
    return path


def fernet_token(key, plaintext):
    # encrypt plaintext with plain Fernet, as the master profile would
    return Fernet(key.encode()).encrypt(plaintext.encode()).decode()


def scrypt_token(plaintext):
    # encrypt plaintext with a key derived by the scrypt of hashlib from
    # PASSPHRASE and SALT, as the human profile would
    raw = hashlib.scrypt(PASSPHRASE.encode(), salt=base64.b64decode(SALT), n=SCRYPT_COST, r=8, p=1, dklen=32)
    return fernet_token(base64.urlsafe_b64encode(raw).decode(), plaintext)
