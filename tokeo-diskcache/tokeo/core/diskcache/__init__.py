"""
Tokeo DiskCache Library.

The disk and file backed cache of DiskCache 5.6.3 by Grant Jenks
(github.com/grantjenks/python-diskcache), without pickle: keys and values that
are no raw types are stored as JSON, pickled data is never loaded, and new
directories are only accessible by their owner. Only ```Cache```, ```Disk```
and the ```Lock``` recipe are taken.
"""

from .core import (
    DEFAULT_SETTINGS,
    ENOVAL,
    EVICTION_POLICY,
    UNKNOWN,
    Cache,
    Disk,
    EmptyDirWarning,
    Timeout,
    UnknownFileWarning,
)
from .recipes import Lock

__all__ = [
    'Cache',
    'DEFAULT_SETTINGS',
    'Disk',
    'ENOVAL',
    'EVICTION_POLICY',
    'EmptyDirWarning',
    'Lock',
    'Timeout',
    'UNKNOWN',
    'UnknownFileWarning',
]
