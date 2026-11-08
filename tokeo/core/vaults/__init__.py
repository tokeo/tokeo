"""
Vault helpers for Tokeo applications.

A small framework for keeping secrets out of plaintext config. Encrypted
values are marked in yaml with a ```!vault:<profile>``` tag and loaded as opaque
```VaultRef``` objects, so the parsed configuration never holds a decrypted
secret. A ```VaultRef``` is turned into plaintext only on demand, at the point
of use, by a vault handler selected through the referenced profile.

A profile lives in the ```vault``` config section and binds a handler:

```yaml
vault:
  profiles:
    master:
      type: enc
      env: VAULT_MASTER1
```

The ```type``` selects a registered handler (for example the built-in ```enc```,
```scrypt``` or ```plain``` handlers); the remaining keys are handler specific.

This module needs no cryptography: it reads the tag by ```VaultLoader```, marks
a decrypted value as ```VaultSecret``` and masks both by ```redact_vault```, so
e.g. pdoc shows a config without decrypting anything.

### Reminders

    .. warning::

        A resolved secret is returned as plaintext to the caller and must be
        used transiently. Never store it back into the configuration or log it.

"""

import re

import yaml
from tokeo.core.exc import TokeoError

#: The text that stands for a vault value wherever it is shown
VAULT_MASK = '***'


class TokeoVaultError(TokeoError):
    """Raised when a vault profile, handler, or secret cannot be resolved."""


class VaultRef:
    """
    Opaque reference to an encrypted configuration value.

    Holds the name of the vault profile that can decrypt the value and the
    encrypted payload as stored in the configuration. The plaintext is never
    kept here; it is produced only when ```resolve``` is called.

    ### Args

    - **profile** (str): Name of the vault profile in the ```vault``` config
        section used to decrypt the payload
    - **ciphertext** (str): The encrypted value as written in the config

    """

    def __init__(self, profile, ciphertext):
        #: Name of the vault profile that decrypts the payload
        self.profile = profile
        #: The encrypted value as written in the config
        self.ciphertext = ciphertext

    def __repr__(self):
        # never expose the ciphertext in full, e.g. in logs or tracebacks
        return f'VaultRef(profile={self.profile!r})'

    @classmethod
    def from_yaml(cls, loader, tag_suffix, node):
        """
        Build a ```VaultRef``` from a ```!vault:<profile> <ciphertext>``` scalar.

        Used as the multi-constructor for the ```!vault:``` yaml tag: the
        profile rides in the tag suffix, the ciphertext is the scalar value.

        ### Args

        - **loader**: The yaml loader constructing the node
        - **tag_suffix** (str): The part after ```!vault:``` (the profile name)
        - **node**: The yaml scalar node holding the ciphertext

        ### Returns

        - **VaultRef**: The opaque reference for the encrypted value

        """
        return cls(tag_suffix, loader.construct_scalar(node))


class VaultLoader(yaml.SafeLoader):
    """
    The yaml loader that knows the !vault: tag, a tagged value loads as VaultRef.

    ### Notes

    : The tag is known to this loader only, not to the loaders of yaml, so
        another ```yaml.full_load``` of the process loads no ```VaultRef```

    : Based on the safe loader: a config holds plain values only, a
        ```!!python``` tag is refused

    """

    pass


VaultLoader.add_multi_constructor('!vault:', VaultRef.from_yaml)


class VaultSecret(str):
    """
    The plaintext of a vault value, a str that ```redact_vault``` masks.

    ### Notes

    : Works as the plain str everywhere, only ```repr()``` shows the mask,
        e.g. in a printed dict or a traceback

    : A str built from it, e.g. a url with the password in it, is a plain
        str again and no longer masked

    """

    def __repr__(self):
        return repr(VAULT_MASK)


def redact_vault(data):
    """
    Copy data with every vault value masked, at any depth.

    ### Args

    - **data** (Any): A value, dict, list or tuple, e.g. a task of automate

    ### Returns

    - **Any**: The same shape, each ```VaultRef``` and ```VaultSecret``` replaced
        by ```VAULT_MASK```

    ### Example

    ```python
    redact_vault({'user': 'tom', 'password': app.config.get('db', 'password')})
    # {'user': 'tom', 'password': '***'}
    ```

    """
    if isinstance(data, (VaultRef, VaultSecret)):
        return VAULT_MASK
    if isinstance(data, dict):
        return {key: redact_vault(value) for key, value in data.items()}
    if isinstance(data, list):
        return [redact_vault(value) for value in data]
    if isinstance(data, tuple):
        return tuple(redact_vault(value) for value in data)
    return data


def redact_vault_yaml(text):
    """
    Copy yaml text with each !vault: value replaced by the mask.

    ### Args

    - **text** (str): The yaml text, e.g. a config file or a part of it

    ### Returns

    - **str**: The same text, comments and layout kept, each ```!vault:```
        value with its tag shown as ```VAULT_MASK```

    ### Raises

    - **yaml.YAMLError**: If the text is no valid yaml

    ### Example

    ```python
    redact_vault_yaml('password: !vault:master gAAAA  # db')
    # 'password: ***  # db'
    ```

    """
    stack = [node for node in yaml.compose_all(text, Loader=VaultLoader) if node is not None]
    found = []
    while stack:
        node = stack.pop()
        if isinstance(node, yaml.ScalarNode):
            if node.tag.startswith('!vault:'):
                found.append(node)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
        elif isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                stack.extend((key, value))
    # from the end, so the positions before stay valid
    for node in sorted(found, key=lambda node: node.start_mark.index, reverse=True):
        start, end = node.start_mark.index, node.end_mark.index
        # a block value ends with its line breaks, they stay
        breaks = len(text[start:end]) - len(text[start:end].rstrip('\n'))
        text = text[:start] + VAULT_MASK + '\n' * breaks + text[end:]
    return text


class TokeoVault:
    """
    Base interface for vault handlers.

    A handler turns an encrypted payload into plaintext and back, using key
    material that a profile points to. Subclasses implement ```encrypt``` and
    ```decrypt```; both receive the profile dict so they can locate their key.

    """

    def encrypt(self, profile, plaintext):
        """
        Encrypt a plaintext string for the given profile.

        ### Args

        - **profile** (dict): The vault profile from the ```vault``` config
        - **plaintext** (str): The value to encrypt

        ### Returns

        - **str**: The encrypted payload, suitable as a ```!vault``` value

        """
        raise NotImplementedError

    def decrypt(self, profile, ciphertext):
        """
        Decrypt a payload produced for the given profile.

        ### Args

        - **profile** (dict): The vault profile from the ```vault``` config
        - **ciphertext** (str): The encrypted payload to decrypt

        ### Returns

        - **str**: The decrypted plaintext

        """
        raise NotImplementedError

    def _format_env_name(self, name, suffix):
        # build a conventional env variable name for a profile scaffold, for
        # example VAULT_EXAMPLE_KEY, sanitising the profile name to upper case
        base = re.sub('[^0-9A-Za-z]+', '_', name).upper()
        return f'VAULT_{base}_{suffix}'

    def build_scaffold(self, name):
        """
        Build a ready-to-use profile scaffold for this handler type.

        Returns the data a user needs to set up a new vault profile of this
        type: the yaml profile fields (including any non-secret generated
        value such as a salt) and the environment variable(s) the secret
        belongs in. The controller renders this into a paste-ready block.

        ### Args

        - **name** (str): The profile name to scaffold under the ```vault```
            section

        ### Returns

        - **dict**: ```profile``` (the yaml fields for the profile) and
            ```secrets``` (a list of ```name```/```value```/```quoted```
            env entries)

        ### Raises

        - **TokeoVaultError**: If the handler does not support scaffolding

        """
        raise TokeoVaultError('this vault type cannot create a profile scaffold')
