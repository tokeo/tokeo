"""
Built-in ```scrypt``` vault handler for Tokeo applications.

Derives the Fernet key from a passphrase using scrypt, a memory-hard key
derivation function, then reuses the Fernet encrypt/decrypt of the ```enc```
handler. The passphrase comes from an environment variable; the salt and cost
parameters live in the profile and are not secret, so they can be committed
with the configuration.

A profile for this handler:

```yaml
vault:
  human:
    type: scrypt
    env: VAULT_PASSPHRASE
    salt: MmEZBlUsC/NETOEsIHEg+w==
    cost: 16384
    block_size: 8
    parallelization: 1
```

The cost parameters are optional; ```cost``` (scrypt's N, a power of two),
```block_size``` (scrypt's r) and ```parallelization``` (scrypt's p) default to
sensible values when omitted.

"""

import os
import base64

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from tokeo.core.vaults import TokeoVaultError
from tokeo.core.vaults.enc import EncVault


class ScryptVault(EncVault):
    """
    Vault handler that derives its Fernet key from a passphrase via scrypt.

    The profile names an environment variable (```env```) holding the passphrase
    and carries the scrypt salt and cost parameters. scrypt is memory-hard,
    which makes brute-forcing the passphrase on GPUs or ASICs expensive.

    """

    # scrypt cost defaults when a profile omits them; cost=2**14 needs ~16 MB
    # per derivation, which resists parallel guessing. cost is scrypt's N (a
    # power of two), block_size is r, parallelization is p
    _default_cost = 2**14
    _default_block_size = 8
    _default_parallelization = 1

    def _key(self, profile):
        # derive the Fernet key from the passphrase; salt and cost are public
        name = profile.get('env')
        if not name:
            raise TokeoVaultError('vault profile is missing an "env" key')
        try:
            passphrase = os.environ[name].encode()
        except KeyError:
            raise TokeoVaultError(f'environment variable {name!r} is not set')
        salt = profile.get('salt')
        if not salt:
            raise TokeoVaultError('scrypt vault profile is missing a "salt"')
        kdf = Scrypt(
            salt=base64.b64decode(salt),
            length=32,
            n=int(profile.get('cost', self._default_cost)),
            r=int(profile.get('block_size', self._default_block_size)),
            p=int(profile.get('parallelization', self._default_parallelization)),
        )
        # Fernet expects a url-safe base64 key
        return base64.urlsafe_b64encode(kdf.derive(passphrase))

    def create(self, name):
        """
        Build a ```scrypt``` profile scaffold with a fresh salt.

        The salt is generated and is not secret, so it is part of the profile.
        The passphrase is the user's own memorable secret and is shown only as
        a placeholder for the ```VAULT_<NAME>_PASSPHRASE``` variable, to be
        replaced before use.

        ### Args

        - **name** (str): The profile name to scaffold

        ### Returns

        - **dict**: The profile fields (with a salt) and the env variable that
            must hold the chosen passphrase

        """
        env = self._env_name(name, 'PASSPHRASE')
        salt = base64.b64encode(os.urandom(16)).decode()
        return {
            'profile': {'type': 'scrypt', 'env': env, 'salt': salt},
            'secrets': [{'name': env, 'value': 'change me to a memorable passphrase', 'quote': True}],
        }
