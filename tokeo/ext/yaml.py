from cement.ext.ext_yaml import YamlConfigHandler
from tokeo.core.utils.dict import deep_merge


class TokeoYamlConfigHandler(YamlConfigHandler):
    """
    This class is an implementation of the :ref:`Config <cement.core.config>`
    interface.  It handles configuration file parsing and the like by
    sub-classing from the standard `ConfigParser
    <http://docs.python.org/library/configparser.html>`_
    library.  Please see the ConfigParser documentation for full usage of the
    class.

    Additional arguments and keyword arguments are passed directly to
    RawConfigParser on initialization.
    """

    class Meta:
        """Handler meta-data."""

        label = 'tokeo.yaml'
        """The string identifier of this handler."""

    def merge(self, dict_obj, override=True):
        """
        Merge a dictionary into our config.  If override is True then
        existing config values are overridden by those passed in.

        Args:
            dict_obj (dict): A dictionary of configuration keys/values to merge
                into our existing config (self).

        Keyword Args:
            override (bool):  Whether or not to override existing values in the
                config.

        """
        assert isinstance(dict_obj, dict), 'Dictionary object required.'

        for section in list(dict_obj.keys()):
            if type(dict_obj[section]) is dict:
                if section not in self.get_sections():
                    self.add_section(section)

                for key in list(dict_obj[section].keys()):
                    if override:
                        b = dict_obj[section][key]
                        if key in self.keys(section) and isinstance(b, dict):
                            a = self.get(section, key)
                            if isinstance(a, dict):
                                b = deep_merge(a, b)
                        self.set(section, key, b)
                    else:
                        # only set it if the key doesn't exist
                        if key not in self.keys(section):
                            self.set(section, key, dict_obj[section][key])

                # we don't support nested config blocks, so no need to go
                # further down to more nested dicts.


def load(app):
    app.handler.register(TokeoYamlConfigHandler)
    app._meta.config_handler = TokeoYamlConfigHandler.Meta.label
