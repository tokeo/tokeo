"""
Provides a global access point to the running Cement app object.

This module allows external modules to interact with the Cement app without
explicitly passing the app object around. It implements a proxy pattern to
make the app accessible through a singleton.

Example:
```python
# Use the imported app object
from tokeo.ext.appshare import app

if app.dramatiq:
    pass
```

"""


class App:
    """
    A proxy class to access the shared app object.

    This class acts as a stand-in for the actual Cement app object, allowing
    external modules to access the app's attributes and methods as if they were
    directly accessing the app object itself.

    ### Attributes:

    - **_app** (Application): The actual Cement app object

    """

    def __init__(self):
        """
        Initialize the App class with an unset app reference.
        """
        self._app = None

    def __getattr__(self, key):
        """
        Provide dynamic access to the attributes of the shared app object.

        This magic method enables transparent access to all properties and methods
        of the underlying Cement application instance.

        ### Args:

        - **key** (str): The attribute name to access from the app object

        ### Returns:

        - **any**: The attribute of the app object if it exists

        ### Raises:

        - **AttributeError**: If the app object is not set or the attribute
          does not exist

        """
        # test _app object
        if self._app is None:
            raise AttributeError(f"'App' object has no attribute '{key}'")
        # return attribute
        return getattr(self._app, key)


app = App()


def load(app_to_share):
    """
    Set the global shared app object.

    This function is called during application initialization to store
    the Cement app object that will be accessible through the global `app`
    instance.

    ### Args:

    - **app_to_share** (Application): The Cement app object to be shared globally

    ### Notes:

    : This function is called automatically by the Cement framework when
      the appshare extension is loaded, making the app globally accessible.

    """
    app._app = app_to_share
