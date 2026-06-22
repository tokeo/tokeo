"""
Built-in ```enc``` vault handler for Tokeo applications.

Encrypts and decrypts values with Fernet (AES-128-CBC plus an HMAC-SHA256
authentication tag), using a ready-made Fernet key taken from an environment
variable named by the profile. This is the base handler; other handlers (for
example ```scrypt```) reuse its Fernet core and only change how the key is
obtained.

A profile for this handler:

```yaml
vault:
  master:
    type: enc
    env: VAULT_MASTER1
```

"""

import os

from cryptography.fernet import Fernet, InvalidToken

from tokeo.core.vaults import Vault, TokeoVaultError


class EncVault(Vault):
    """
    Fernet vault handler that reads a ready Fernet key from the environment.

    The profile names an environment variable (```env```) holding a Fernet key,
    a 32-byte url-safe base64 string as produced by ```vault create```.

    """

    def _key(self, profile):
        # the key lives in the environment, never in the config, so an
        # encrypted file alone is useless without the runtime secret
        name = profile.get('env')
        if not name:
            raise TokeoVaultError('vault profile is missing an "env" key')
        try:
            return os.environ[name].encode()
        except KeyError:
            raise TokeoVaultError(f'environment variable {name!r} is not set')

    def _fernet(self, profile):
        try:
            return Fernet(self._key(profile))
        except (ValueError, TypeError):
            raise TokeoVaultError('vault key is not a valid Fernet key')

    def encrypt(self, profile, plaintext):
        """
        Encrypt a plaintext string into a Fernet token.

        ### Args

        - **profile** (dict): The vault profile selecting the key
        - **plaintext** (str): The value to encrypt

        ### Returns

        - **str**: The Fernet token to store as a ```!vault``` value

        """
        return self._fernet(profile).encrypt(plaintext.encode()).decode()

    def decrypt(self, profile, ciphertext):
        """
        Decrypt a Fernet token back into plaintext.

        ### Args

        - **profile** (dict): The vault profile selecting the key
        - **ciphertext** (str): The Fernet token to decrypt

        ### Returns

        - **str**: The decrypted plaintext

        ### Raises

        - **TokeoVaultError**: If the token is invalid or the key is wrong

        """
        try:
            return self._fernet(profile).decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            raise TokeoVaultError('vault payload could not be decrypted (wrong key or corrupt data)')

    def create(self, name):
        """
        Build an ```enc``` profile scaffold with a fresh Fernet key.

        The key is the secret for an ```enc``` profile and belongs in the
        environment, never in the config, so it is reported for the
        ```VAULT_<NAME>_KEY``` variable the profile points to.

        ### Args

        - **name** (str): The profile name to scaffold

        ### Returns

        - **dict**: The profile fields and the env variable holding the key

        """
        env = self._env_name(name, 'KEY')
        return {
            'profile': {'type': 'enc', 'env': env},
            'secrets': [{'name': env, 'value': generate_fernet_key(), 'quote': False}],
        }


def generate_fernet_key():
    """
    Generate a fresh Fernet key as a url-safe base64 string.

    ### Returns

    - **str**: A new Fernet key, suitable as an ```enc``` profile secret

    """
    return Fernet.generate_key().decode()
