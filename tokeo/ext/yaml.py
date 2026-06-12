"""
Tokeo YAML extension module.

This extension provides enhanced YAML configuration handling for Tokeo applications.
It extends the standard Cement YamlConfigHandler with additional capabilities for
deep merging of configuration dictionaries, preserving nested structures and
handling various data types appropriately.

The module enables loading and parsing of YAML configuration files with proper
handling of nested dictionaries, allowing for more complex configuration structures
than the standard Cement config handlers support.

"""

import os
import re
import yaml
from configparser import RawConfigParser
from cement.ext.ext_yaml import YamlConfigHandler
from tokeo.core.exc import TokeoError
from tokeo.core.utils.dict import deep_merge


class TokeoYamlConfigError(TokeoError):
    """
    Exception raised for invalid yaml configuration input.

    This exception is raised when a configuration value cannot be handled by
    the yaml config handler, for example when an environment override carries
    a yaml tag, which is not allowed because env overrides are plain values.

    """


def _coerce_env_value(raw, env_var=None):
    # an env override is a plain value, coerced like a yaml scalar so true/42/
    # null get their proper types. a yaml tag (a leading "!") is rejected here:
    # env injects plain values only and never a typed/constructed node such as
    # an encrypted secret; quoting the value forces a plain string, as in yaml
    if raw.lstrip().startswith('!'):
        where = f' {env_var}' if env_var else ''
        raise TokeoYamlConfigError(f'a yaml tag is not allowed in the environment override{where}')
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


class TokeoYamlConfigHandler(YamlConfigHandler):
    """
    Enhanced YAML configuration handler for Tokeo applications.

    This class extends the Cement YamlConfigHandler to provide improved
    configuration merging capabilities, particularly for nested dictionary
    structures. It enables loading and parsing YAML configuration files with
    proper handling of nested configuration sections.

    ### Methods

    - **merge**: Merges a dictionary into the current configuration with support
        for deep merging
    - All methods inherited from YamlConfigHandler, including:

        - **get**: Get a config value
        - **set**: Set a config value
        - **get_sections**: Get list of sections
        - **get_section_dict**: Get a section as a dictionary
        - **add_section**: Add a new section
        - **parse_file**: Parse a YAML config file

    ### Notes

    : The handler preserves nested dictionary structures during merges using
        a deep merge algorithm, which allows for more complex configuration
        structures than the standard Cement config handlers support.

    ### Example

    ```python
    # In your application:
    # The handler is automatically registered by the extension

    # To access configuration:
    value = app.config.get('section', 'key')

    # To override configuration:
    app.config.merge({
        'section1': {
            'key1': 'value1',
            'nested': {
                'subkey': 'subvalue'
            }
        }
    })
    ```

    ### See Also

    - Cement YamlConfigHandler: The parent class in the Cement framework
    - deep_merge: Utility function used to merge nested dictionaries

    """

    class Meta:
        """
        Handler meta-data for configuration and identification.

        ### Notes

        : This class defines metadata required by the Cement framework
            for proper handler registration and operation.

        """

        label = 'tokeo.yaml'
        """The unique string identifier of this handler in the application."""

    def _env_var_for_path(self, path):
        """
        Build the environment variable name overriding a config path.

        Generalises cement's section/key scheme to an arbitrary depth, so a
        nested value can be overridden too. The name is the application's
        config_section followed by the path, joined with underscores,
        uppercased, with every other character replaced by an underscore. A
        leading path element equal to the config_section is not repeated.

        ### Args

        - **path** (list): The config path, e.g. ```['dramatiq', 'rabbitmq',
            'auth_password']```

        ### Returns

        - **str**: The environment variable name for that path

        """
        prefix = self.app._meta.config_section
        # the app's own section is not repeated, matching cement's 2-level rule
        parts = path[1:] if path and path[0] == prefix else path
        name = '_'.join([prefix] + [str(p) for p in parts]).upper()
        return re.sub('[^0-9a-zA-Z_]+', '_', name)

    def _get_env_var(self, section, key):
        # keep cement's section/key entry point, routed through the path form
        return self._env_var_for_path([section, key])

    def _resolve_leaf(self, value, path):
        # hook for subclasses to transform a scalar leaf (for example the
        # vault handler decrypts a VaultRef); the base returns it unchanged
        return value

    def _resolve_value(self, value, path):
        """
        Resolve a config value for reading, applying environment overrides.

        Walks dictionaries and lists so any leaf, at any depth, can be
        overridden by an environment variable. A container is rebuilt only
        when a descendant actually changes; otherwise the original object is
        returned, so a plain read keeps its object identity and behaves as
        before. Each scalar leaf is passed through ```_resolve_leaf```, which
        subclasses may override to transform it further.

        ### Args

        - **value**: The stored config value (scalar, dict, or list)
        - **path** (list|None): The config path to this value, or None inside
            a list where no env name can be formed

        ### Returns

        - The original value when nothing changes, otherwise a rebuilt copy
            with environment overrides applied

        """
        if isinstance(value, dict):
            changed = False
            result = {}
            for k, v in value.items():
                rv = self._resolve_value(v, (path + [k]) if path is not None else None)
                result[k] = rv
                changed = changed or rv is not v
            # keep the stored object when nothing changed, so a plain read is
            # identical to before (same reference, no copy)
            return result if changed else value
        if isinstance(value, list):
            changed = False
            result = []
            for v in value:
                rv = self._resolve_value(v, None)
                result.append(rv)
                changed = changed or rv is not v
            return result if changed else value
        # scalar leaf: an env override wins, else hand it to the leaf hook
        if path is not None:
            env_var = self._env_var_for_path(path)
            if env_var in os.environ:
                return _coerce_env_value(os.environ[env_var], env_var)
        return self._resolve_leaf(value, path)

    def get(self, section, key, **kwargs):
        """
        Get a configuration value, applying environment overrides at any depth.

        Extends cement's section/key override so a nested value can be
        overridden too, by an environment variable whose name follows the full
        path (for example ```<CONFIG_SECTION>_DRAMATIQ_RABBITMQ_AUTH_PASSWORD```).
        Overrides arrive as raw strings and are coerced like a yaml scalar (via
        ```yaml.safe_load```), so ```true```, ```42``` or ```null``` become their proper
        types; values from a file are already typed by the yaml parser.
        Resolution is applied on a rebuilt copy only where a value actually
        changes, so a plain read returns the same object as before.

        ### Args

        - **section** (str): The configuration section
        - **key** (str): The configuration key within the section

        ### Keyword Args

        - **kwargs**: Forwarded to the parser get (for example ```fallback```)

        ### Returns

        - The value, with environment overrides applied

        ### Raises

        - **TokeoYamlConfigError**: If an environment override carries a yaml
            tag (a leading ```!```); env overrides are plain values only

        ### Notes

        - Quoting forces a string, exactly as in yaml: an env value of
            ```"true"``` (with quotes) stays the string ```true``` while ```true```
            becomes a bool; the yaml "Norway" rule also makes
            ```yes```/```no```/```on```/```off``` booleans
        - Subclasses can transform individual leaves through ```_resolve_leaf```
            (the vault handler uses this to decrypt secrets)

        """
        # an env override at section/key level wins even if the key is absent
        # from the config file, so check it before reading the stored value
        env_var = self._get_env_var(section, key)
        if env_var in os.environ:
            return _coerce_env_value(os.environ[env_var], env_var)
        value = RawConfigParser.get(self, section, key, **kwargs)
        return self._resolve_value(value, [section, key])

    def merge(self, dict_obj, override=True):
        """
        Merge a dictionary into the current configuration.

        This method extends the standard Cement merge capability by adding
        deep merging of nested dictionary structures. When merging configuration
        dictionaries, it preserves the nested structure and handles complex
        data types appropriately.

        ### Args

        - **dict_obj** (dict): Dictionary of configuration keys/values to merge
            into the existing configuration

        ### Keyword Args

        - **override** (bool): Whether to override existing values in the
            configuration. If True, existing values are replaced or deep-merged
            if they are dictionaries. If False, only new keys are added.

        ### Raises

        - **AssertionError**: If dict_obj is not None and not a dictionary
        - **ValueError**: If a top-level entry is not a dict (config section)
        - **ValueError**: If deep_merge encounters incompatible types

        ### Example

        ```python
        # Simple merge
        app.config.merge({
            'section1': {
                'key1': 'value1',
                'key2': 'value2'
            }
        })

        # Deep merge with nested dictionaries
        app.config.merge({
            'section1': {
                'subsection': {
                    'key1': 'value1',
                    'key2': 'value2'
                }
            }
        })
        ```

        ### Notes

        : For nested dictionaries, a deep merge is performed, allowing for
            preserving existing nested values while adding or updating specific
            keys. This is particularly useful for complex configuration structures.

        : Merged values are stored by reference and nested dicts are deep
            merged in place, so a later merge of the same key can mutate a
            dict the caller passed earlier (including a shared class-level
            config_defaults). This is intended: the configuration is mutable
            by design. A caller that needs its source kept isolated should
            pass a copy, e.g. app.config.merge(copy.deepcopy(my_config))

        """
        # Skip processing if dict_obj is None
        if dict_obj is None:
            return

        # Validate input type
        assert isinstance(dict_obj, dict), 'Dictionary object required.'

        # Process each top-level section in the input dictionary
        for section in list(dict_obj.keys()):
            # special debug value in foundation gets ignored
            if section == 'debug':
                continue
            # get the content for section
            section_content = dict_obj.get(section, None)
            # every top-level entry must be a config section (a dict); a
            # non-dict here means malformed config, so fail loud instead
            # of silently dropping it
            if section_content is not None and not isinstance(section_content, dict):
                raise ValueError(f'config section "{section}" must be a dict, got "{type(dict_obj[section]).__name__}"')

            # Create section if it doesn't exist
            if section not in self.get_sections():
                self.add_section(section)

            # skip None section
            if section_content is None:
                continue

            # Process each key in the section
            for key in list(dict_obj[section].keys()):
                if override:
                    b = dict_obj[section][key]
                    if key in self.keys(section) and isinstance(b, dict):
                        # read the stored value raw, so merging combines stored
                        # structures and is not affected by a subclass get that
                        # resolves or transforms values on read
                        a = RawConfigParser.get(self, section, key)
                        if isinstance(a, dict):
                            b = deep_merge(a, b)
                    self.set(section, key, b)
                else:
                    # When not overriding, only set if key doesn't exist
                    if key not in self.keys(section):
                        self.set(section, key, dict_obj[section][key])

            # Note: We don't fully support arbitrary nesting configuration
            # blocks beyond the section.key level, so we don't process any
            # deeper nesting here. However, our deep_merge functionality
            # does handle nested dictionaries stored as values.


def load(app):
    """
    Load the TokeoYamlConfigHandler and register it with the application.

    This function is called by the Cement framework when the extension
    is loaded. It registers the YAML config handler and sets it as the
    default configuration handler for the application.

    ### Args

    - **app**: The application instance

    ### Example

    ```python
    # In your application configuration:
    class MyApp(App):
        class Meta:
            extensions = [
                'tokeo.ext.yaml',
                # other extensions...
            ]

            # Set the configuration file suffix
            config_file_suffix = '.yaml'

            # The config handler will be automatically set by the extension
    ```

    ### Notes

    : This function performs two actions:

        1. Registers the TokeoYamlConfigHandler with the application
        1. Sets it as the default configuration handler

    : After loading this extension, all configuration operations will use
        this handler for loading, parsing and merging configuration data.

    """
    app.handler.register(TokeoYamlConfigHandler)
    app._meta.config_handler = TokeoYamlConfigHandler.Meta.label
