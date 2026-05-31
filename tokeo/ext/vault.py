"""
Tokeo vault extension module.

This extension adds transparent decryption of secrets on top of the Tokeo YAML
config handler. Encrypted values are marked in yaml with a ``!vault:<profile>``
tag and loaded as opaque ``VaultRef`` objects, so the stored configuration
never holds a decrypted secret. On read, an encrypted value is resolved to
plaintext at its leaf, while the parent handler provides the walk, the
copy-only-when-changed behaviour, and environment overrides at any depth.

A profile lives in the ``vault`` config section and binds a handler by its
``type`` (the built-in ``enc`` and ``scrypt`` handlers are registered here):

```yaml
vault:
  master:
    type: enc
    env: VAULT_MASTER1

pocketbase:
  auth_password: !vault:master gAAAAAB...
```

Enabling this extension replaces the default config handler with the vault
handler; no consumer code needs to change to read encrypted values.

"""

import sys
from os.path import basename
from cement import Controller, ex
from tokeo.ext.yaml import TokeoYamlConfigHandler
from tokeo.core.vaults import (
    VaultRef,
    TokeoVaultError,
    register_handler,
    register_yaml_tag,
    get_handler as vault_get_handler,
    resolve as vault_resolve,
)
from tokeo.core.vaults.enc import EncVault
from tokeo.core.vaults.scrypt import ScryptVault


class TokeoVaultConfigHandler(TokeoYamlConfigHandler):
    """
    Vault-aware YAML configuration handler for Tokeo applications.

    Extends the Tokeo YAML handler with one concern only: decrypting an
    encrypted ``VaultRef`` leaf when a value is read. Everything else, the
    deep environment overrides and the copy-only-when-changed behaviour, is
    inherited unchanged, so the stored configuration is never modified and a
    plain read returns the same object.

    Decryption applies only to ``!vault`` values that come from a config file.
    An environment override is always taken as a plain value and is never
    decrypted: encryption protects secrets at rest in committed files, while
    an environment variable is already a trusted, out-of-band injection path.
    Because the parent handler applies the env override before this decryption
    hook, an env override of a secret simply replaces it with its plain value.

    ### Notes

    - Decryption is applied through the parent handler's ``_resolve_leaf``
        hook, so it composes with environment overrides (an env override of
        the same value wins, since it is applied before the leaf hook)
    - An env variable holding a ``!vault`` (or any other yaml tag) value is
        not decrypted but rejected by the parent handler with a
        ``TokeoYamlConfigError``; quote the value to inject it as plain text

    ### Reminders

        .. warning::

            Never put an encrypted secret in an environment variable
            expecting it to be decrypted. Environment overrides are plain
            values only; secrets are encrypted exclusively in config files.

    """

    class Meta:
        """Meta configuration for the vault config handler."""

        label = 'tokeo.vault'
        """The unique string identifier of this handler in the application."""

    def _resolve_leaf(self, value, path):
        """
        Decrypt an encrypted leaf value on read.

        Extends the yaml handler's leaf hook so that an encrypted ``VaultRef``
        is resolved to plaintext, transiently, for the caller; any other value
        is passed through unchanged.

        ### Args

        - **value**: The scalar leaf value, possibly a ``VaultRef``
        - **path** (list|None): The config path to this value

        ### Returns

        - The decrypted plaintext for a ``VaultRef``, otherwise the value as is

        """
        if isinstance(value, VaultRef):
            return vault_resolve(self.app, value)
        return super()._resolve_leaf(value, path)


class TokeoVaultController(Controller):
    """
    Command-line controller for managing vault secrets.

    Provides commands to create a profile scaffold, encrypt a value into a
    ``!vault`` payload, and read an encrypted value back. Output is printed to
    the console only; no files are written.

    ### Notes

    : Encryption and decryption use the vault profile named on the command
        line (or referenced by the config); the key material is taken from
        the environment variable that profile points to.

    """

    class Meta:
        """Meta configuration for the vault controller."""

        label = 'vault'
        stacked_type = 'nested'
        stacked_on = 'base'
        subparser_options = dict(metavar='')
        help = 'manage encrypted configuration secrets'
        description = 'Generate keys and encrypt or decrypt configuration secrets.'
        epilog = f'Example: {basename(sys.argv[0])} vault command --options'

    def _profile(self, name):
        # read a vault profile, turning a missing section/key or a malformed
        # entry into a clean vault error instead of a parser traceback
        try:
            profile = self.app.config.get('vault', name)
        except Exception:
            raise TokeoVaultError(f'unknown vault profile {name!r}')
        if not isinstance(profile, dict) or 'type' not in profile:
            raise TokeoVaultError(f'vault profile {name!r} is missing a "type"')
        return profile

    @ex(
        help='create a profile scaffold with generated key material',
        description='Print a ready-to-paste vault profile and its environment variable(s) for a type.',
        epilog=f'Use "{basename(sys.argv[0])} vault create --type enc example".',
        arguments=[
            (
                ['--type'],
                dict(action='store', required=True, help='vault type to scaffold, e.g. enc or scrypt'),
            ),
            (
                ['name'],
                dict(help='the profile name to create under the vault section'),
            ),
        ],
    )
    def create(self):
        """
        Print a paste-ready profile scaffold for a vault type.

        Dispatches to the type's handler to build the profile fields and the
        environment variable(s) the secret belongs in: an ``enc`` profile gets
        a fresh Fernet key, a ``scrypt`` profile gets a fresh salt plus a
        placeholder passphrase to replace.

        ### Output

        - The ``vault`` profile block, then a blank line, then the
            ``NAME=value`` environment assignment(s)

        ### Raises

        - **TokeoVaultError**: If the type is unknown or cannot be scaffolded

        """
        name = self.app.pargs.name
        scaffold = vault_get_handler(self.app.pargs.type).create(name)
        self.app.print('vault:')
        self.app.print(f'  {name}:')
        for field, value in scaffold['profile'].items():
            self.app.print(f'    {field}: {value}')
        self.app.print('')
        for secret in scaffold['secrets']:
            value = f'"{secret["value"]}"' if secret.get('quote') else secret['value']
            self.app.print(f'{secret["name"]}={value}')

    @ex(
        help='encrypt a value for a profile',
        description='Encrypt a value with a vault profile and print the !vault payload.',
        epilog=f'Use "{basename(sys.argv[0])} vault encrypt --profile master my-secret".',
        arguments=[
            (
                ['--profile'],
                dict(action='store', required=True, help='vault profile to encrypt with'),
            ),
            (
                ['value'],
                dict(help='the plaintext value to encrypt'),
            ),
        ],
    )
    def encrypt(self):
        """
        Encrypt a value with a vault profile and print the !vault payload.

        The value is the positional argument. Only the encrypted payload is
        printed, never the profile's key, passphrase or salt.

        ### Output

        - A ready-to-paste ``!vault:<profile> <payload>`` line

        ### Raises

        - **TokeoVaultError**: If the profile is unknown or its key material
            is missing or invalid

        """
        name = self.app.pargs.profile
        profile = self._profile(name)
        token = vault_get_handler(profile['type']).encrypt(profile, self.app.pargs.value)
        self.app.print(f'!vault:{name} {token}')

    @ex(
        help='decrypt a value or a config reference',
        description='Decrypt a payload given as the argument with a profile, or a config reference.',
        epilog=f'Use "{basename(sys.argv[0])} vault decrypt --config pocketbase.auth_password".',
        arguments=[
            (
                ['value'],
                dict(nargs='?', help='the encrypted payload to decrypt (needs --profile)'),
            ),
            (
                ['--config'],
                dict(action='store', dest='config_ref', help='dotted config reference, e.g. section.key'),
            ),
            (
                ['--profile'],
                dict(action='store', help='vault profile for the payload argument'),
            ),
        ],
    )
    def decrypt(self):
        """
        Decrypt and print a secret, by config reference or by payload.

        With ``--config`` a dotted path (``section.key`` or deeper) is read
        from the configuration and shown decrypted; the profile is taken from
        the value's ``!vault`` tag, so ``--profile`` is not used here.
        Otherwise the positional payload is decrypted with the given
        ``--profile``.

        ### Output

        - The decrypted plaintext is printed to the console

        ### Raises

        - **TokeoVaultError**: If no input is given, ``--config`` is combined
            with ``--profile``, the reference is not found, a profile is
            missing, or the secret cannot be decrypted

        """
        ref = self.app.pargs.config_ref
        if ref:
            # the profile comes from the !vault tag of the referenced value,
            # so a --profile here would be ignored; reject it as a clear error
            if self.app.pargs.profile is not None:
                raise TokeoVaultError('--profile is not used with --config; the profile comes from the referenced config value')
            parts = ref.split('.')
            if len(parts) < 2:
                raise TokeoVaultError('a config reference needs at least section.key')
            try:
                # the vault config handler resolves on read; only wrap the
                # lookup so a missing reference is a clean error, while a real
                # decryption error keeps its own specific message
                value = self.app.config.get(parts[0], parts[1])
                for part in parts[2:]:
                    value = value[part]
            except TokeoVaultError:
                raise
            except Exception:
                raise TokeoVaultError(f'config reference {ref!r} was not found')
            self.app.print(value)
            return
        value = self.app.pargs.value
        if not value:
            raise TokeoVaultError('provide an encrypted payload, or --config <section.key>')
        name = self.app.pargs.profile
        if not name:
            raise TokeoVaultError('a --profile is required to decrypt a payload')
        profile = self._profile(name)
        self.app.print(vault_get_handler(profile['type']).decrypt(profile, value))


def load(app):
    """
    Load the vault extension and register it with the application.

    This function is called by the Cement framework when the extension is
    loaded. It registers the vault yaml tag and the built-in handlers, sets
    the vault config handler as the default, and registers the vault
    command-line controller.

    ### Args

    - **app**: The application instance

    ### Notes

    : The vault config handler subclasses the Tokeo YAML handler, so this
        extension replaces the YAML config handler when both are enabled.

    """
    # register the vault tag before any config is parsed, and the built-in
    # handlers before any secret is resolved
    register_yaml_tag()
    register_handler('enc', EncVault())
    register_handler('scrypt', ScryptVault())
    app.handler.register(TokeoVaultConfigHandler)
    app.handler.register(TokeoVaultController)
    app._meta.config_handler = TokeoVaultConfigHandler.Meta.label
