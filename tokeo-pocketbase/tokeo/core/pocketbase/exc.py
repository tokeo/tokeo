"""
The errors of the driver: every error of the server or of the connection
raises ```TokeoPocketBaseError```, a refused login its subclass
```TokeoPocketBaseAuthError```.

"""

from tokeo.core.exc import TokeoError


class TokeoPocketBaseError(TokeoError):
    """
    Raised for an error answer of PocketBase or a failed request, by the
    extension also for a config that cannot be used.

    ### Args

    - **message** (str): What failed
    - **status** (int): The http status, 0 when no answer came
    - **url** (str): The url of the request
    - **data** (dict): The data of the error answer, e.g. the invalid fields

    """

    def __init__(self, message, status=0, url=None, data=None):
        super().__init__(message)
        #: The http status, 0 when no answer came
        self.status = status
        #: The url of the request
        self.url = url
        #: The data of the error answer, e.g. the invalid fields
        self.data = data if isinstance(data, dict) else {}


class TokeoPocketBaseAuthError(TokeoPocketBaseError):
    """Raised when an authentication cannot be done."""
