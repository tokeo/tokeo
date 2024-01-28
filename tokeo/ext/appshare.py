"""
appshare Module
===============

This module provides a global access point to the running Cement app object. It allows external modules to interact with the Cement app without explicitly passing the app object around.

Classes
-------
App
    A proxy class that provides access to the attributes and methods of the shared app object.

Functions
---------
load(app_to_share)
    Sets the Cement app object to be shared globally.

"""

class App():
    """
    A proxy class to access the shared app object.

    This class acts as a stand-in for the actual Cement app object. It allows external modules to access the app's attributes and methods as if they were directly accessing the app object itself.

    Attributes
    ----------
    _app : object
        The actual Cement app object.

    Methods
    -------
    __getattr__(key)
        Returns the attribute of the app object corresponding to the key.

    """

    def __init__(self):
        """
        Initializes the App class with a None value for the _app attribute.
        """
        self._app = None

    def __getattr__(self, key):
        """
        Provides dynamic access to the attributes of the shared app object.

        Parameters
        ----------
        key : str
            The attribute name to access from the app object.

        Returns
        -------
        The attribute of the app object if it exists.

        Raises
        ------
        AttributeError
            If the app object is not set or the attribute does not exist.

        """
        # test _app object
        if self._app is None:
            raise AttributeError(f'\'App\' object has no attribute \'{key}\'')
        # return attribute
        return getattr(self._app, key)

app = App()

def load(app_to_share):
    """
    Sets the shared app object.

    This function is used to set the actual Cement app object that will be accessed through the App proxy class.

    Parameters
    ----------
    app_to_share : object
        The Cement app object to be shared.

    """
    app._app = app_to_share
