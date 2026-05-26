"""
Provides a global access point to the running Cement app object.

This module allows external modules to interact with the Cement app without
explicitly passing the app object around. It implements a proxy pattern to
make the app accessible through a singleton.

### Example:

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

    This class acts as a stand-in for the actual Cement app object. Regular
    attribute and method access (app.foo, app.foo()) is forwarded to the
    underlying app object.

    ### Attributes:

    - **_app** (Application): The actual Cement app object

    ### Notes:

    : Only regular attribute and method access is proxied. Item access
        (app[...]) and implicitly invoked dunder methods (e.g. __enter__,
        __exit__, __call__) are not forwarded, since python resolves those
        on the type rather than through __getattr__. The proxy targets an
        already running app, so its lifecycle is deliberately out of scope.

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
        # guard against recursion if _app was never set on the instance,
        # e.g. an App built without __init__ (copy/pickle): accessing
        # self._app would otherwise re-enter __getattr__ endlessly
        if key == '_app':
            raise AttributeError(key)
        # the shared app is only set once load() ran; surface that as the
        # real reason instead of a misleading "no attribute" error
        if self._app is None:
            raise AttributeError(f"shared app not set yet; call appshare.load() first (accessing '{key}')")
        # delegate to the shared app
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
