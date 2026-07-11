"""
Tokeo SMTPD Logger Module.

The log severities handed to ```on_logging_event``` -- a translation of
standard ```Logger::DEBUG/INFO/WARN/ERROR/FATAL``` levels. The enum
values are Python's ```logging``` level integers, so a handler can pass
```severity.value``` straight to ```logging```/```app.log``` and levels compare
numerically, while the members themselves still compare by identity.

### Notes

: This mirrors both Ruby's ```Logger``` (ordered integer levels) and Python's
    ```logging```; ```WARN```/```FATAL``` map onto ```WARNING```/```CRITICAL```.

"""

import logging
from enum import Enum


class Severity(Enum):
    """Log severity levels handed to on_logging_event (Python logging levels)."""

    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARN = logging.WARNING
    ERROR = logging.ERROR
    FATAL = logging.CRITICAL


class ForwardingLogger:
    """
    A logger that forwards every call to ```on_logging_event```.

    The ```ForwardingLogger```: ```info```/```warn```/
    ```error```/```fatal```/```debug``` push the message to the given
    ```on_logging_event``` callable with the matching ```Severity``` and a nil
    context.

    ### Args

    - **on_logging_event** (callable): ```on_logging_event(ctx, severity, msg)```

    """

    def __init__(self, on_logging_event):
        self._on_logging_event = on_logging_event

    def info(self, msg):
        """Log msg at INFO severity."""
        self._on_logging_event(None, Severity.INFO, msg)

    def warn(self, msg):
        """Log msg at WARN severity."""
        self._on_logging_event(None, Severity.WARN, msg)

    def error(self, msg):
        """Log msg at ERROR severity."""
        self._on_logging_event(None, Severity.ERROR, msg)

    def fatal(self, msg):
        """Log msg at FATAL severity."""
        self._on_logging_event(None, Severity.FATAL, msg)

    def debug(self, msg):
        """Log msg at DEBUG severity."""
        self._on_logging_event(None, Severity.DEBUG, msg)
