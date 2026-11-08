"""
Vault handler that keeps a value as plain text.

A ```plain``` profile marks a value as vault value without encrypting it: the
value stands readable in the config file, but the app masks it like any vault
value, e.g. in pdoc or in a dump by ```redact_vault```. It needs no key and no
cryptography.

```yaml
vault:
  profiles:
    dev:
      type: plain

automate:
  hosts:
    web1:
      password: !vault:dev my-dev-password
```

### Reminders

    .. warning::

        A plain value is not encrypted, everybody who reads the file reads
        it. Use it for development and tests, or to keep a value out of
        what the app shows, never for a real secret in a shared file.

"""

from tokeo.core.vaults import TokeoVault


class PlainVault(TokeoVault):
    """
    Vault handler that keeps the value as plain text, not encrypted.

    ### Notes

    : For a switch to ```enc``` or ```scrypt``` change the profile and encrypt
        the values anew by ```vault encrypt```

    """

    def encrypt(self, profile, plaintext):
        """
        Keep the plaintext as it is.

        ### Args

        - **profile** (dict): The vault profile from the ```vault``` config
        - **plaintext** (str): The value

        ### Returns

        - **str**: The value unchanged, as the payload of ```!vault:```

        """
        return plaintext

    def decrypt(self, profile, ciphertext):
        """
        Give the payload back as it is.

        ### Args

        - **profile** (dict): The vault profile from the ```vault``` config
        - **ciphertext** (str): The payload of ```!vault:```

        ### Returns

        - **str**: The payload unchanged

        """
        return ciphertext

    def build_scaffold(self, name):
        """
        Build a profile scaffold of type plain, it needs no secret.

        ### Args

        - **name** (str): The profile name

        ### Returns

        - **dict**: ```profile``` with the type only and no ```secrets```

        """
        return {'profile': {'type': 'plain'}, 'secrets': []}
