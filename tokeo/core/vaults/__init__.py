"""
Vault helpers for Tokeo applications.

A small framework for keeping secrets out of plaintext config. Encrypted
values are marked in yaml with a ``!vault:<profile>`` tag and loaded as opaque
``VaultRef`` objects, so the parsed configuration never holds a decrypted
secret. A ``VaultRef`` is turned into plaintext only on demand, at the point
of use, by a vault handler selected through the referenced profile.

A profile lives in the ``vault`` config section and binds a handler:

```yaml
vault:
  master:
    type: enc
    env: VAULT_MASTER1
```

The ``type`` selects a registered handler (for example the built-in ``enc``
or ``scrypt`` handlers); the remaining keys are handler specific.

### Reminders

    .. warning::

        A resolved secret is returned as plaintext to the caller and must be
        used transiently. Never store it back into the configuration or log it.

"""

import re

import yaml

from tokeo.core.exc import TokeoError


class TokeoVaultError(TokeoError):
    """Raised when a vault profile, handler, or secret cannot be resolved."""


class VaultRef:
    """
    Opaque reference to an encrypted configuration value.

    Holds the name of the vault profile that can decrypt the value and the
    encrypted payload as stored in the configuration. The plaintext is never
    kept here; it is produced only when ``resolve`` is called.

    ### Args

    - **profile** (str): Name of the vault profile in the ``vault`` config
        section used to decrypt the payload
    - **ciphertext** (str): The encrypted value as written in the config

    """

    def __init__(self, profile, ciphertext):
        self.profile = profile
        self.ciphertext = ciphertext

    def __repr__(self):
        # never expose the ciphertext in full, e.g. in logs or tracebacks
        return f'VaultRef(profile={self.profile!r})'


class Vault:
    """
    Base interface for vault handlers.

    A handler turns an encrypted payload into plaintext and back, using key
    material that a profile points to. Subclasses implement ``encrypt`` and
    ``decrypt``; both receive the profile dict so they can locate their key.

    """

    def encrypt(self, profile, plaintext):
        """
        Encrypt a plaintext string for the given profile.

        ### Args

        - **profile** (dict): The vault profile from the ``vault`` config
        - **plaintext** (str): The value to encrypt

        ### Returns

        - **str**: The encrypted payload, suitable as a ``!vault`` value

        """
        raise NotImplementedError

    def decrypt(self, profile, ciphertext):
        """
        Decrypt a payload produced for the given profile.

        ### Args

        - **profile** (dict): The vault profile from the ``vault`` config
        - **ciphertext** (str): The encrypted payload to decrypt

        ### Returns

        - **str**: The decrypted plaintext

        """
        raise NotImplementedError

    def _env_name(self, name, suffix):
        # build a conventional env variable name for a profile scaffold, for
        # example VAULT_EXAMPLE_KEY, sanitising the profile name to upper case
        base = re.sub('[^0-9A-Za-z]+', '_', name).upper()
        return f'VAULT_{base}_{suffix}'

    def create(self, name):
        """
        Build a ready-to-use profile scaffold for this handler type.

        Returns the data a user needs to set up a new vault profile of this
        type: the yaml profile fields (including any non-secret generated
        value such as a salt) and the environment variable(s) the secret
        belongs in. The controller renders this into a paste-ready block.

        ### Args

        - **name** (str): The profile name to scaffold under the ``vault``
            section

        ### Returns

        - **dict**: ``profile`` (the yaml fields for the profile) and
            ``secrets`` (a list of ``name``/``value``/``quote`` env entries)

        ### Raises

        - **TokeoVaultError**: If the handler does not support scaffolding

        """
        raise TokeoVaultError('this vault type cannot create a profile scaffold')


# registry of vault handler instances keyed by their config ``type`` name
_handlers = {}


def register_handler(name, handler):
    """
    Register a vault handler instance under a ``type`` name.

    ### Args

    - **name** (str): The ``type`` value a vault profile uses to select this
        handler
    - **handler** (Vault): The handler instance to register

    """
    _handlers[name] = handler


def get_handler(name):
    """
    Return the registered vault handler for a ``type`` name.

    ### Args

    - **name** (str): The handler ``type`` to look up

    ### Returns

    - **Vault**: The registered handler instance

    ### Raises

    - **TokeoVaultError**: If no handler is registered for the name

    """
    try:
        return _handlers[name]
    except KeyError:
        raise TokeoVaultError(f'no vault handler registered for type {name!r}')


def get_profile(app, name):
    """
    Read a named profile from the ``vault.profiles`` configuration section.

    ### Args

    - **app**: The application instance
    - **name** (str): The profile name under ``vault.profiles``

    ### Returns

    - **dict**: The profile fields (``type`` and handler-specific keys)

    ### Raises

    - **TokeoVaultError**: If the profile is unknown or has no ``type``

    """
    try:
        profile = app.config.get('vault', 'profiles')[name]
    except Exception:
        raise TokeoVaultError(f'unknown vault profile {name!r}')
    if not isinstance(profile, dict) or 'type' not in profile:
        raise TokeoVaultError(f'vault profile {name!r} is missing a type')
    return profile


def resolve(app, value):
    """
    Resolve a value to plaintext, decrypting it if it is a ``VaultRef``.

    Non-``VaultRef`` values (plain config values or env overrides) are
    returned unchanged, so a caller can wrap any config read without first
    checking the type. The plaintext is returned to the caller and is never
    stored back into the configuration.

    ### Args

    - **app**: The application instance, used to read the ``vault`` profile
    - **value**: A config value that may be a ``VaultRef`` or a plain value

    ### Returns

    - The decrypted plaintext for a ``VaultRef``, otherwise the value as is

    ### Raises

    - **TokeoVaultError**: If the referenced profile is unknown or its
        handler cannot decrypt the payload

    """
    if not isinstance(value, VaultRef):
        return value
    profile = get_profile(app, value.profile)
    return get_handler(profile['type']).decrypt(profile, value.ciphertext)


def _construct_vault_ref(loader, tag_suffix, node):
    # build a VaultRef from a `!vault:<profile> <ciphertext>` scalar; the
    # profile rides in the tag suffix, the ciphertext is the scalar value
    return VaultRef(tag_suffix, loader.construct_scalar(node))


def register_yaml_tag():
    """
    Register the ``!vault:<profile>`` yaml tag on the loader cement uses.

    After registration, a scalar tagged ``!vault:<profile>`` is loaded as a
    ``VaultRef`` instead of a plain string, so encrypted values stay opaque
    in the parsed configuration. cement parses config with ``yaml.full_load``,
    so the constructor is registered on the FullLoader.

    """
    yaml.add_multi_constructor('!vault:', _construct_vault_ref, Loader=yaml.FullLoader)
