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

from cement.ext.ext_yaml import YamlConfigHandler
from tokeo.core.utils.dict import deep_merge


class TokeoYamlConfigHandler(YamlConfigHandler):
    """
    Enhanced YAML configuration handler for Tokeo applications.

    This class extends the Cement YamlConfigHandler to provide improved
    configuration merging capabilities, particularly for nested dictionary
    structures. It enables loading and parsing YAML configuration files with
    proper handling of nested configuration sections.

    ### Methods:

    - **merge**: Merges a dictionary into the current configuration with support for deep merging
    - All methods inherited from YamlConfigHandler, including:

        - **get**: Get a config value
        - **set**: Set a config value
        - **get_sections**: Get list of sections
        - **get_section_dict**: Get a section as a dictionary
        - **add_section**: Add a new section
        - **parse_file**: Parse a YAML config file

    ### Notes:

    : The handler preserves nested dictionary structures during merges using
      a deep merge algorithm, which allows for more complex configuration
      structures than the standard Cement config handlers support.

    ### Example:

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

    ### See Also:

    - Cement YamlConfigHandler: The parent class in the Cement framework
    - deep_merge: Utility function used to merge nested dictionaries

    """

    class Meta:
        """
        Handler meta-data for configuration and identification.

        ### Notes:

        : This class defines metadata required by the Cement framework
          for proper handler registration and operation.

        """

        label = 'tokeo.yaml'
        """The unique string identifier of this handler in the application."""

    def merge(self, dict_obj, override=True):
        """
        Merge a dictionary into the current configuration.

        This method extends the standard Cement merge capability by adding
        deep merging of nested dictionary structures. When merging configuration
        dictionaries, it preserves the nested structure and handles complex
        data types appropriately.

        ### Args:

        - **dict_obj** (dict): Dictionary of configuration keys/values to merge
          into the existing configuration

        ### Keyword Args:

        - **override** (bool): Whether to override existing values in the
          configuration. If True, existing values are replaced or deep-merged
          if they are dictionaries. If False, only new keys are added.

        ### Raises:

        - **AssertionError**: If dict_obj is not None and not a dictionary
        - **ValueError**: If deep_merge encounters incompatible types

        ### Example:

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

        ### Notes:

        : For nested dictionaries, a deep merge is performed, allowing for
          preserving existing nested values while adding or updating specific
          keys. This is particularly useful for complex configuration structures.

        """
        # Skip processing if dict_obj is None
        if dict_obj is None:
            return

        # Validate input type
        assert isinstance(dict_obj, dict), 'Dictionary object required.'

        # Process each top-level section in the input dictionary
        for section in list(dict_obj.keys()):
            # We only process dictionary sections (configuration sections)
            if type(dict_obj[section]) is dict:
                # Create section if it doesn't exist
                if section not in self.get_sections():
                    self.add_section(section)

                # Process each key in the section
                for key in list(dict_obj[section].keys()):
                    if override:
                        b = dict_obj[section][key]
                        if key in self.keys(section) and isinstance(b, dict):
                            a = self.get(section, key)
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

    ### Args:

    - **app**: The application instance

    ### Example:

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

    ### Notes:

    : This function performs two actions:

        1. Registers the TokeoYamlConfigHandler with the application
        1. Sets it as the default configuration handler

    : After loading this extension, all configuration operations will use
      this handler for loading, parsing and merging configuration data.

    """
    app.handler.register(TokeoYamlConfigHandler)
    app._meta.config_handler = TokeoYamlConfigHandler.Meta.label
