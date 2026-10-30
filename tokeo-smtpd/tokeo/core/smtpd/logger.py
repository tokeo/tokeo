"""
Tokeo SMTPD Logger Module.

The log severities handed to ```on_logging_event```, the levels of
Python's ```logging``` (```DEBUG/INFO/WARNING/ERROR/CRITICAL```). The enum
values are Python's ```logging``` level integers, so a handler can pass
```severity.value``` straight to ```logging```/```app.log``` and levels compare
numerically, while the members themselves still compare by identity.

### Notes

: The member names are the level names of ```logging```, so
    ```severity.name.lower()``` names the matching log method, e.g.
    ```getattr(app.log, severity.name.lower())```.

"""

import logging
from enum import Enum


class Severity(Enum):
    """Log severity levels handed to on_logging_event (Python logging levels)."""

    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class ForwardingLogger:
    """
    A logger that forwards every call to ```on_logging_event```.

    The ```ForwardingLogger```: ```debug```/```info```/```warning```/
    ```error```/```critical``` push the message to the given
    ```on_logging_event``` callable with the matching ```Severity``` and the
    given context, nil when not given.

    ### Args

    - **on_logging_event** (callable): ```on_logging_event(ctx, severity, msg)```

    """

    def __init__(self, on_logging_event):
        self._on_logging_event = on_logging_event

    def info(self, msg, ctx=None):
        """Log msg at INFO severity, optionally for a ctx."""
        self._on_logging_event(ctx, Severity.INFO, msg)

    def warning(self, msg, ctx=None):
        """Log msg at WARNING severity, optionally for a ctx."""
        self._on_logging_event(ctx, Severity.WARNING, msg)

    def error(self, msg, ctx=None):
        """Log msg at ERROR severity, optionally for a ctx."""
        self._on_logging_event(ctx, Severity.ERROR, msg)

    def critical(self, msg, ctx=None):
        """Log msg at CRITICAL severity, optionally for a ctx."""
        self._on_logging_event(ctx, Severity.CRITICAL, msg)

    def debug(self, msg, ctx=None):
        """Log msg at DEBUG severity, optionally for a ctx."""
        self._on_logging_event(ctx, Severity.DEBUG, msg)
